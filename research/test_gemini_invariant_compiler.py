from __future__ import annotations

import json
import unittest
from pathlib import Path

from research.gemini_invariant_compiler import (
    CompilerUnavailableError,
    GeminiConfig,
    GeminiInvariantCompiler,
    ProviderResponseError,
)
from research.models import BenchmarkDataset


ROOT = Path(__file__).resolve().parents[1]


class GeminiInvariantCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dataset = BenchmarkDataset.model_validate_json(
            (ROOT / "research" / "data" / "compiler_cases.json").read_text(encoding="utf-8")
        )
        cls.floor_case = next(case for case in dataset.cases if case.caseId == "pvf-001")
        cls.drawdown_case = next(case for case in dataset.cases if case.caseId == "pdd-002")

    @staticmethod
    def response(output):
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"role": "model", "parts": [{"text": json.dumps(output)}]},
                }
            ]
        }

    def test_missing_key_has_no_offline_fallback(self) -> None:
        with self.assertRaises(CompilerUnavailableError):
            GeminiInvariantCompiler(GeminiConfig(""))

    def test_request_contains_only_intent_and_caller_owned_portfolio_context(self) -> None:
        compiler = GeminiInvariantCompiler(GeminiConfig("test-key"), lambda *_: {})
        request = compiler.build_request(self.drawdown_case)
        prompt = request["contents"][0]["parts"][0]["text"]
        self.assertIn(self.drawdown_case.intentText, prompt)
        self.assertIn(self.drawdown_case.context.currentPortfolioValue1e18, prompt)
        self.assertNotIn("wallet", prompt.lower())
        self.assertEqual(0, request["generationConfig"]["temperature"])

    def test_valid_structured_output_reaches_the_strict_local_contract(self) -> None:
        expected = self.floor_case.expected
        compiler = GeminiInvariantCompiler(
            GeminiConfig("test-key"),
            lambda *_: self.response(expected.model_dump(mode="json")),
        )
        self.assertEqual(expected, compiler.compile(self.floor_case))

    def test_malformed_or_wrong_shape_output_fails_closed(self) -> None:
        compiler = GeminiInvariantCompiler(
            GeminiConfig("test-key"),
            lambda *_: self.response({"supported": True}),
        )
        with self.assertRaises(ProviderResponseError):
            compiler.compile(self.floor_case)

    def test_transport_failure_is_not_converted_to_a_fixture(self) -> None:
        def fail(*_):
            raise CompilerUnavailableError("provider unavailable")

        compiler = GeminiInvariantCompiler(GeminiConfig("test-key"), fail)
        with self.assertRaises(CompilerUnavailableError):
            compiler.compile(self.floor_case)


if __name__ == "__main__":
    unittest.main()
