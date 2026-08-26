from __future__ import annotations

import copy
import unittest

from pydantic import ValidationError

from .canonical import canonical_json, canonical_sha256
from .evaluator import evaluate
from .gate import ExecutionGate, GateRejected
from .models import (
    ApprovedPolicyEnvelope,
    ChainContext,
    CompilationResult,
    CompileRequest,
    ExecutionCandidate,
    PolicyProposal,
)
from .policy_binding import PolicyApprovalError, approval_confirmation, approve, verify_request_binding


WALLET = "0x1111111111111111111111111111111111111111"
TOKEN = "0x2222222222222222222222222222222222222222"
RECIPIENT = "0x3333333333333333333333333333333333333333"
HASH_A = "0x" + "aa" * 32
GAS_LIMIT, GAS_USED = 60000, 51000


def transfer_data(recipient: str, amount: int) -> str:
    return "0xa9059cbb" + ("0" * 24) + recipient[2:] + f"{amount:064x}"


def request_data() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "policy-compile-request",
        "requestId": "request-1",
        "proposalId": "proposal-1",
        "policyId": "usdc-floor",
        "intentText": "USDC를 900 base-unit 이상 남겨줘",
        "chainId": 1,
        "walletAddress": WALLET,
        "tokenAddress": TOKEN,
        "tokenSymbol": "USDC",
        "tokenDecimals": 6,
    }


def proposal_data(**policy_overrides: object) -> dict[str, object]:
    request = CompileRequest.model_validate(request_data())
    policy: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "assetBalanceFloor",
        "policyId": "usdc-floor",
        "chainId": 1,
        "walletAddress": WALLET,
        "tokenAddress": TOKEN,
        "assetBalanceFloor": "900",
    }
    policy.update(policy_overrides)
    return {
        "schemaVersion": 1,
        "kind": "policy-proposal",
        "proposalId": "proposal-1",
        "requestSha256": canonical_sha256(request),
        "intentText": request.intentText,
        "compiler": {"provider": "anthropic", "model": "claude-test"},
        "policy": policy,
        "policySha256": canonical_sha256(policy),
        "rationales": ["잔고 하한을 base-unit으로 고정한다."],
        "assumptions": ["금액은 base-unit으로 해석했다."],
        "unsupportedItems": [],
    }


def approval_data(**policy_overrides: object) -> dict[str, object]:
    proposal = proposal_data(**policy_overrides)
    proposal_hash = canonical_sha256(proposal)
    return {
        "schemaVersion": 1,
        "kind": "approved-policy-envelope",
        "approvalId": "approval-1",
        "approvalScope": "user",
        "approvedBy": "wallet-owner",
        "proposal": proposal,
        "proposalSha256": proposal_hash,
        "policySha256": proposal["policySha256"],
        "confirmation": f"APPROVE {proposal_hash}",
    }


def context_data() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "chain-context",
        "chainId": 1,
        "blockNumber": "100",
        "blockHash": HASH_A,
        "walletAddress": WALLET,
        "senderNonce": "7",
        "tokenAddress": TOKEN,
        "assetBalance": "1000",
    }


def candidate_data(approval: ApprovedPolicyEnvelope, *, amount: int = 100) -> dict[str, object]:
    context = context_data()
    ledger = {"schemaVersion": 1, "kind": "history-ledger", "context": context, "entries": []}
    return {
        "schemaVersion": 1,
        "kind": "erc20-transfer-candidate",
        "candidateId": "candidate-1",
        "approvalSha256": canonical_sha256(approval),
        "policySha256": approval.policySha256,
        "context": context,
        "history": ledger,
        "historySha256": canonical_sha256(ledger),
        "transaction": {
            "schemaVersion": 1,
            "kind": "evm-transaction",
            "chainId": 1,
            "fromAddress": WALLET,
            "toAddress": TOKEN,
            "nonce": "7",
            "gasLimit": str(GAS_LIMIT),
            "value": "0",
            "data": transfer_data(RECIPIENT, amount),
        },
        "simulation": {
            "schemaVersion": 1,
            "kind": "erc20-transfer-simulation",
            "status": "success",
            "chainId": 1,
            "tokenAddress": TOKEN,
            "senderAddress": WALLET,
            "recipientAddress": RECIPIENT,
            "transferAmount": str(amount),
            "senderNonce": "7",
            "gasLimit": str(GAS_LIMIT),
            "gasUsed": str(GAS_USED),
            "beforeAssetBalance": "1000",
            "afterAssetBalance": str(1000 - amount),
            "beforeRecipientBalance": "0",
            "afterRecipientBalance": str(amount),
        },
    }


