from __future__ import annotations

import unittest

import server
from synthesis_workflow import SynthesisInputError


class PolicyUiWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        server.policy_state = None

    def exact_intent(self) -> str:
        return server.INTENT_PATH.read_text(encoding="utf-8").strip()

    def test_committed_intent_reaches_unapproved_offline_proposal(self) -> None:
        state = server.build_policy_flow(self.exact_intent())

        self.assertEqual("proposal-ready", state["stage"])
        self.assertEqual("offline-fixture", state["compilerSource"])
        self.assertIsNotNone(state["proposal"])
        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])
        self.assertEqual(
            "0xed84bfa046632e62ab288ec91328897e10b1b856346749862eda835de7149b21",
            state["proposalSha256"],
        )

    def test_unbound_intent_stops_after_request_creation(self) -> None:
        state = server.build_policy_flow("포트폴리오 손실을 제한해줘.")

        self.assertEqual("request-created", state["stage"])
        self.assertEqual("provider-required", state["compilerSource"])
        self.assertIsNone(state["proposal"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_exact_hash_approval_records_audit_artifact_but_not_broadcast_eligibility(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertEqual("user", state["approval"]["approvalScope"])
        self.assertEqual("user", state["approval"]["approvedBy"])
        self.assertEqual(
            "0x6bc7ec4fccbd7478aa597b83b41cb91161f3483bc2a6fe66f3f8311c44e77828",
            state["approvalSha256"],
        )
        self.assertFalse(state["candidateEvaluation"]["accepted"])
        self.assertEqual(
            [False, False],
            [item["passed"] for item in state["candidateEvaluation"]["evaluations"]],
        )
        self.assertEqual(
            "18981111516100000000000",
            state["candidateEvaluation"]["evaluations"][0]["observedMinimumValue1e18"],
        )
        self.assertEqual(
            "2499418800000000000000",
            state["candidateEvaluation"]["evaluations"][1]["observedMaximumLossValue1e18"],
        )
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_wrong_hash_approval_fails_closed(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())

        with self.assertRaises(SynthesisInputError):
            server.approve_current_policy("APPROVE 0x" + "00" * 32)

        self.assertIsNone(server.policy_state["approval"])
        self.assertIsNone(server.policy_state["approvalSha256"])
        self.assertIsNone(server.policy_state["candidateEvaluation"])

    def test_valid_approval_records_even_when_candidate_history_coverage_is_insufficient(self) -> None:
        server.policy_state = server.build_structured_policy_flow(
            "48시간 손실 한도 - 후보 이력 커버리지 부족 테스트",
            [
                {
                    "id": "insufficient-coverage-loss-cap",
                    "kind": "cumulativeLossCap",
                    "windowSeconds": "172800",
                    "maxLossValue1e18": "1999535040000000000000",
                }
            ],
        )
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertIsNotNone(state["approval"])
        self.assertIsNotNone(state["approvalSha256"])
        self.assertEqual("evaluation-invalid", state["candidateEvaluation"]["status"])
        self.assertIn("history coverage is incomplete", state["candidateEvaluation"]["reason"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_valid_approval_records_even_when_drawdown_reference_mismatches(self) -> None:
        server.policy_state = server.build_structured_policy_flow(
            "낙폭 참조값 불일치 테스트",
            [
                {
                    "id": "mismatched-reference-drawdown",
                    "kind": "portfolioDrawdownCapBps",
                    "referenceValue1e18": "1",
                    "maxDrawdownBps": "2000",
                }
            ],
        )
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertIsNotNone(state["approval"])
        self.assertEqual("evaluation-invalid", state["candidateEvaluation"]["status"])
        self.assertIn("referenceValue1e18", state["candidateEvaluation"]["reason"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_structured_conditions_create_fresh_local_proposal_without_provider(self) -> None:
        state = server.build_structured_policy_flow(
            "구조화된 조건 편집기로 최저 가치 조건을 새로 설정",
            [
                {
                    "id": "portfolio-value-floor-structured",
                    "kind": "portfolioValueFloor",
                    "floorValue1e18": "1000000000000000000000",
                }
            ],
        )

        self.assertEqual("proposal-ready", state["stage"])
        self.assertEqual("local-structured-editor", state["compilerSource"])
        self.assertIsNotNone(state["proposal"])
        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])
        self.assertEqual(["portfolioValueFloor"], [i["kind"] for i in state["proposal"]["policy"]["invariants"]])

    def test_structured_conditions_invalidate_prior_approval_with_fresh_hashes(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())
        proposal_hash_before_approval = server.policy_state["proposalSha256"]
        approved = server.approve_current_policy(f"APPROVE {proposal_hash_before_approval}")
        server.policy_state = approved
        previous_proposal_hash = approved["proposalSha256"]

        state = server.replace_structured_policy_flow(
            "구조화된 조건 편집기로 조건 변경",
            [
                {
                    "id": "rolling-loss-cap-structured",
                    "kind": "cumulativeLossCap",
                    "windowSeconds": "3600",
                    "maxLossValue1e18": "500000000000000000000",
                }
            ],
        )

        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertNotEqual(previous_proposal_hash, state["proposalSha256"])
        with self.assertRaises(SynthesisInputError):
            server.approve_current_policy(f"APPROVE {previous_proposal_hash}")

    def test_structured_conditions_reject_duplicate_kind(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow(
                "중복 조건",
                [
                    {"id": "floor-a", "kind": "portfolioValueFloor", "floorValue1e18": "1"},
                    {"id": "floor-b", "kind": "portfolioValueFloor", "floorValue1e18": "2"},
                ],
            )

    def test_structured_conditions_reject_missing_drawdown_reference(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow(
                "낙폭 조건 누락 테스트",
                [
                    {
                        "id": "portfolio-drawdown-structured",
                        "kind": "portfolioDrawdownCapBps",
                        "maxDrawdownBps": "2000",
                    }
                ],
            )

    def test_structured_conditions_reject_empty_list(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow("빈 조건", [])


if __name__ == "__main__":
    unittest.main()
