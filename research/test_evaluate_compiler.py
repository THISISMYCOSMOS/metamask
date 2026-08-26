from __future__ import annotations

import unittest
from pathlib import Path

from research.evaluate_compiler import evaluate
from research.models import BenchmarkDataset, CompilationOutput, PredictionRecord


ROOT = Path(__file__).resolve().parents[1]


class CompilerBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = BenchmarkDataset.model_validate_json(
            (ROOT / "research" / "data" / "compiler_cases.json").read_text(encoding="utf-8")
        )

    def predictions(self):
        return [PredictionRecord(caseId=case.caseId, output=case.expected) for case in self.dataset.cases]

    def test_exact_predictions_produce_a_perfect_contract_report(self) -> None:
        report = evaluate(self.dataset, self.predictions())
        self.assertEqual(60, report["caseCount"])
        self.assertEqual(1.0, report["exactInvariantAccuracy"])
        self.assertEqual(
            {"truePositive": 48, "trueNegative": 12, "falsePositive": 0, "falseNegative": 0},
            report["confusionMatrix"],
        )

    def test_false_positive_is_visible_in_metrics_and_mismatch_list(self) -> None:
        predictions = self.predictions()
        supported = self.dataset.cases[0].expected
        predictions[-1] = PredictionRecord(caseId=self.dataset.cases[-1].caseId, output=supported)
        report = evaluate(self.dataset, predictions)
        self.assertEqual(1, report["confusionMatrix"]["falsePositive"])
        self.assertEqual(self.dataset.cases[-1].caseId, report["mismatches"][0]["caseId"])

    def test_incomplete_prediction_set_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cover the dataset exactly"):
            evaluate(self.dataset, self.predictions()[:-1])


if __name__ == "__main__":
    unittest.main()
