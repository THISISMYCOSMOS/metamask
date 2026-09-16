from __future__ import annotations

import copy
import json
import unittest

from core.canonical import canonical_sha256
from core.models import CompilationResult, CompileRequest, CompilerIdentity, RevisedPolicyProposal
from core.policy_binding import PolicyApprovalError, approval_confirmation, approve

from backend.anthropic_compiler import (
    ANTHROPIC_VERSION,
    POLICY_OUTPUT_SCHEMA,
    AnthropicConfig,
    AnthropicPolicyCompiler,
    CompilerUnavailableError,
    ProviderResponseError,
)
from backend.policy_models import ContractError, PolicyFloorOutput, assemble_compilation
from backend.policy_service import PolicyRevisionError, revise_asset_balance_floor


MODEL = "claude-test"
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
        "requestId": "request-1",
        "proposalId": "proposal-1",
        "policyId": "usdc-floor",
        "intentText": "USDC를 20개 이상 남겨줘",
        "chainId": 31337,
        "walletAddress": WALLET,
        "tokenAddress": TOKEN,
        "tokenSymbol": "USDC",
        "tokenDecimals": 6,
    }
    data.update(overrides)
    return CompileRequest.model_validate(data)


def provider_response(output: dict | None = None, **overrides: object) -> dict:
    response: dict = {
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(VALID_OUTPUT if output is None else output)}],
    }
    response.update(overrides)
    return response


class CompilerRequestTests(unittest.TestCase):
    def compiler(self, response: dict | None = None) -> tuple[AnthropicPolicyCompiler, list[tuple]]:
        calls: list[tuple] = []

        def transport(url, headers, body):
            calls.append((url, headers, body))
            return provider_response() if response is None else response

        return AnthropicPolicyCompiler(AnthropicConfig("test-key", MODEL), transport), calls

    def test_missing_key_or_model_fails_closed_before_transport(self) -> None:
        with self.assertRaisesRegex(CompilerUnavailableError, "no offline fallback"):
            AnthropicConfig.from_env({"ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": MODEL})
        with self.assertRaisesRegex(CompilerUnavailableError, "no offline fallback"):
            AnthropicConfig.from_env({"ANTHROPIC_API_KEY": "key", "ANTHROPIC_MODEL": ""})

    def test_request_uses_json_schema_output_and_sends_no_beta_header(self) -> None:
        compiler, calls = self.compiler()
        compiler.compile(compile_request())
        self.assertEqual(len(calls), 1)
        _, headers, body = calls[0]
        self.assertEqual(headers["anthropic-version"], ANTHROPIC_VERSION)
        self.assertNotIn("anthropic-beta", headers)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(body["output_config"]["format"]["schema"], POLICY_OUTPUT_SCHEMA)

    def test_prompt_never_offers_the_model_chain_wallet_or_identifiers(self) -> None:
        compiler, calls = self.compiler()
        request = compile_request()
        compiler.compile(request)
        _, _, body = calls[0]
        sent = json.dumps(body, ensure_ascii=False)
        for secret in (request.walletAddress, request.tokenAddress, str(request.chainId), request.policyId):
            self.assertNotIn(secret, sent)
        self.assertIn(request.intentText, sent)
        self.assertIn("USDC", sent)

    def test_transport_failure_is_not_converted_to_fixture(self) -> None:
        def failing_transport(url, headers, body):
            raise OSError("network unavailable")

        compiler = AnthropicPolicyCompiler(AnthropicConfig("test-key", MODEL), failing_transport)
        with self.assertRaisesRegex(CompilerUnavailableError, "no offline fallback"):
            compiler.compile(compile_request())

    def test_non_request_input_is_refused(self) -> None:
        compiler, _ = self.compiler()
        with self.assertRaisesRegex(ContractError, "CompileRequest"):
            compiler.compile({"intentText": "USDC를 20개 이상 남겨줘"})  # type: ignore[arg-type]