class SharedModelTests(unittest.TestCase):
    def test_canonical_json_is_key_order_independent(self) -> None:
        self.assertEqual(canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')
        self.assertEqual(canonical_sha256({"b": 1, "a": 2}), canonical_sha256({"a": 2, "b": 1}))

    def test_model_and_plain_dict_hash_identically(self) -> None:
        data = proposal_data()
        self.assertEqual(canonical_sha256(PolicyProposal.model_validate(data)), canonical_sha256(data))

    def test_strict_models_reject_extra_and_non_string_uint(self) -> None:
        data = approval_data()
        data["extra"] = "forbidden"
        with self.assertRaises(ValidationError):
            ApprovedPolicyEnvelope.model_validate(data)
        ctx = context_data()
        ctx["assetBalance"] = 1000
        with self.assertRaises(ValidationError):
            ChainContext.model_validate(ctx)

    def test_a_proposal_carrying_unsupported_items_cannot_exist(self) -> None:
        data = proposal_data()
        data["unsupportedItems"] = ["하루 지출 한도"]
        with self.assertRaises(ValidationError):
            PolicyProposal.model_validate(data)

    def test_exact_approval_hash_is_required(self) -> None:
        data = approval_data()
        data["confirmation"] = "APPROVE 0x" + "00" * 32
        with self.assertRaises(ValidationError):
            ApprovedPolicyEnvelope.model_validate(data)


class CompilationResultTests(unittest.TestCase):
    def result_data(self, **overrides: object) -> dict[str, object]:
        proposal = proposal_data()
        data: dict[str, object] = {
            "schemaVersion": 1,
            "kind": "policy-compilation-result",
            "requestId": "request-1",
            "requestSha256": proposal["requestSha256"],
            "supported": True,
            "proposal": proposal,
            "proposalSha256": canonical_sha256(proposal),
            "rationales": list(proposal["rationales"]),  # type: ignore[arg-type]
            "assumptions": list(proposal["assumptions"]),  # type: ignore[arg-type]
            "unsupportedItems": [],
            "reasonCodes": [],
        }
        data.update(overrides)
        return data

    def test_supported_result_must_bind_its_proposal(self) -> None:
        result = CompilationResult.model_validate(self.result_data())
        self.assertEqual(result.approvable_proposal.proposalId, "proposal-1")
        with self.assertRaises(ValidationError):
            CompilationResult.model_validate(self.result_data(proposalSha256="0x" + "00" * 32))
        with self.assertRaises(ValidationError):
            CompilationResult.model_validate(self.result_data(unsupportedItems=["일일 한도"]))
        with self.assertRaises(ValidationError):
            CompilationResult.model_validate(self.result_data(proposal=None, proposalSha256=None))

    def test_non_approvable_result_cannot_smuggle_a_proposal(self) -> None:
        with self.assertRaises(ValidationError):
            CompilationResult.model_validate(self.result_data(supported=False))
        silent = self.result_data(supported=False, proposal=None, proposalSha256=None)
        with self.assertRaises(ValidationError):
            CompilationResult.model_validate(silent)
        explained = CompilationResult.model_validate({**silent, "reasonCodes": ["MODEL_REPORTED_UNSUPPORTED"]})
        self.assertIsNone(explained.proposal)
        with self.assertRaisesRegex(ValueError, "not approvable"):
            explained.approvable_proposal


class ApprovalPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = CompileRequest.model_validate(request_data())
        self.proposal = PolicyProposal.model_validate(proposal_data())

    def test_exact_confirmation_binds_the_reviewed_proposal(self) -> None:
        envelope = approve(
            self.proposal,
            approval_id="approval-1",
            approved_by="wallet-owner",
            confirmation=approval_confirmation(self.proposal),
            request=self.request,
        )
        self.assertEqual(envelope.proposalSha256, canonical_sha256(self.proposal))
        self.assertEqual(envelope.proposal, self.proposal)

    def test_request_binding_catches_every_caller_owned_fact(self) -> None:
        for field, value in (
            ("chainId", 2),
            ("walletAddress", RECIPIENT),
            ("tokenAddress", RECIPIENT),
            ("policyId", "other-floor"),
            ("proposalId", "proposal-2"),
            ("intentText", "다른 요청"),
        ):
            with self.subTest(field=field):
                drifted = CompileRequest.model_validate({**request_data(), field: value})
                with self.assertRaises(PolicyApprovalError):
                    verify_request_binding(self.proposal, drifted)


class EvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.approval = ApprovedPolicyEnvelope.model_validate(approval_data())

    def test_floor_boundary_accepts_and_binds_all_hashes(self) -> None:
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval, amount=100))
        decision = evaluate(self.approval, candidate)
        self.assertEqual(decision.reasonCodes, ())
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.executionSha256, canonical_sha256(candidate.transaction))
        self.assertEqual(decision.candidateSha256, canonical_sha256(candidate))

    def test_one_base_unit_below_the_floor_is_rejected(self) -> None:
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval, amount=101))
        self.assertEqual(evaluate(self.approval, candidate).reasonCodes, ("ASSET_BALANCE_FLOOR_VIOLATION",))

    def test_invalid_calldata_fails_closed(self) -> None:
        data = candidate_data(self.approval)
        data["transaction"]["data"] = "0x1234"  # type: ignore[index]
        self.assertIn(
            "INVALID_ERC20_TRANSFER_CALLDATA",
            evaluate(self.approval, ExecutionCandidate.model_validate(data)).reasonCodes,
        )

    def test_calldata_substituted_after_simulation_is_caught(self) -> None:
        """A predicted balance alone would not notice a swapped recipient."""
        swapped = candidate_data(self.approval)
        swapped["transaction"]["data"] = transfer_data("0x" + "99" * 20, 100)  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(swapped)).reasonCodes,
            ("SIMULATION_RECIPIENT_MISMATCH",),
        )

        raised = candidate_data(self.approval)
        raised["transaction"]["data"] = transfer_data(RECIPIENT, 50)  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(raised)).reasonCodes,
            ("SIMULATION_AMOUNT_MISMATCH",),
        )

    def test_gas_limit_or_nonce_drift_from_the_simulation_is_caught(self) -> None:
        gassed = candidate_data(self.approval)
        gassed["transaction"]["gasLimit"] = str(GAS_LIMIT + 1)  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(gassed)).reasonCodes,
            ("SIMULATION_GAS_LIMIT_MISMATCH",),
        )
        drifted = candidate_data(self.approval)
        drifted["simulation"]["senderNonce"] = "8"  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(drifted)).reasonCodes,
            ("SIMULATION_NONCE_MISMATCH",),
        )

    def test_inconsistent_simulated_balances_fail_closed(self) -> None:
        data = candidate_data(self.approval)
        data["simulation"]["afterAssetBalance"] = "999"  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(data)).reasonCodes,
            ("SIMULATION_PREDICTED_BALANCE_MISMATCH",),
        )
        recipient = candidate_data(self.approval)
        recipient["simulation"]["afterRecipientBalance"] = "1"  # type: ignore[index]
        self.assertEqual(
            evaluate(self.approval, ExecutionCandidate.model_validate(recipient)).reasonCodes,
            ("SIMULATION_RECIPIENT_BALANCE_MISMATCH",),
        )

    def test_a_policy_for_a_different_wallet_is_reported_not_silently_applied(self) -> None:
        approval = ApprovedPolicyEnvelope.model_validate(approval_data(walletAddress=RECIPIENT))
        candidate = ExecutionCandidate.model_validate(candidate_data(approval))
        self.assertEqual(evaluate(approval, candidate).reasonCodes, ("POLICY_WALLET_MISMATCH",))


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.approval = ApprovedPolicyEnvelope.model_validate(approval_data())

    def test_reject_never_calls_sender(self) -> None:
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval, amount=101))
        sends: list[object] = []
        with self.assertRaises(GateRejected):
            ExecutionGate().execute(
                self.approval, candidate, read_context=lambda: candidate.context, send=sends.append
            )
        self.assertEqual(sends, [])

    def test_accepted_decision_sends_exactly_once_in_this_process(self) -> None:
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval))
        gate = ExecutionGate()
        sends: list[object] = []
        decision, receipt = gate.execute(
            self.approval, candidate, read_context=lambda: candidate.context, send=lambda tx: sends.append(tx) or "sent"
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(receipt, "sent")
        with self.assertRaisesRegex(GateRejected, "already consumed"):
            gate.execute(self.approval, candidate, read_context=lambda: candidate.context, send=sends.append)
        self.assertEqual(sends, [candidate.transaction])

    def test_a_second_gate_instance_does_not_share_the_one_shot_record(self) -> None:
        """Documented boundary: the one-shot property is process- and instance-local."""
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval))
        sends: list[object] = []
        for _ in range(2):
            ExecutionGate().execute(
                self.approval, candidate, read_context=lambda: candidate.context, send=sends.append
            )
        self.assertEqual(len(sends), 2)

    def test_context_drift_blocks_before_send(self) -> None:
        candidate = ExecutionCandidate.model_validate(candidate_data(self.approval))
        drifted = copy.deepcopy(candidate.context.model_dump())
        drifted["senderNonce"] = "8"
        sends: list[object] = []
        with self.assertRaises(GateRejected):
            ExecutionGate().execute(
                self.approval,
                candidate,
                read_context=lambda: ChainContext.model_validate(drifted),
                send=sends.append,
            )
        self.assertEqual(sends, [])


if __name__ == "__main__":
    unittest.main()
