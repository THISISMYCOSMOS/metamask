from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from evaluate_invariants import (
    EvaluationInputError,
    PortfolioPoint,
    evaluate,
    evaluate_points,
    normalize_phase1_trace,
)
from invariant_models import UINT256_MAX, InvariantPolicy, canonical_policy_sha256
from models import Trace


ROOT = Path(__file__).resolve().parents[1]


class InvariantEvaluatorStressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_data = json.loads((ROOT / "specs" / "phase1-demo-invariants.json").read_text(encoding="utf-8"))
        cls.trace_data = json.loads((ROOT / "traces" / "cumulative-loss.json").read_text(encoding="utf-8"))

    def policy(self, data: dict | None = None) -> InvariantPolicy:
        return InvariantPolicy.model_validate(copy.deepcopy(data or self.policy_data))

    def trace(self, data: dict | None = None) -> Trace:
        return Trace.model_validate(copy.deepcopy(data or self.trace_data))

    def test_g3_golden_attack_is_rejected_by_both_invariants(self) -> None:
        report = evaluate(self.policy(), self.trace())
        self.assertFalse(report["accepted"])
        self.assertEqual([False, False], [item["passed"] for item in report["evaluations"]])

    def test_inclusive_boundaries_accept_and_one_unit_tighter_rejects(self) -> None:
        base_policy = self.policy()
        points = normalize_phase1_trace(self.trace())
        observed_floor = min([points[0].before_value1e18] + [point.after_value1e18 for point in points])
        observed_loss = int(
            evaluate_points(base_policy, points)[1]["observedMaximumLossValue1e18"]
        )

        boundary_data = copy.deepcopy(self.policy_data)
        boundary_data["invariants"][0]["floorValue1e18"] = str(observed_floor)
        boundary_data["invariants"][1]["maxLossValue1e18"] = str(observed_loss)
        self.assertTrue(all(item["passed"] for item in evaluate_points(self.policy(boundary_data), points)))

        tighter_data = copy.deepcopy(boundary_data)
        tighter_data["invariants"][0]["floorValue1e18"] = str(observed_floor + 1)
        tighter_data["invariants"][1]["maxLossValue1e18"] = str(observed_loss - 1)
        self.assertTrue(all(not item["passed"] for item in evaluate_points(self.policy(tighter_data), points)))
        self.assertEqual(base_policy.schemaVersion, 1)

    def test_policy_fails_closed_on_unknown_field_duplicate_id_zero_window_and_overflow(self) -> None:
        unknown = copy.deepcopy(self.policy_data)
        unknown["unexpected"] = True
        with self.assertRaises(ValidationError):
            self.policy(unknown)

        duplicate = copy.deepcopy(self.policy_data)
        duplicate["invariants"][1]["id"] = duplicate["invariants"][0]["id"]
        with self.assertRaises(ValidationError):
            self.policy(duplicate)

        zero_window = copy.deepcopy(self.policy_data)
        zero_window["invariants"][1]["windowSeconds"] = "0"
        with self.assertRaises(ValidationError):
            self.policy(zero_window)

        overflow = copy.deepcopy(self.policy_data)
        overflow["invariants"][1]["maxLossValue1e18"] = str(UINT256_MAX + 1)
        with self.assertRaises(ValidationError):
            self.policy(overflow)

    def test_fork_mismatch_fails_closed(self) -> None:
        data = copy.deepcopy(self.policy_data)
        data["fork"]["blockNumber"] = "25700001"
        with self.assertRaises(EvaluationInputError):
            evaluate(self.policy(data), self.trace())

    def test_joint_policy_and_trace_fork_tampering_fails_closed(self) -> None:
        policy_data = copy.deepcopy(self.policy_data)
        trace_data = copy.deepcopy(self.trace_data)
        policy_data["fork"]["blockNumber"] = "25700001"
        trace_data["hashed"]["fork"]["blockNumber"] = "25700001"
        with self.assertRaises(EvaluationInputError):
            evaluate(self.policy(policy_data), self.trace(trace_data))

    def test_trace_state_tampering_and_discontinuity_fail_closed(self) -> None:
        state_tamper = copy.deepcopy(self.trace_data)
        state_tamper["hashed"]["states"][0]["after"]["usdc"] = "9500000001"
        with self.assertRaises(EvaluationInputError):
            normalize_phase1_trace(self.trace(state_tamper))

        discontinuity = copy.deepcopy(self.trace_data)
        discontinuity["hashed"]["steps"][1]["usdcBefore"] = "9499999999"
        discontinuity["hashed"]["steps"][1]["usdcAfter"] = "8999999999"
        with self.assertRaises(EvaluationInputError):
            normalize_phase1_trace(self.trace(discontinuity))

    def test_trace_order_result_and_length_tampering_fail_closed(self) -> None:
        reversed_timestamp = copy.deepcopy(self.trace_data)
        reversed_timestamp["hashed"]["steps"][1]["timestamp"] = reversed_timestamp["hashed"]["steps"][0]["timestamp"]
        with self.assertRaises(EvaluationInputError):
            normalize_phase1_trace(self.trace(reversed_timestamp))

        reversed_block = copy.deepcopy(self.trace_data)
        reversed_block["hashed"]["steps"][1]["blockNumber"] = reversed_block["hashed"]["steps"][0]["blockNumber"]
        with self.assertRaises(EvaluationInputError):
            normalize_phase1_trace(self.trace(reversed_block))

        result_tamper = copy.deepcopy(self.trace_data)
        result_tamper["hashed"]["result"]["startingPortfolio"]["usdc"] = "9999999999"
        with self.assertRaises(EvaluationInputError):
            normalize_phase1_trace(self.trace(result_tamper))

        missing_state = copy.deepcopy(self.trace_data)
        missing_state["hashed"]["states"].pop()
        with self.assertRaises(ValidationError):
            self.trace(missing_state)

    def test_pure_evaluator_rejects_empty_negative_and_unordered_points(self) -> None:
        policy = self.policy()
        with self.assertRaises(EvaluationInputError):
            evaluate_points(policy, [])
        with self.assertRaises(EvaluationInputError):
            evaluate_points(policy, [PortfolioPoint(0, 100, -1, 0)])
        with self.assertRaises(EvaluationInputError):
            evaluate_points(
                policy,
                [PortfolioPoint(0, 100, 30_000, 29_500), PortfolioPoint(1, 100, 29_500, 29_000)],
            )

    def test_pure_evaluator_accepts_benign_continuous_control(self) -> None:
        points = [
            PortfolioPoint(step_index=0, timestamp=100, before_value1e18=30_000, after_value1e18=29_500),
            PortfolioPoint(step_index=1, timestamp=200, before_value1e18=29_500, after_value1e18=29_000),
        ]
        data = copy.deepcopy(self.policy_data)
        data["invariants"][0]["floorValue1e18"] = "29000"
        data["invariants"][1]["windowSeconds"] = "1000"
        data["invariants"][1]["maxLossValue1e18"] = "1000"
        evaluations = evaluate_points(self.policy(data), points)
        self.assertTrue(all(item["passed"] for item in evaluations))

    def test_policy_hash_is_key_order_independent(self) -> None:
        reordered = {key: self.policy_data[key] for key in reversed(self.policy_data)}
        self.assertEqual(canonical_policy_sha256(self.policy()), canonical_policy_sha256(self.policy(reordered)))

    def test_report_is_deterministic(self) -> None:
        first = evaluate(self.policy(), self.trace())
        second = evaluate(self.policy(), self.trace())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