class ProviderResponseAnomalyTests(unittest.TestCase):
    def compile_with(self, response: dict) -> CompilationResult:
        compiler = AnthropicPolicyCompiler(AnthropicConfig("test-key", MODEL), lambda u, h, b: response)
        return compiler.compile(compile_request())

    def test_truncated_refused_and_abnormal_stops_are_rejected(self) -> None:
        for stop_reason, pattern in (
            ("max_tokens", "truncated"),
            ("refusal", "refused"),
            ("tool_use", "did not complete normally"),
            (None, "did not complete normally"),
        ):
            with self.subTest(stop_reason=stop_reason):
                with self.assertRaisesRegex(ProviderResponseError, pattern):
                    self.compile_with(provider_response(stop_reason=stop_reason))

    def test_provider_identity_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderResponseError, "does not match the configured compiler identity"):
            self.compile_with(provider_response(model="some-other-model"))
        with self.assertRaisesRegex(ProviderResponseError, "not an assistant message"):
            self.compile_with(provider_response(role="user"))
        with self.assertRaisesRegex(ProviderResponseError, "not an assistant message"):
            self.compile_with(provider_response(type="error"))

    def test_multiple_and_non_text_blocks_are_rejected(self) -> None:
        text_block = {"type": "text", "text": json.dumps(VALID_OUTPUT)}
        with self.assertRaisesRegex(ProviderResponseError, "exactly one structured text block"):
            self.compile_with(provider_response(content=[text_block, text_block]))
        with self.assertRaisesRegex(ProviderResponseError, "exactly one structured text block"):
            self.compile_with(provider_response(content=[]))
        with self.assertRaisesRegex(ProviderResponseError, "lacks structured text output"):
            self.compile_with(provider_response(content=[{"type": "tool_use", "id": "t", "input": {}}]))

    def test_malformed_json_and_schema_violations_are_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderResponseError, "not valid JSON"):
            self.compile_with(provider_response(content=[{"type": "text", "text": "{not json"}]))
        extra = copy.deepcopy(VALID_OUTPUT)
        extra["compiler"] = {"provider": "anthropic", "model": "attacker-chosen"}
        with self.assertRaisesRegex(ProviderResponseError, "violated the policy output contract"):
            self.compile_with(provider_response(extra))
        wrong_type = copy.deepcopy(VALID_OUTPUT)
        wrong_type["minimumBalanceBaseUnits"] = 20000000
        with self.assertRaisesRegex(ProviderResponseError, "violated the policy output contract"):
            self.compile_with(provider_response(wrong_type))


