"""End-to-end boundary tests: compile -> approve -> simulate -> decide -> send.

These are the only tests in ``core`` that import ``backend``.  The runtime
modules stay backend-independent; the import here exists to prove the whole
path uses **one** proposal model, **one** canonical hash and **one** user
approval, with no adapter or second artifact in between.
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from backend.gemini_compiler import DEFAULT_GEMINI_MODEL, GeminiConfig, GeminiPolicyCompiler
from backend.policy_service import PolicyProposalService

from .canonical import canonical_sha256
from .candidate_binding import CandidateBindingError, candidate_from_evidence
from .evaluator import evaluate
from .execution_service import Erc20ExecutionService, live_context_reader
from .gate import ExecutionGate
from .models import ApprovedPolicyEnvelope, ChainContext, CompileRequest
from .policy_binding import approval_confirmation
from .rpc_simulator import ERC20TransferRequest, JsonRpcClient
from .test_rpc_simulator import (
    AMOUNT,
    ESTIMATED_GAS,
    GAS_USED,
    NONCE,
    RECIPIENT,
    SENDER,
    SENDER_AFTER,
    SENDER_BEFORE,
    TOKEN,
    ScriptedNode,
    request as transfer_request,
    simulator,
)


MODEL = DEFAULT_GEMINI_MODEL
CHAIN_ID = 31337


def compile_request(**overrides: Any) -> CompileRequest:
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "policy-compile-request",
        "requestId": "request-1",
        "proposalId": "proposal-1",
        "policyId": "test-floor",
        "intentText": "테스트 토큰을 90 base-unit 이상 남겨줘",
        "chainId": CHAIN_ID,
        "walletAddress": SENDER,
        "tokenAddress": TOKEN,
        "tokenSymbol": "TST",
        "tokenDecimals": 0,
    }
    data.update(overrides)
    return CompileRequest.model_validate(data)


def model_output(floor: int = SENDER_AFTER, **overrides: Any) -> dict[str, Any]:
    output: dict[str, Any] = {
        "supported": True,
        "minimumBalanceBaseUnits": str(floor),
        "rationales": ["잔고 하한을 base-unit으로 고정한다."],
        "assumptions": ["금액은 base-unit으로 해석했다."],
        "unsupportedItems": [],
    }
    output.update(overrides)
    return output


def service(output: dict[str, Any] | None = None) -> PolicyProposalService:
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(model_output() if output is None else output)}],
                },
            }
        ],
        "modelVersion": "gemini-3.5-flash-lite",
    }
    return PolicyProposalService(GeminiPolicyCompiler(GeminiConfig("test-key", MODEL), lambda u, h, b: response))


def approved(request: CompileRequest, output: dict[str, Any] | None = None) -> ApprovedPolicyEnvelope:
    policy_service = service(output)
    proposal = policy_service.compile(request).approvable_proposal
    return policy_service.approve(
        proposal,
        approval_id="approval-1",
        approved_by="wallet-owner",
        confirmation=policy_service.confirmation_sentence(proposal),
        request=request,
    )


class OneProposalOneApprovalTests(unittest.TestCase):
    def test_the_hash_the_user_approves_is_the_hash_core_enforces(self) -> None:
        request = compile_request()
        result = service().compile(request)
        proposal = result.approvable_proposal
        envelope = approved(request)

        self.assertEqual(result.proposalSha256, canonical_sha256(proposal))
        self.assertEqual(approval_confirmation(proposal), f"APPROVE {result.proposalSha256}")
        self.assertEqual(envelope.proposalSha256, result.proposalSha256)
        self.assertIs(type(envelope.proposal), type(proposal))
        self.assertEqual(envelope.proposal, proposal)

    def test_korean_text_survives_the_shared_canonicalization(self) -> None:
        request = compile_request()
        proposal = service().compile(request).approvable_proposal
        self.assertEqual(proposal.intentText, request.intentText)
        self.assertIn("테스트", proposal.intentText)
        self.assertEqual(canonical_sha256(proposal), canonical_sha256(proposal.model_dump(mode="json")))

    def test_unsupported_intent_never_reaches_an_approvable_proposal(self) -> None:
        result = service(model_output(unsupportedItems=["하루 500 지출 한도"])).compile(compile_request())
        self.assertFalse(result.supported)
        self.assertIsNone(result.proposalSha256)
        with self.assertRaisesRegex(ValueError, "not approvable"):
            result.approvable_proposal


class CandidateBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = compile_request()
        self.approval = approved(self.request)
        self.evidence = simulator(ScriptedNode()).simulate_transfer(transfer_request(), lambda _: True).evidence

    def candidate(self, evidence: Any = None) -> Any:
        return candidate_from_evidence(self.approval, evidence or self.evidence, candidate_id="candidate-1")

    def test_every_simulated_field_is_bound_into_the_candidate(self) -> None:
        candidate = self.candidate()
        self.assertEqual(candidate.context.blockNumber, "16")
        self.assertEqual(candidate.context.senderNonce, str(NONCE))
        self.assertEqual(candidate.context.assetBalance, str(SENDER_BEFORE))
        self.assertEqual(candidate.transaction.nonce, str(NONCE))
        self.assertEqual(candidate.transaction.gasLimit, str(ESTIMATED_GAS))
        self.assertEqual(candidate.simulation.gasUsed, str(GAS_USED))
        self.assertEqual(candidate.simulation.recipientAddress, RECIPIENT)
        self.assertEqual(candidate.simulation.transferAmount, str(AMOUNT))
        self.assertEqual(candidate.simulation.senderNonce, candidate.transaction.nonce)
        self.assertEqual(candidate.simulation.gasLimit, candidate.transaction.gasLimit)
        self.assertEqual(candidate.approvalSha256, canonical_sha256(self.approval))
        self.assertEqual(candidate.historySha256, canonical_sha256(candidate.history))

    def test_unrestored_or_tampered_evidence_produces_no_candidate(self) -> None:
        from dataclasses import replace

        from .rpc_simulator import encode_erc20_transfer

        with self.assertRaisesRegex(CandidateBindingError, "restoration was never confirmed"):
            self.candidate(replace(self.evidence, reverted=False))

        forged = dict(self.evidence.transaction)
        forged["data"] = encode_erc20_transfer(RECIPIENT, AMOUNT + 1)
        with self.assertRaisesRegex(CandidateBindingError, "does not re-derive"):
            self.candidate(replace(self.evidence, transaction=forged))

        regassed = dict(self.evidence.transaction)
        regassed["gas"] = hex(ESTIMATED_GAS + 1)
        with self.assertRaisesRegex(CandidateBindingError, "gas does not match"):
            self.candidate(replace(self.evidence, transaction=regassed))


class ControlledExecutionServiceTests(unittest.TestCase):
    """The reject/accept send-count contract, end to end against a scripted node."""

    def build(self, floor: int, *, read_context: Any = None) -> tuple[Any, ScriptedNode, list[Any]]:
        request = compile_request()
        approval = approved(request, model_output(floor))
        node = ScriptedNode()
        rpc = JsonRpcClient(transport=node)
        sends: list[Any] = []
        self.send_methods: list[list[str]] = []

        def send(transaction: Any) -> str:
            self.send_methods.append(list(node.methods))
            sends.append(transaction)
            return "0xsent"

        executor = Erc20ExecutionService(
            simulator(node),
            read_context=read_context or live_context_reader(rpc, SENDER, TOKEN),
            send=send,
            gate=ExecutionGate(),
        )
        return (executor, approval), node, sends

    def test_accepted_control_sends_the_exact_transaction_once(self) -> None:
        (executor, approval), node, sends = self.build(SENDER_AFTER)
        outcome = executor.run(approval, transfer_request(), candidate_id="candidate-1")

        self.assertTrue(outcome.decision.accepted)
        self.assertEqual(outcome.decision.reasonCodes, ())
        self.assertTrue(outcome.sent)
        self.assertEqual(outcome.send_result, "0xsent")
        self.assertEqual(sends, [outcome.candidate.transaction])
        self.assertEqual(outcome.candidate.transaction.gasLimit, str(ESTIMATED_GAS))

    def test_the_send_happens_only_after_the_snapshot_was_reverted(self) -> None:
        (executor, approval), node, sends = self.build(SENDER_AFTER)
        executor.run(approval, transfer_request(), candidate_id="candidate-1")

        self.assertEqual(len(sends), 1)
        methods_at_send = self.send_methods[0]
        self.assertEqual(methods_at_send.count("eth_sendTransaction"), 1)
        self.assertLess(methods_at_send.index("eth_sendTransaction"), methods_at_send.index("evm_revert"))

    def test_rejected_control_calls_the_external_send_zero_times(self) -> None:
        def never_read() -> Any:
            raise AssertionError("context was read before an accepted decision")

        (executor, approval), node, sends = self.build(SENDER_AFTER + 1, read_context=never_read)
        outcome = executor.run(approval, transfer_request(), candidate_id="candidate-1")

        self.assertFalse(outcome.sent)
        self.assertFalse(outcome.decision.accepted)
        self.assertEqual(outcome.decision.reasonCodes, ("ASSET_BALANCE_FLOOR_VIOLATION",))
        self.assertEqual(outcome.rejection, "ASSET_BALANCE_FLOOR_VIOLATION")
        self.assertEqual(sends, [])
        self.assertEqual(node.methods.count("eth_sendTransaction"), 1)  # the snapshot one only

    def test_context_drift_after_simulation_blocks_the_send(self) -> None:
        drifted = ChainContext.model_validate(
            {
                "schemaVersion": 1,
                "kind": "chain-context",
                "chainId": CHAIN_ID,
                "blockNumber": "17",
                "blockHash": "0x" + "c" * 64,
                "walletAddress": SENDER,
                "senderNonce": str(NONCE + 1),
                "tokenAddress": TOKEN,
                "assetBalance": str(SENDER_BEFORE),
            }
        )
        (executor, approval), node, sends = self.build(SENDER_AFTER, read_context=lambda: drifted)
        outcome = executor.run(approval, transfer_request(), candidate_id="candidate-1")

        self.assertTrue(outcome.decision.accepted)
        self.assertFalse(outcome.sent)
        self.assertIn("drift", outcome.rejection or "")
        self.assertEqual(sends, [])


class DecisionRecordTests(unittest.TestCase):
    def test_a_policy_bound_to_another_wallet_is_recorded_not_hidden(self) -> None:
        request = compile_request(walletAddress="0x" + "44" * 20, requestId="request-2", proposalId="proposal-2")
        approval = approved(request)
        evidence = simulator(ScriptedNode()).simulate_transfer(transfer_request(), lambda _: True).evidence
        candidate = candidate_from_evidence(approval, evidence, candidate_id="candidate-2")
        self.assertEqual(evaluate(approval, candidate).reasonCodes, ("POLICY_WALLET_MISMATCH",))


if __name__ == "__main__":
    unittest.main()
