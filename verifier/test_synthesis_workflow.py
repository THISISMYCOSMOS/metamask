from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from candidate_models import CandidateTrace
from evaluate_approved_candidate import evaluate_approved_candidate
from evaluate_invariants import EvaluationInputError
from invariant_models import InvariantPolicy
from synthesis_models import (
    IntentCompilerRequest,
    LlmPolicyResponse,
    PolicyApproval,
    PolicyProposal,
    canonical_model_sha256,
)
from synthesis_workflow import (
    SynthesisInputError,
    approve_policy_proposal,
    artifact_json,
    compile_policy_proposal,
    create_intent_request,
)


ROOT = Path(__file__).resolve().parents[1]


class SynthesisWorkflowStressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_data = json.loads(
            (ROOT / "specs" / "mvp-candidate-invariants.json").read_text(encoding="utf-8")
        )
        cls.request_data = json.loads(
            (ROOT / "specs" / "mvp-intent-request.json").read_text(encoding="utf-8")
        )
        cls.response_data = json.loads(
            (ROOT / "specs" / "mvp-llm-response.fixture.json").read_text(encoding="utf-8")
        )
        cls.proposal_data = json.loads(
            (ROOT / "specs" / "mvp-policy-proposal.json").read_text(encoding="utf-8")
        )
        cls.approval_data = json.loads(
            (ROOT / "specs" / "mvp-test-policy-approval.json").read_text(encoding="utf-8")
        )
        cls.user_approval_data = json.loads(
            (ROOT / "specs" / "mvp-user-policy-approval.json").read_text(encoding="utf-8")
        )
        cls.candidate_data = json.loads(
            (ROOT / "traces" / "mvp-candidate-reject.json").read_text(encoding="utf-8")
        )

    def request(self, data: dict | None = None) -> IntentCompilerRequest:
        return IntentCompilerRequest.model_validate(copy.deepcopy(data or self.request_data))

    def response(self, data: dict | None = None) -> LlmPolicyResponse:
        return LlmPolicyResponse.model_validate(copy.deepcopy(data or self.response_data))

    def proposal(self, data: dict | None = None) -> PolicyProposal:
        return PolicyProposal.model_validate(copy.deepcopy(data or self.proposal_data))

    def approval(self, data: dict | None = None) -> PolicyApproval:
        return PolicyApproval.model_validate(copy.deepcopy(data or self.approval_data))

    def user_approval(self, data: dict | None = None) -> PolicyApproval:
        return PolicyApproval.model_validate(copy.deepcopy(data or self.user_approval_data))

    def test_request_fixture_is_exact_deterministic_output(self) -> None:
        generated = create_intent_request(
            request_id="mvp-intent-001",
            intent_text=(ROOT / "specs" / "mvp-intent.ko.txt").read_text(encoding="utf-8").strip(),
            policy_template=InvariantPolicy.model_validate(copy.deepcopy(self.template_data)),
        )
        self.assertEqual(self.request(), generated)
        self.assertEqual(
            (ROOT / "specs" / "mvp-intent-request.json").read_text(encoding="utf-8"),
            artifact_json(generated),
        )

    def test_response_compiles_to_exact_unapproved_proposal_fixture(self) -> None:
        generated = compile_policy_proposal(self.request(), self.response())
        self.assertEqual(self.proposal(), generated)
        self.assertEqual(
            (ROOT / "specs" / "mvp-policy-proposal.json").read_text(encoding="utf-8"),
            artifact_json(generated),
        )

    def test_test_approval_fixture_is_exact_hash_confirmed_output(self) -> None:
        proposal = self.proposal()
        proposal_hash = canonical_model_sha256(proposal)
        generated = approve_policy_proposal(
            proposal,
            confirmation=f"APPROVE {proposal_hash}",
            approved_by="mvp-test-fixture",
            approval_scope="test-fixture",
        )
        self.assertEqual(self.approval(), generated)
        self.assertEqual(
            (ROOT / "specs" / "mvp-test-policy-approval.json").read_text(encoding="utf-8"),
            artifact_json(generated),
        )

    def test_wrong_request_hash_fork_kind_order_and_rationale_fail_closed(self) -> None:
        unknown_field = copy.deepcopy(self.response_data)
        unknown_field["unexpected"] = True
        with self.assertRaises(ValidationError):
            self.response(unknown_field)

        wrong_hash = copy.deepcopy(self.response_data)
        wrong_hash["requestSha256"] = "0x" + "00" * 32
        with self.assertRaises(SynthesisInputError):
            compile_policy_proposal(self.request(), self.response(wrong_hash))

        wrong_fork = copy.deepcopy(self.response_data)
        wrong_fork["policy"]["fork"]["blockNumber"] = "25700001"
        with self.assertRaises(SynthesisInputError):
            compile_policy_proposal(self.request(), self.response(wrong_fork))

        wrong_order = copy.deepcopy(self.response_data)
        wrong_order["policy"]["invariants"].reverse()
        with self.assertRaises(SynthesisInputError):
            compile_policy_proposal(self.request(), self.response(wrong_order))

        wrong_rationale = copy.deepcopy(self.response_data)
        wrong_rationale["rationales"].reverse()
        with self.assertRaises(SynthesisInputError):
            compile_policy_proposal(self.request(), self.response(wrong_rationale))

    def test_approval_requires_exact_hash_confirmation(self) -> None:
        with self.assertRaises(SynthesisInputError):
            approve_policy_proposal(
                self.proposal(),
                confirmation="APPROVE 0x" + "00" * 32,
                approved_by="user",
            )

    def test_embedded_proposal_or_policy_tampering_invalidates_approval(self) -> None:
        proposal_tamper = copy.deepcopy(self.approval_data)
        proposal_tamper["proposal"]["request"]["intentText"] += " changed"
        with self.assertRaises(ValidationError):
            self.approval(proposal_tamper)

        policy_tamper = copy.deepcopy(self.approval_data)
        policy_tamper["proposal"]["policy"]["invariants"][0]["floorValue1e18"] = "1"
        with self.assertRaises(ValidationError):
            self.approval(policy_tamper)

    def test_runtime_refuses_test_approval_without_explicit_test_flag(self) -> None:
        candidate = CandidateTrace.model_validate(copy.deepcopy(self.candidate_data))
        with self.assertRaises(EvaluationInputError):
            evaluate_approved_candidate(self.approval(), candidate)
        report = evaluate_approved_candidate(
            self.approval(),
            candidate,
            allow_test_fixture=True,
        )
        self.assertFalse(report["accepted"])
        self.assertEqual([False, False], [item["passed"] for item in report["evaluations"]])

    def test_user_scoped_approval_is_accepted_without_test_flag(self) -> None:
        proposal = self.proposal()
        proposal_hash = canonical_model_sha256(proposal)
        approval = approve_policy_proposal(
            proposal,
            confirmation=f"APPROVE {proposal_hash}",
            approved_by="user",
        )
        self.assertEqual(self.user_approval(), approval)
        self.assertEqual(
            (ROOT / "specs" / "mvp-user-policy-approval.json").read_text(encoding="utf-8"),
            artifact_json(approval),
        )
        report = evaluate_approved_candidate(
            approval,
            CandidateTrace.model_validate(copy.deepcopy(self.candidate_data)),
        )
        self.assertFalse(report["accepted"])


if __name__ == "__main__":
    unittest.main()
