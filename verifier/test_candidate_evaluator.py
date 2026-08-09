from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from build_candidate_fixture import build_candidate_fixture, candidate_fixture_json
from candidate_models import CandidateTrace
from evaluate_candidate import EvaluationInputError, evaluate_candidate
from invariant_models import InvariantPolicy
from models import Trace


ROOT = Path(__file__).resolve().parents[1]


class CandidateEvaluatorStressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_data = json.loads((ROOT / "specs" / "mvp-candidate-invariants.json").read_text(encoding="utf-8"))
        cls.candidate_data = json.loads((ROOT / "traces" / "mvp-candidate-reject.json").read_text(encoding="utf-8"))
        cls.source_data = json.loads((ROOT / "traces" / "cumulative-loss.json").read_text(encoding="utf-8"))

    def policy(self, data: dict | None = None) -> InvariantPolicy:
        return InvariantPolicy.model_validate(copy.deepcopy(data or self.policy_data))

    def candidate(self, data: dict | None = None) -> CandidateTrace:
        return CandidateTrace.model_validate(copy.deepcopy(data or self.candidate_data))

    def test_committed_fixture_is_exact_deterministic_adapter_output(self) -> None:
        generated = build_candidate_fixture(self.policy(), Trace.model_validate(copy.deepcopy(self.source_data)))
        self.assertEqual(self.candidate(), generated)
        self.assertEqual(
            (ROOT / "traces" / "mvp-candidate-reject.json").read_text(encoding="utf-8"),
            candidate_fixture_json(generated),
        )

    def test_adapter_refuses_to_invent_missing_source_history(self) -> None:
        data = copy.deepcopy(self.policy_data)
        data["invariants"][1]["windowSeconds"] = "9999999999"
        with self.assertRaises(EvaluationInputError):
            build_candidate_fixture(
                self.policy(data),
                Trace.model_validate(copy.deepcopy(self.source_data)),
            )

    def test_g3_final_candidate_is_rejected_by_both_invariants(self) -> None:
        report = evaluate_candidate(self.policy(), self.candidate())
        self.assertFalse(report["accepted"])
        self.assertEqual([False, False], [item["passed"] for item in report["evaluations"]])

    def test_benign_candidate_at_inclusive_limits_is_accepted(self) -> None:
        data = copy.deepcopy(self.candidate_data)
        floor = self.policy_data["invariants"][0]["floorValue1e18"]
        cap = int(self.policy_data["invariants"][1]["maxLossValue1e18"])
        initial = int(floor) + cap
        candidate_timestamp = int(data["transitions"][-1]["timestamp"])
        data["coverageStartTimestamp"] = str(candidate_timestamp - 86_400)
        data["initialValue1e18"] = str(initial)
        data["transitions"] = [
            {
                "transitionIndex": 0,
                "timestamp": str(candidate_timestamp),
                "beforeValue1e18": str(initial),
                "afterValue1e18": floor,
                "role": "candidate",
            }
        ]
        report = evaluate_candidate(self.policy(), self.candidate(data))
        self.assertTrue(report["accepted"])
        self.assertEqual([True, True], [item["passed"] for item in report["evaluations"]])

    def test_restorative_candidate_is_judged_on_its_final_state(self) -> None:
        data = copy.deepcopy(self.candidate_data)
        initial = int(self.policy_data["invariants"][0]["floorValue1e18"]) + 10**21
        low = initial - 5 * 10**21
        candidate_timestamp = int(data["transitions"][-1]["timestamp"])
        data["coverageStartTimestamp"] = str(candidate_timestamp - 86_400)
        data["initialValue1e18"] = str(initial)
        data["transitions"] = [
            {
                "transitionIndex": 0,
                "timestamp": str(candidate_timestamp - 43_200),
                "beforeValue1e18": str(initial),
                "afterValue1e18": str(low),
                "role": "history",
            },
            {
                "transitionIndex": 1,
                "timestamp": str(candidate_timestamp),
                "beforeValue1e18": str(low),
                "afterValue1e18": str(initial),
                "role": "candidate",
            },
        ]
        report = evaluate_candidate(self.policy(), self.candidate(data))
        self.assertTrue(report["accepted"])
        self.assertEqual([True, True], [item["passed"] for item in report["evaluations"]])

    def test_incomplete_history_fails_closed(self) -> None:
        data = copy.deepcopy(self.candidate_data)
        data["transitions"].pop(0)
        for index, transition in enumerate(data["transitions"]):
            transition["transitionIndex"] = index
        data["coverageStartTimestamp"] = data["transitions"][0]["timestamp"]
        data["initialValue1e18"] = data["transitions"][0]["beforeValue1e18"]
        with self.assertRaises(EvaluationInputError):
            evaluate_candidate(self.policy(), self.candidate(data))

    def test_fork_mismatch_fails_closed(self) -> None:
        data = copy.deepcopy(self.policy_data)
        data["fork"]["blockNumber"] = "25700001"
        with self.assertRaises(EvaluationInputError):
            evaluate_candidate(self.policy(data), self.candidate())

    def test_unknown_field_discontinuity_order_and_role_mutations_fail_schema(self) -> None:
        unknown = copy.deepcopy(self.candidate_data)
        unknown["unexpected"] = True
        with self.assertRaises(ValidationError):
            self.candidate(unknown)

        discontinuous = copy.deepcopy(self.candidate_data)
        discontinuous["transitions"][1]["beforeValue1e18"] = "1"
        with self.assertRaises(ValidationError):
            self.candidate(discontinuous)

        reordered = copy.deepcopy(self.candidate_data)
        reordered["transitions"][1]["timestamp"] = reordered["transitions"][0]["timestamp"]
        with self.assertRaises(ValidationError):
            self.candidate(reordered)

        wrong_role = copy.deepcopy(self.candidate_data)
        wrong_role["transitions"][-1]["role"] = "history"
        with self.assertRaises(ValidationError):
            self.candidate(wrong_role)

    def test_evaluation_report_is_deterministic(self) -> None:
        self.assertEqual(
            evaluate_candidate(self.policy(), self.candidate()),
            evaluate_candidate(self.policy(), self.candidate()),
        )


if __name__ == "__main__":
    unittest.main()
