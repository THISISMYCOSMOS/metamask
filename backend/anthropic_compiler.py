"""Anthropic Messages API adapter for production policy proposals.

There is intentionally no offline fixture path in this module.  Tests inject a
transport; the production default uses urllib against the Messages API.

The request uses ``output_config.format`` with a JSON schema.  That parameter is
generally available and takes **no** beta header; sending an obsolete
``anthropic-beta`` value is at best ignored and at worst an error, so none is
sent.

Everything the model returns is treated as untrusted.  Provider identity,
compiler identity, chain, wallet, token and identifiers all come from
configuration or from the caller's :class:`core.models.CompileRequest`.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.models import CompilationResult, CompileRequest, CompilerIdentity

from .compiler_contract import (
    MAX_OUTPUT_TOKENS,
    POLICY_OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    CompilerUnavailableError,
    ProviderResponseError,
    Transport,
    compilation_context,
)
from .policy_models import ContractError, PolicyFloorOutput, assemble_compilation


MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
@dataclass(frozen=True)
class AnthropicConfig:
    api_key: str
    model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AnthropicConfig":
        source = os.environ if env is None else env
        api_key = source.get("ANTHROPIC_API_KEY", "").strip()
        model = source.get("ANTHROPIC_MODEL", "").strip()
        if not api_key or not model:
            raise CompilerUnavailableError(
                "ANTHROPIC_API_KEY and ANTHROPIC_MODEL are both required; no offline fallback exists"
            )
        return cls(api_key=api_key, model=model)


def _urllib_transport(url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> Mapping[str, Any]:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=encoded, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310: fixed Anthropic API endpoint
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CompilerUnavailableError("Anthropic policy compilation failed; no offline fallback exists") from exc
    if not isinstance(parsed, dict):
        raise ProviderResponseError("Anthropic returned a non-object response")
    return parsed


class AnthropicPolicyCompiler:
    """Compile one :class:`CompileRequest` into one :class:`CompilationResult`."""

    def __init__(self, config: AnthropicConfig, transport: Transport | None = None) -> None:
        if not config.api_key.strip() or not config.model.strip():
            raise CompilerUnavailableError("Anthropic API key and model are required")
        self._config = config
        self._transport = transport or _urllib_transport

    @property
    def compiler_identity(self) -> CompilerIdentity:
        """Compiler identity from configuration; model output can never set it."""
        return CompilerIdentity(provider="anthropic", model=self._config.model)

    def build_request(self, request: CompileRequest) -> dict[str, Any]:
        if not isinstance(request, CompileRequest):
            raise ContractError("compilation requires the shared CompileRequest model")
        context = compilation_context(request)
        return {
            "model": self._config.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": context}],
            "output_config": {"format": {"type": "json_schema", "schema": POLICY_OUTPUT_SCHEMA}},
        }

    def compile(self, request: CompileRequest) -> CompilationResult:
        """Call the provider and assemble the result, or fail closed.

        Never returns a fixture and never returns a proposal the caller's
        request did not authorize.
        """
        body = self.build_request(request)
        headers = {
            "content-type": "application/json",
            "x-api-key": self._config.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        try:
            response = self._transport(MESSAGES_URL, headers, body)
        except (CompilerUnavailableError, ProviderResponseError):
            raise
        except Exception as exc:  # Transport failures must never trigger a fallback.
            raise CompilerUnavailableError("Anthropic policy compilation failed; no offline fallback exists") from exc
        output = self._parse_output(response)
        return assemble_compilation(request, output, compiler=self.compiler_identity)

    def _parse_output(self, response: Mapping[str, Any]) -> PolicyFloorOutput:
        if not isinstance(response, Mapping):
            raise ProviderResponseError("Anthropic response was not an object")
        if response.get("type") != "message" or response.get("role") != "assistant":
            raise ProviderResponseError("Anthropic response is not an assistant message")
        if response.get("model") != self._config.model:
            raise ProviderResponseError("Anthropic response model does not match the configured compiler identity")

        stop_reason = response.get("stop_reason")
        if stop_reason == "max_tokens":
            raise ProviderResponseError("Anthropic response was truncated at max_tokens; output is incomplete")
        if stop_reason == "refusal":
            raise ProviderResponseError("Anthropic refused to produce structured output")
        if stop_reason != "end_turn":
            raise ProviderResponseError(f"Anthropic response did not complete normally: stop_reason={stop_reason!r}")

        content = response.get("content")
        if not isinstance(content, list) or len(content) != 1:
            raise ProviderResponseError("Anthropic response must contain exactly one structured text block")
        block = content[0]
        if not isinstance(block, Mapping) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise ProviderResponseError("Anthropic response lacks structured text output")
        try:
            parsed = json.loads(block["text"])
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Anthropic structured output was not valid JSON") from exc
        try:
            return PolicyFloorOutput.parse(parsed)
        except ContractError as exc:
            raise ProviderResponseError(f"Anthropic output violated the policy output contract: {exc}") from exc
