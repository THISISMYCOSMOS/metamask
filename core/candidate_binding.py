"""Bind proven Anvil simulation evidence to a Core execution candidate.

``ControlledErc20Simulator`` produces :class:`SimulationEvidence` only after the
transfer succeeded on a loopback Anvil node *and* ``evm_revert`` restored the
state it touched.  This module converts that evidence into the
:class:`ExecutionCandidate` the evaluator judges.

Every value is re-derived rather than trusted: the calldata is recomputed from
the request, the gas limit and nonce are taken from the fields that were
actually submitted, and inconsistent evidence produces no candidate at all.

It deliberately does **not** compare the evidence against the approved policy.
Chain, wallet, token, calldata and floor mismatches belong in the evaluator's
``reasonCodes``, which is the deterministic decision record a reviewer reads.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .canonical import canonical_sha256
from .models import (
    ApprovedPolicyEnvelope,
    ChainContext,
    Erc20TransferTransaction,
    ExecutionCandidate,
    HistoryEntry,
    HistoryLedger,
    Simulation,
)
from .rpc_simulator import SimulationEvidence, encode_erc20_transfer


TRANSACTION_KEYS = frozenset({"from", "to", "value", "data", "nonce", "gas"})


class CandidateBindingError(ValueError):
    """Simulation evidence cannot become a candidate without unproven claims."""


def verify_evidence(evidence: SimulationEvidence) -> None:
    """Fail closed on any evidence that does not prove a restored, exact transfer."""
    if not isinstance(evidence, SimulationEvidence):
        raise CandidateBindingError("only SimulationEvidence from the controlled simulator can be bound")
    if evidence.reverted is not True:
        raise CandidateBindingError("simulation state restoration was never confirmed")
    request = evidence.request
    if request.sender == request.recipient:
        raise CandidateBindingError("self-transfer evidence cannot support a balance-floor decision")

    transaction = evidence.transaction
    if not isinstance(transaction, Mapping) or set(transaction) != set(TRANSACTION_KEYS):
        raise CandidateBindingError(
            "simulated transaction must contain exactly: data, from, gas, nonce, to, value"
        )
    if transaction["from"] != request.sender or transaction["to"] != request.token:
        raise CandidateBindingError("simulated transaction sender or token does not match the request")
    if transaction["value"] != "0x0":
        raise CandidateBindingError("simulated transaction must carry zero native value")
    if transaction["data"] != encode_erc20_transfer(request.recipient, request.amount):
        raise CandidateBindingError("simulated calldata does not re-derive from the request")
    if transaction["nonce"] != hex(evidence.context.sender_nonce):
        raise CandidateBindingError("simulated transaction nonce does not match the captured sender nonce")
    if transaction["gas"] != hex(evidence.gas_limit):
        raise CandidateBindingError("simulated transaction gas does not match the recorded gas limit")

    if evidence.gas_used <= 0 or evidence.gas_used > evidence.gas_limit:
        raise CandidateBindingError("recorded gasUsed is not within the submitted gas limit")
    if evidence.sender_balance_before - evidence.sender_balance_after != request.amount:
        raise CandidateBindingError("sender balance delta does not match the simulated amount")
    if evidence.recipient_balance_after - evidence.recipient_balance_before != request.amount:
        raise CandidateBindingError("recipient balance delta does not match the simulated amount")


def chain_context_from_evidence(evidence: SimulationEvidence) -> ChainContext:
    """Capture the exact chain state the simulation observed before sending."""
    verify_evidence(evidence)
    return ChainContext(
        schemaVersion=1,
        kind="chain-context",
        chainId=evidence.context.chain_id,
        blockNumber=str(evidence.context.block_number),
        blockHash=evidence.context.block_hash,
        walletAddress=evidence.request.sender,
        senderNonce=str(evidence.context.sender_nonce),
        tokenAddress=evidence.request.token,
        assetBalance=str(evidence.sender_balance_before),
    )


def simulation_from_evidence(evidence: SimulationEvidence) -> Simulation:
    """Record the full transfer identity, not just the predicted balance."""
    verify_evidence(evidence)
    return Simulation(
        schemaVersion=1,
        kind="erc20-transfer-simulation",
        status="success",
        chainId=evidence.context.chain_id,
        tokenAddress=evidence.request.token,
        senderAddress=evidence.request.sender,
        recipientAddress=evidence.request.recipient,
        transferAmount=str(evidence.request.amount),
        senderNonce=str(evidence.context.sender_nonce),
        gasLimit=str(evidence.gas_limit),
        gasUsed=str(evidence.gas_used),
        beforeAssetBalance=str(evidence.sender_balance_before),
        afterAssetBalance=str(evidence.sender_balance_after),
        beforeRecipientBalance=str(evidence.recipient_balance_before),
        afterRecipientBalance=str(evidence.recipient_balance_after),
    )


def transaction_from_evidence(evidence: SimulationEvidence) -> Erc20TransferTransaction:
    """Rebuild exactly the transaction that was simulated, field for field."""
    verify_evidence(evidence)
    return Erc20TransferTransaction(
        schemaVersion=1,
        kind="evm-transaction",
        chainId=evidence.context.chain_id,
        fromAddress=evidence.request.sender,
        toAddress=evidence.request.token,
        nonce=str(evidence.context.sender_nonce),
        gasLimit=str(evidence.gas_limit),
        value="0",
        data=evidence.transaction["data"],
    )


def candidate_from_evidence(
    approval: ApprovedPolicyEnvelope,
    evidence: SimulationEvidence,
    *,
    candidate_id: str,
    history_entries: Sequence[HistoryEntry] = (),
) -> ExecutionCandidate:
    """Build the candidate for ``evidence`` under ``approval``.

    The gas limit is no longer caller input: it is the limit the simulation
    actually submitted, so a real send under this candidate cannot run out of
    gas at a lower limit than the one that was proven to succeed.
    """
    context = chain_context_from_evidence(evidence)
    ledger = HistoryLedger(
        schemaVersion=1,
        kind="history-ledger",
        context=context,
        entries=list(history_entries),
    )
    return ExecutionCandidate(
        schemaVersion=1,
        kind="erc20-transfer-candidate",
        candidateId=candidate_id,
        approvalSha256=canonical_sha256(approval),
        policySha256=approval.policySha256,
        context=context,
        history=ledger,
        historySha256=canonical_sha256(ledger),
        transaction=transaction_from_evidence(evidence),
        simulation=simulation_from_evidence(evidence),
    )
