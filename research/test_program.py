from __future__ import annotations

import json
import unittest
from pathlib import Path

from research.models import BenchmarkDataset
from research.program import ProgramRejected, approve_and_decide, build_proposal

from candidate_models import CandidateTrace
from invariant_models import ForkBinding
from synthesis_models import canonical_model_sha256


ROOT = Path(__file__).resolve().parents[1]


class ResearchProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = BenchmarkDataset.model_validate_json(
            (ROOT / "research" / "data" / "compiler_cases.json").read_text(encoding="utf-8")
        )
        cls.candidate = CandidateTrace.model_validate_json(
            (ROOT / "traces" / "mvp-candidate-reject.json").read_text(encoding="utf-8")
        )
        cls.fork = ForkBinding.model_validate(cls.candidate.fork.model_dump(mode="json"))

    def case(self, case_id: str):
        return next(case for case in self.dataset.cases if case.caseId == case_id)

    def proposal(self, case_id: str):
        case = self.case(case_id)
        return build_proposal(
            case,
            case.expected,
            fork=self.fork,
            compiler_provider="fixture",
            compiler_model="contract-test-v1",
        )

    def test_dataset_has_sixty_fixed_cases_across_all_four_kinds(self) -> None:
        self.assertEqual(60, len(self.dataset.cases))
        kinds = {case.expected.invariant.kind for case in self.dataset.cases if case.expected.invariant}
        self.assertEqual(
            {"portfolioValueFloor", "portfolioDrawdownCapBps", "cumulativeLossCap", "cumulativeLossCapBps"},
            kinds,
        )

    def test_each_supported_kind_becomes_the_existing_strict_policy_shape(self) -> None:
        for case_id in ("pvf-001", "pdd-001", "cla-001", "clb-001"):
            with self.subTest(case_id=case_id):
                proposal = self.proposal(case_id)
                self.assertEqual(self.case(case_id).expected.invariant.kind, proposal.policy.invariants[0].kind)

    def test_drawdown_reference_comes_from_caller_context(self) -> None:
        case = self.case("pdd-002")
        proposal = self.proposal("pdd-002")
        self.assertEqual(case.context.currentPortfolioValue1e18, proposal.policy.invariants[0].referenceValue1e18)

    def test_unsupported_intent_never_becomes_a_proposal(self) -> None:
        case = self.case("uns-001")
        with self.assertRaises(ProgramRejected):
            build_proposal(
                case,
                case.expected,
                fork=self.fork,
                compiler_provider="fixture",
                compiler_model="contract-test-v1",
            )

    def test_exact_hash_approval_then_decides_the_simulated_candidate(self) -> None:
        proposal = self.proposal("pvf-012")
        confirmation = f"APPROVE {canonical_model_sha256(proposal)}"
        report = approve_and_decide(proposal, confirmation=confirmation, candidate=self.candidate)
        self.assertFalse(report["accepted"])

    def test_wrong_hash_never_reaches_candidate_evaluation(self) -> None:
        proposal = self.proposal("pvf-001")
        with self.assertRaisesRegex(Exception, "confirmation"):
            approve_and_decide(
                proposal,
                confirmation="APPROVE 0x" + "00" * 32,
                candidate=self.candidate,
            )


if __name__ == "__main__":
    unittest.main()
