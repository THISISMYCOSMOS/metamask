from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from research.evaluate_compiler import evaluate
from research.models import BenchmarkDataset, CompilationOutput, PredictionRecord
from research.gemini_invariant_compiler import GeminiConfig, ProviderResponseError
from research.run_gemini_benchmark import run_benchmark


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

    def test_benchmark_run_records_metadata_and_resumes_failed_case(self) -> None:
        by_intent = {case.intentText: case.expected for case in self.dataset.cases}
        failed_intent = self.dataset.cases[10].intentText
        failed_case_id = self.dataset.cases[10].caseId
        should_fail = {"value": True}

        def transport(_url, _headers, body):
            prompt = body["contents"][0]["parts"][0]["text"]
            intent = prompt.split("user intent:\n", 1)[1]
            if intent == failed_intent and should_fail["value"]:
                raise ProviderResponseError("synthetic per-case failure")
            output = by_intent[intent].model_dump(mode="json")
            return {
                "modelVersion": "gemini-test-version",
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"role": "model", "parts": [{"text": __import__("json").dumps(output)}]},
                    }
                ],
            }

        with TemporaryDirectory() as directory:
            root = Path(directory)
            predictions = root / "predictions.jsonl"
            manifest = root / "manifest.json"
            dataset_path = ROOT / "research" / "data" / "compiler_cases.json"
            first, complete = run_benchmark(
                self.dataset,
                dataset_path=dataset_path,
                output_path=predictions,
                manifest_path=manifest,
                config=GeminiConfig("test-key", "gemini-test"),
                transport=transport,
                now=lambda: "2026-09-04T00:00:00Z",
            )
            self.assertFalse(complete)
            self.assertEqual(59, first["completedCount"])
            self.assertEqual(1, first["failedCount"])
            self.assertEqual(60, first["operationalMetrics"]["caseCount"])

            should_fail["value"] = False
            second, complete = run_benchmark(
                self.dataset,
                dataset_path=dataset_path,
                output_path=predictions,
                manifest_path=manifest,
                config=GeminiConfig("test-key", "gemini-test"),
                transport=transport,
                now=lambda: "2026-09-04T00:00:01Z",
            )
            self.assertTrue(complete)
            self.assertEqual(60, second["completedCount"])
            self.assertEqual(0, second["failedCount"])
            self.assertEqual(1.0, second["metrics"]["exactInvariantAccuracy"])
            self.assertEqual(60, len(predictions.read_text(encoding="utf-8").splitlines()))
            self.assertEqual("gemini-test-version", second["caseResults"][failed_case_id]["modelVersion"])


if __name__ == "__main__":
    unittest.main()
