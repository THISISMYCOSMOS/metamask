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
    MVP_COMPILER_RULES,
    PERCENTAGE_COMPILER_RULES,
    STRUCTURED_EDITOR_ASSUMPTION,
    STRUCTURED_EDITOR_REQUEST_ID,
    SynthesisInputError,
    approve_policy_proposal,
    artifact_json,
    build_structured_policy,
    compile_local_structured_proposal,
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

    def test_one_kind_percentage_request_uses_non_conflicting_rules(self) -> None:
        template_data = copy.deepcopy(self.template_data)
        template_data["invariants"] = [
            {
                "id": "portfolio-drawdown-from-intent",
                "kind": "portfolioDrawdownCapBps",
                "referenceValue1e18": "100000000000000000000",
                "maxDrawdownBps": "2000",
            }
        ]
        request = create_intent_request(
            request_id="percentage-intent-001",
            intent_text="기준 포트폴리오 가치에서 20% 넘게 하락하지 않게 해줘.",
            policy_template=InvariantPolicy.model_validate(template_data),
        )

        self.assertEqual(["portfolioDrawdownCapBps"], request.allowedInvariants)
        self.assertEqual(PERCENTAGE_COMPILER_RULES, request.compilerRules)
        self.assertNotIn(MVP_COMPILER_RULES[1], request.compilerRules)
        self.assertIn('Represent percentage thresholds as basis points: 20 percent is the integer string "2000".', request.compilerRules)
        self.assertIn("portfolioDrawdownCapBps requires an explicit referenceValue1e18.", request.compilerRules)
        self.assertIn("Never infer a missing referenceValue1e18 or any unrequested condition.", request.compilerRules)

    def test_mixed_percentage_request_preserves_canonical_subset_order(self) -> None:
        template_data = copy.deepcopy(self.template_data)
        template_data["invariants"] = [
            template_data["invariants"][0],
            {
                "id": "portfolio-drawdown-from-intent",
                "kind": "portfolioDrawdownCapBps",
                "referenceValue1e18": "100000000000000000000",
                "maxDrawdownBps": "2000",
            },
            {
                "id": "rolling-loss-percent-from-intent",
                "kind": "cumulativeLossCapBps",
                "windowSeconds": "86400",
                "maxLossBps": "500",
            },
        ]
        request = create_intent_request(
            request_id="percentage-intent-002",
            intent_text="최저 가치와 20% 낙폭, 24시간 5% 누적 손실 한도를 적용해줘.",
            policy_template=InvariantPolicy.model_validate(template_data),
        )

        self.assertEqual(
            ["portfolioValueFloor", "portfolioDrawdownCapBps", "cumulativeLossCapBps"],
            request.allowedInvariants,
        )
        self.assertEqual(PERCENTAGE_COMPILER_RULES, request.compilerRules)

    def test_allowed_invariants_reject_duplicates_and_noncanonical_order(self) -> None:
        duplicate = copy.deepcopy(self.request_data)
        duplicate["allowedInvariants"] = ["portfolioValueFloor", "portfolioValueFloor"]
        with self.assertRaisesRegex(ValidationError, "must not contain duplicates"):
            self.request(duplicate)

        noncanonical = copy.deepcopy(self.request_data)
        noncanonical["allowedInvariants"] = ["cumulativeLossCap", "portfolioValueFloor"]
        with self.assertRaisesRegex(ValidationError, "canonical-order subset"):
            self.request(noncanonical)

    def test_percentage_response_policy_kind_mismatch_fails_closed(self) -> None:
        template_data = copy.deepcopy(self.template_data)
        template_data["invariants"] = [
            {
                "id": "portfolio-drawdown-from-intent",
                "kind": "portfolioDrawdownCapBps",
                "referenceValue1e18": "100000000000000000000",
                "maxDrawdownBps": "2000",
            }
        ]
        request = create_intent_request(
            request_id="percentage-intent-003",
            intent_text="기준 가치에서 20% 넘게 하락하지 않게 해줘.",
            policy_template=InvariantPolicy.model_validate(template_data),
        )
        response_data = copy.deepcopy(self.response_data)
        response_data["requestSha256"] = canonical_model_sha256(request)

        with self.assertRaisesRegex(SynthesisInputError, "exactly match allowedInvariants"):
            compile_policy_proposal(request, self.response(response_data))

    def test_committed_absolute_artifact_hashes_remain_exact(self) -> None:
        self.assertEqual(
            "0xd66c2b1305d55d5981d55287cdb410b952b93e15bb3a481aa2031164042c6ba7",
            canonical_model_sha256(self.request()),
        )
        self.assertEqual(
            "0xed84bfa046632e62ab288ec91328897e10b1b856346749862eda835de7149b21",
            canonical_model_sha256(self.proposal()),
        )
        self.assertEqual(
            "0x2d15e62b950301a4594356609cfdfa3ce7fe87eb26bea60e32de8b2b20fc6deb",
            canonical_model_sha256(self.approval()),
        )
        self.assertEqual(
            "0x6bc7ec4fccbd7478aa597b83b41cb91161f3483bc2a6fe66f3f8311c44e77828",
            canonical_model_sha256(self.user_approval()),
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

    def test_structured_editor_compiles_local_proposal_without_offline_response(self) -> None:
        policy = build_structured_policy(
            policy_id="mvp-demo-loss-guard",
            fork=self.request().fork,
            invariants=[
                {
                    "id": "portfolio-drawdown-structured",
                    "kind": "portfolioDrawdownCapBps",
                    "referenceValue1e18": "100000000000000000000",
                    "maxDrawdownBps": "2000",
                }
            ],
        )
        request = create_intent_request(
            request_id=STRUCTURED_EDITOR_REQUEST_ID,
            intent_text="구조화된 편집기로 20% 낙폭 한도를 설정",
            policy_template=policy,
        )
        proposal = compile_local_structured_proposal(request, policy.invariants)

        self.assertEqual("local", proposal.compiler.provider)
        self.assertEqual(["portfolioDrawdownCapBps"], [i.kind for i in proposal.policy.invariants])
        self.assertIn(STRUCTURED_EDITOR_ASSUMPTION, proposal.assumptions)
        self.assertEqual(STRUCTURED_EDITOR_REQUEST_ID, proposal.request.requestId)

    def test_structured_editor_hash_differs_from_committed_default_proposal(self) -> None:
        policy = build_structured_policy(
            policy_id="mvp-demo-loss-guard",
            fork=self.request().fork,
            invariants=self.template_data["invariants"],
        )
        request = create_intent_request(
            request_id=STRUCTURED_EDITOR_REQUEST_ID,
            intent_text="구조화된 편집기로 동일한 조건을 다시 제출",
            policy_template=policy,
        )
        proposal = compile_local_structured_proposal(request, policy.invariants)

        self.assertNotEqual(
            "0xed84bfa046632e62ab288ec91328897e10b1b856346749862eda835de7149b21",
            canonical_model_sha256(proposal),
        )

    def test_structured_editor_rejects_duplicate_kind_and_noncanonical_order(self) -> None:
        policy = build_structured_policy(
            policy_id="mvp-demo-loss-guard",
            fork=self.request().fork,
            invariants=[
                {"id": "floor-a", "kind": "portfolioValueFloor", "floorValue1e18": "1"},
                {"id": "floor-b", "kind": "portfolioValueFloor", "floorValue1e18": "2"},
            ],
        )
        with self.assertRaises(SynthesisInputError):
            create_intent_request(
                request_id=STRUCTURED_EDITOR_REQUEST_ID,
                intent_text="중복 조건",
                policy_template=policy,
            )

    def test_structured_editor_drawdown_requires_reference_value(self) -> None:
        with self.assertRaises(SynthesisInputError):
            build_structured_policy(
                policy_id="mvp-demo-loss-guard",
                fork=self.request().fork,
                invariants=[
                    {
                        "id": "portfolio-drawdown-structured",
                        "kind": "portfolioDrawdownCapBps",
                        "maxDrawdownBps": "2000",
                    }
                ],
            )

    def test_structured_editor_rejects_out_of_range_bps(self) -> None:
        with self.assertRaises(SynthesisInputError):
            build_structured_policy(
                policy_id="mvp-demo-loss-guard",
                fork=self.request().fork,
                invariants=[
                    {
                        "id": "rolling-loss-percent-structured",
                        "kind": "cumulativeLossCapBps",
                        "windowSeconds": "86400",
                        "maxLossBps": "10001",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