class AssemblyTests(unittest.TestCase):
    def compile(self, output: dict) -> CompilationResult:
        compiler = AnthropicPolicyCompiler(AnthropicConfig("test-key", MODEL), lambda u, h, b: provider_response(output))
        return compiler.compile(compile_request())

    def test_supported_output_becomes_one_shared_proposal(self) -> None:
        request = compile_request()
        result = self.compile(VALID_OUTPUT)
        proposal = result.approvable_proposal
        self.assertTrue(result.supported)
        self.assertEqual(result.requestSha256, canonical_sha256(request))
        self.assertEqual(proposal.requestSha256, canonical_sha256(request))
        self.assertEqual(result.proposalSha256, canonical_sha256(proposal))
        self.assertEqual(proposal.intentText, request.intentText)
        self.assertEqual(proposal.compiler, CompilerIdentity(provider="anthropic", model=MODEL))
        self.assertEqual(proposal.policy.chainId, request.chainId)
        self.assertEqual(proposal.policy.walletAddress, WALLET)
        self.assertEqual(proposal.policy.tokenAddress, TOKEN)
        self.assertEqual(proposal.policy.policyId, request.policyId)
        self.assertEqual(proposal.policy.assetBalanceFloor, "20000000")
        self.assertEqual(proposal.unsupportedItems, [])

    def test_mixed_supported_and_unsupported_intent_is_not_approvable(self) -> None:
        mixed = copy.deepcopy(VALID_OUTPUT)
        mixed["unsupportedItems"] = ["하루 500 USDC 지출 한도는 표현할 수 없습니다."]
        result = self.compile(mixed)
        self.assertFalse(result.supported)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.proposalSha256)
        self.assertIn("UNSUPPORTED_ITEMS_PRESENT", result.reasonCodes)
        self.assertEqual(result.unsupportedItems, mixed["unsupportedItems"])
        with self.assertRaisesRegex(ValueError, "not approvable"):
            result.approvable_proposal

    def test_model_reported_unsupported_is_not_approvable(self) -> None:
        declined = copy.deepcopy(VALID_OUTPUT)
        declined["supported"] = False
        declined["unsupportedItems"] = ["요청을 잔액 하한으로 표현할 수 없습니다."]
        result = self.compile(declined)
        self.assertFalse(result.supported)
        self.assertIn("MODEL_REPORTED_UNSUPPORTED", result.reasonCodes)

    def test_missing_or_ambiguous_floor_is_not_approvable(self) -> None:
        for value, code in (
            (None, "MINIMUM_BALANCE_MISSING"),
            ("", "MINIMUM_BALANCE_NOT_A_BASE_UNIT_INTEGER"),
            ("20.5", "MINIMUM_BALANCE_NOT_A_BASE_UNIT_INTEGER"),
            ("-1", "MINIMUM_BALANCE_NOT_A_BASE_UNIT_INTEGER"),
            ("020", "MINIMUM_BALANCE_NOT_A_BASE_UNIT_INTEGER"),
            (str(1 << 256), "MINIMUM_BALANCE_EXCEEDS_UINT256"),
        ):
            with self.subTest(value=value):
                output = copy.deepcopy(VALID_OUTPUT)
                output["minimumBalanceBaseUnits"] = value
                result = self.compile(output)
                self.assertFalse(result.supported)
                self.assertIsNone(result.proposal)
                self.assertIn(code, result.reasonCodes)

    def test_a_proposal_without_a_rationale_is_not_approvable(self) -> None:
        output = copy.deepcopy(VALID_OUTPUT)
        output["rationales"] = []
        result = self.compile(output)
        self.assertFalse(result.supported)
        self.assertIn("NO_RATIONALE_FOR_POLICY", result.reasonCodes)

    def test_compiler_identity_must_come_from_code(self) -> None:
        with self.assertRaisesRegex(ContractError, "never by model output"):
            assemble_compilation(
                compile_request(),
                PolicyFloorOutput.parse(VALID_OUTPUT),
                compiler={"provider": "anthropic", "model": MODEL},  # type: ignore[arg-type]
            )


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        compiler = AnthropicPolicyCompiler(AnthropicConfig("test-key", MODEL), lambda u, h, b: provider_response())
        self.request = compile_request()
        self.result = compiler.compile(self.request)
        self.proposal = self.result.approvable_proposal

    def test_the_only_approval_is_over_the_shared_proposal_hash(self) -> None:
        confirmation = approval_confirmation(self.proposal)
        self.assertEqual(confirmation, f"APPROVE {self.result.proposalSha256}")
        envelope = approve(
            self.proposal,
            approval_id="approval-1",
            approved_by="wallet-owner",
            confirmation=confirmation,
            request=self.request,
        )
        self.assertEqual(envelope.proposalSha256, self.result.proposalSha256)
        self.assertEqual(envelope.policySha256, self.proposal.policySha256)
        self.assertEqual(envelope.proposal, self.proposal)

    def test_edited_proposal_and_inexact_confirmation_are_refused(self) -> None:
        confirmation = approval_confirmation(self.proposal)
        for bad in (confirmation + " ", confirmation.lower(), confirmation.replace("APPROVE", "approve"), "APPROVE"):
            with self.subTest(confirmation=bad):
                with self.assertRaises(PolicyApprovalError):
                    approve(self.proposal, approval_id="approval-1", approved_by="owner", confirmation=bad)

        edited = self.proposal.model_copy(update={"intentText": self.proposal.intentText + " 그리고 더"})
        with self.assertRaises(PolicyApprovalError):
            approve(edited, approval_id="approval-1", approved_by="owner", confirmation=confirmation)

    def test_approval_refuses_a_proposal_bound_to_a_different_request(self) -> None:
        other = compile_request(walletAddress="0x" + "11" * 20)
        with self.assertRaisesRegex(PolicyApprovalError, "does not bind the exact compile request"):
            approve(
                self.proposal,
                approval_id="approval-1",
                approved_by="owner",
                confirmation=approval_confirmation(self.proposal),
                request=other,
            )

    def test_user_revision_preserves_the_source_hash_and_requires_a_fresh_approval(self) -> None:
        source_hash = canonical_sha256(self.proposal)
        revised = revise_asset_balance_floor(
            self.proposal,
            source_proposal_sha256=source_hash,
            asset_balance_floor="25000000",
            revised_by="wallet-owner",
        )
        self.assertIsInstance(revised, RevisedPolicyProposal)
        self.assertEqual(revised.revision.sourceProposalSha256, source_hash)
        self.assertEqual(revised.revision.assetBalanceFloorBefore, "20000000")
        self.assertEqual(revised.revision.assetBalanceFloorAfter, "25000000")
        self.assertEqual(revised.policy.assetBalanceFloor, "25000000")
        self.assertNotEqual(canonical_sha256(revised), source_hash)
        self.assertIn("사용자가 검토 후", revised.rationales[0])

        with self.assertRaises(PolicyApprovalError):
            approve(
                revised,
                approval_id="approval-revised",
                approved_by="wallet-owner",
                confirmation=approval_confirmation(self.proposal),
                request=self.request,
            )
        envelope = approve(
            revised,
            approval_id="approval-revised",
            approved_by="wallet-owner",
            confirmation=approval_confirmation(revised),
            request=self.request,
        )
        self.assertEqual(envelope.proposal, revised)
        self.assertEqual(envelope.proposalSha256, canonical_sha256(revised))

    def test_user_revision_fails_closed_on_stale_hash_invalid_uint_or_no_change(self) -> None:
        source_hash = canonical_sha256(self.proposal)
        for source, floor, pattern in (
            ("0x" + "00" * 32, "25000000", "source proposal hash"),
            (source_hash, "020000000", "canonical decimal uint256"),
            (source_hash, "20.5", "canonical decimal uint256"),
            (source_hash, "20000000", "must differ"),
        ):
            with self.subTest(source=source, floor=floor):
                with self.assertRaisesRegex(PolicyRevisionError, pattern):
                    revise_asset_balance_floor(
                        self.proposal,
                        source_proposal_sha256=source,
                        asset_balance_floor=floor,
                        revised_by="wallet-owner",
                    )


if __name__ == "__main__":
    unittest.main()
