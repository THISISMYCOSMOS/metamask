"""The controlled runtime path: simulate, decide, and only then broadcast.

Ordering is the whole point of this module.

1. Resolve the exact transaction fields and snapshot the node.
2. Send that exact transaction *inside* the snapshot, confirm it succeeded,
   revert, and prove balances and nonce were restored.
3. Build a candidate from that evidence and evaluate it deterministically.
4. Only if the decision is accepted, re-read the live chain context and hand the
   same transaction to the external sender exactly once.

The simulator never touches the external sender.  On a rejection the sender is
called zero times, which is the property the tests assert directly by counting
calls rather than by inspecting logs.

Scope limit: see :mod:`core.gate`.  The exactly-once property is process-local
and this whole path is a bypassable companion guard, not wallet-native
enforcement.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .candidate_binding import candidate_from_evidence
from .evaluator import evaluate
from .gate import ExecutionGate, GateRejected
from .models import (
    ApprovedPolicyEnvelope,
    ChainContext,
    Erc20TransferTransaction,
    ExecutionCandidate,
    FinalDecision,
    HistoryEntry,
)
from .rpc_simulator import (
    ControlledErc20Simulator,
    ERC20TransferRequest,
    JsonRpcClient,
    RpcSimulationError,
    SimulationEvidence,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    parse_rpc_quantity,
)


class ExecutionServiceError(RuntimeError):
    """The controlled path could not reach a decision safely."""


@dataclass(frozen=True)
class ExecutionOutcome:
    """What happened, including the explicit fact that nothing was sent."""

    evidence: SimulationEvidence
    candidate: ExecutionCandidate
    decision: FinalDecision
    sent: bool
    send_result: Any = None
    rejection: str | None = None


Sender = Callable[[Erc20TransferTransaction], Any]


def live_context_reader(rpc: JsonRpcClient, wallet: str, token: str) -> Callable[[], ChainContext]:
    """Re-read the chain context the gate compares against just before sending."""

    def read() -> ChainContext:
        block = rpc.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict):
            raise RpcSimulationError("eth_getBlockByNumber did not return a block object")
        block_hash = block.get("hash")
        if not isinstance(block_hash, str):
            raise RpcSimulationError("block.hash is missing or malformed")
        balance = decode_erc20_uint256(rpc.call("eth_call", [{"to": token, "data": encode_erc20_balance_of(wallet)}, "latest"]))
        nonce = parse_rpc_quantity(rpc.call("eth_getTransactionCount", [wallet, "latest"]), field="sender nonce")
        chain_id = parse_rpc_quantity(rpc.call("eth_chainId"), field="eth_chainId")
        return ChainContext(
            schemaVersion=1,
            kind="chain-context",
            chainId=chain_id,
            blockNumber=str(parse_rpc_quantity(block.get("number"), field="block.number")),
            blockHash=block_hash.lower(),
            walletAddress=wallet.lower(),
            senderNonce=str(nonce),
            tokenAddress=token.lower(),
            assetBalance=str(balance),
        )

    return read


def anvil_sender(rpc: JsonRpcClient) -> Sender:
    """External send for the local demo: one real ``eth_sendTransaction``.

    This is the only place a transaction leaves the process for real, and the
    gate is the only caller of it.
    """

    def send(transaction: Erc20TransferTransaction) -> str:
        if not rpc.is_local_controlled_endpoint:
            raise RpcSimulationError("refusing to broadcast to a non-loopback endpoint")
        payload = {
            "from": transaction.fromAddress,
            "to": transaction.toAddress,
            "value": "0x0",
            "data": transaction.data,
            "nonce": hex(int(transaction.nonce)),
            "gas": hex(int(transaction.gasLimit)),
        }
        transaction_hash = rpc.call("eth_sendTransaction", [payload])
        if not isinstance(transaction_hash, str) or not transaction_hash.startswith("0x"):
            raise RpcSimulationError("external send did not return a transaction hash")
        return transaction_hash

    return send


class Erc20ExecutionService:
    """Wire simulation, deterministic decision and a single external send together."""

    def __init__(
        self,
        simulator: ControlledErc20Simulator,
        *,
        read_context: Callable[[], ChainContext],
        send: Sender,
        gate: ExecutionGate | None = None,
    ) -> None:
        self._simulator = simulator
        self._read_context = read_context
        self._send = send
        self._gate = gate or ExecutionGate()

    def run(
        self,
        approval: ApprovedPolicyEnvelope,
        request: ERC20TransferRequest,
        *,
        candidate_id: str,
        history_entries: Sequence[HistoryEntry] = (),
    ) -> ExecutionOutcome:
        """Simulate, decide, and send only on acceptance.

        The callback passed to the simulator *decides*; it does not broadcast.
        Broadcasting happens here, after the snapshot has been reverted and the
        decision is known to be accepted.
        """
        built: dict[str, Any] = {}

        def decide(evidence: SimulationEvidence) -> bool:
            candidate = candidate_from_evidence(
                approval, evidence, candidate_id=candidate_id, history_entries=history_entries
            )
            decision = evaluate(approval, candidate)
            built["candidate"] = candidate
            built["decision"] = decision
            return decision.accepted

        result = self._simulator.simulate_transfer(request, decide)
        candidate = built.get("candidate")
        decision = built.get("decision")
        if candidate is None or decision is None:
            raise ExecutionServiceError("the simulation did not produce a decision; refusing to send")

        if not result.gate_accepted:
            return ExecutionOutcome(
                evidence=result.evidence,
                candidate=candidate,
                decision=decision,
                sent=False,
                rejection=",".join(decision.reasonCodes) or "DECISION_NOT_ACCEPTED",
            )

        try:
            decision, send_result = self._gate.execute(
                approval, candidate, read_context=self._read_context, send=self._send
            )
        except GateRejected as exc:
            return ExecutionOutcome(
                evidence=result.evidence, candidate=candidate, decision=decision, sent=False, rejection=str(exc)
            )
        return ExecutionOutcome(
            evidence=result.evidence, candidate=candidate, decision=decision, sent=True, send_result=send_result
        )
