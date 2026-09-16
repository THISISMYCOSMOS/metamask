from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from core.canonical import canonical_sha256
from core.models import CompileRequest, CompilerIdentity

from backend.compiler_contract import POLICY_OUTPUT_SCHEMA, CompilerUnavailableError, ProviderResponseError
from backend.gemini_compiler import (
    DEFAULT_GEMINI_MODEL,
    GeminiConfig,
    GeminiPolicyCompiler,
)
from backend.policy_models import ContractError
from backend.policy_service import PolicyProposalService


WALLET = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
TOKEN = "0x5fbdb2315678afecb367f032d93f642f64180aa3"
VALID_OUTPUT = {
    "supported": True,
    "minimumBalanceBaseUnits": "20000000",
    "rationales": ["USDC 잔액 하한을 base-unit으로 고정합니다."],
    "assumptions": ["20개는 6 decimals 기준으로 해석했습니다."],
    "unsupportedItems": [],
}


def compile_request(**overrides: object) -> CompileRequest:
    data: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "policy-compile-request",
        "requestId": "gemini-request-1",
        "proposalId": "gemini-proposal-1",
        "policyId": "gemini-usdc-floor",
        "intentText": "USDC를 20개 이상 남겨줘",
        "chainId": 31337,
        "walletAddress": WALLET,
        "tokenAddress": TOKEN,
        "tokenSymbol": "USDC",
        "tokenDecimals": 6,
    }
    data.update(overrides)
    return CompileRequest.model_validate(data)


def gemini_response(output: dict | None = None, **candidate_overrides: object) -> dict:
    candidate: dict = {
        "finishReason": "STOP",
        "content": {
            "role": "model",
            "parts": [{"text": json.dumps(VALID_OUTPUT if output is None else output)}],
        },
    }
    candidate.update(candidate_overrides)
    return {
        "candidates": [candidate],
        "modelVersion": "gemini-3.5-flash-lite",
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
    }


class GeminiConfigurationTests(unittest.TestCase):
    def test_api_key_is_required_and_free_tier_model_is_defaulted(self) -> None:
        with self.assertRaisesRegex(CompilerUnavailableError, "GEMINI_API_KEY"):
            GeminiConfig.from_env({})
        config = GeminiConfig.from_env({"GEMINI_API_KEY": "test-key"})
        self.assertEqual(config.model, DEFAULT_GEMINI_MODEL)

    def test_invalid_model_cannot_change_the_fixed_api_endpoint(self) -> None:
        for model in ("", "../other", "gemini/model", "gemini?key=leak"):
            with self.subTest(model=model):
                with self.assertRaisesRegex(CompilerUnavailableError, "GEMINI_MODEL"):
                    GeminiConfig.from_env({"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": model})

    def test_policy_service_defaults_to_gemini(self) -> None:
        calls: list[tuple] = []

        def transport(url, headers, body):
            calls.append((url, headers, body))
            return gemini_response()

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(CompilerUnavailableError, "GEMINI_API_KEY"):
                PolicyProposalService.from_env(transport=transport)

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True):
            service = PolicyProposalService.from_env(transport=transport)
            proposal = service.compile(compile_request()).approvable_proposal
        self.assertEqual(proposal.compiler.provider, "google-gemini")
        self.assertEqual(len(calls), 1)


class GeminiRequestTests(unittest.TestCase):
    def compiler(self, response: dict | None = None) -> tuple[GeminiPolicyCompiler, list[tuple]]:
        calls: list[tuple] = []

        def transport(url, headers, body):
            calls.append((url, headers, body))
            return gemini_response() if response is None else response

        return GeminiPolicyCompiler(GeminiConfig("test-key"), transport), calls

    def test_request_uses_api_key_header_and_json_schema(self) -> None:
        compiler, calls = self.compiler()
        compiler.compile(compile_request())
        self.assertEqual(len(calls), 1)
        url, headers, body = calls[0]
        self.assertTrue(url.endswith(f"/models/{DEFAULT_GEMINI_MODEL}:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "test-key")
        self.assertNotIn("test-key", url)
        config = body["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["responseJsonSchema"], POLICY_OUTPUT_SCHEMA)
        self.assertEqual(config["temperature"], 0)

    def test_prompt_excludes_caller_owned_chain_wallet_token_and_identifiers(self) -> None:
        compiler, calls = self.compiler()
        request = compile_request()
        compiler.compile(request)
        sent = json.dumps(calls[0][2], ensure_ascii=False)
        for fact in (request.walletAddress, request.tokenAddress, str(request.chainId), request.policyId):
            self.assertNotIn(fact, sent)
        self.assertIn(request.intentText, sent)
        self.assertIn(request.tokenSymbol, sent)
        self.assertIn(str(request.tokenDecimals), sent)

    def test_transport_failure_has_no_fixture_fallback(self) -> None:
        def failing_transport(url, headers, body):
            raise OSError("network unavailable")

        compiler = GeminiPolicyCompiler(GeminiConfig("test-key"), failing_transport)
        with self.assertRaisesRegex(CompilerUnavailableError, "no offline fallback"):
            compiler.compile(compile_request())

    def test_non_request_input_is_refused_before_transport(self) -> None:
        compiler, _ = self.compiler()
        with self.assertRaisesRegex(ContractError, "CompileRequest"):
            compiler.compile({"intentText": "USDC를 남겨줘"})  # type: ignore[arg-type]


class GeminiResponseTests(unittest.TestCase):
    def compile_with(self, response: dict):
        compiler = GeminiPolicyCompiler(GeminiConfig("test-key"), lambda u, h, b: response)
        return compiler.compile(compile_request())

    def test_valid_response_becomes_the_shared_proposal(self) -> None:
        result = self.compile_with(gemini_response())
        proposal = result.approvable_proposal
        self.assertEqual(proposal.compiler, CompilerIdentity(provider="google-gemini", model=DEFAULT_GEMINI_MODEL))
        self.assertEqual(result.proposalSha256, canonical_sha256(proposal))
        self.assertEqual(proposal.policy.assetBalanceFloor, "20000000")

    def test_blocked_missing_multiple_and_incomplete_candidates_fail_closed(self) -> None:
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        with self.assertRaisesRegex(ProviderResponseError, "blocked"):
            self.compile_with(blocked)
        for response, pattern in (
            ({"candidates": []}, "exactly one candidate"),
            ({"candidates": [gemini_response()["candidates"][0]] * 2}, "exactly one candidate"),
            (gemini_response(finishReason="MAX_TOKENS"), "did not complete normally"),
            (gemini_response(finishReason="SAFETY"), "did not complete normally"),
        ):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ProviderResponseError, pattern):
                    self.compile_with(response)

    def test_wrong_role_parts_malformed_json_and_schema_violation_fail_closed(self) -> None:
        with self.assertRaisesRegex(ProviderResponseError, "not a model response"):
            self.compile_with(gemini_response(content={"role": "user", "parts": []}))
        with self.assertRaisesRegex(ProviderResponseError, "exactly one structured text part"):
            self.compile_with(gemini_response(content={"role": "model", "parts": []}))
        with self.assertRaisesRegex(ProviderResponseError, "not valid JSON"):
            self.compile_with(gemini_response(content={"role": "model", "parts": [{"text": "{bad"}]}))
        invalid = copy.deepcopy(VALID_OUTPUT)
        invalid["minimumBalanceBaseUnits"] = 20_000_000
        with self.assertRaisesRegex(ProviderResponseError, "violated the policy output contract"):
            self.compile_with(gemini_response(invalid))


if __name__ == "__main__":
    unittest.main()
