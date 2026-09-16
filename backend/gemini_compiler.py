"""Gemini Developer API adapter for free-tier policy compilation.

The default model is the stable, low-cost ``gemini-3.5-flash-lite``. Production
uses the API-key based Gemini Developer API, not Vertex AI, so no GCP billing,
project or IAM configuration is required for the free tier.

There is deliberately no fixture fallback. All model output remains untrusted
and must pass both Gemini's JSON Schema constraint and the local Pydantic
contract before it can become a proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
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


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str = DEFAULT_GEMINI_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GeminiConfig":
        source = os.environ if env is None else env
        api_key = source.get("GEMINI_API_KEY", "").strip()
        model = source.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        if not api_key:
            raise CompilerUnavailableError("GEMINI_API_KEY is required; no offline fallback exists")
        if not MODEL_PATTERN.fullmatch(model):
            raise CompilerUnavailableError("GEMINI_MODEL is invalid; no offline fallback exists")
        return cls(api_key=api_key, model=model)


def _urllib_transport(url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> Mapping[str, Any]:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=encoded, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310: fixed Google Gemini API endpoint
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CompilerUnavailableError("Gemini policy compilation failed; no offline fallback exists") from exc
    if not isinstance(parsed, dict):
        raise ProviderResponseError("Gemini returned a non-object response")
    return parsed


class GeminiPolicyCompiler:
    """Compile one request using Gemini structured JSON output."""

    def __init__(self, config: GeminiConfig, transport: Transport | None = None) -> None:
        if not config.api_key.strip() or not MODEL_PATTERN.fullmatch(config.model):
            raise CompilerUnavailableError("Gemini API key and a valid model are required")
        self._config = config
        self._transport = transport or _urllib_transport

    @property
    def compiler_identity(self) -> CompilerIdentity:
        return CompilerIdentity(provider="google-gemini", model=self._config.model)

    def build_request(self, request: CompileRequest) -> dict[str, Any]:
        if not isinstance(request, CompileRequest):
            raise ContractError("compilation requires the shared CompileRequest model")
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": compilation_context(request)}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseJsonSchema": POLICY_OUTPUT_SCHEMA,
            },
        }

    def compile(self, request: CompileRequest) -> CompilationResult:
        body = self.build_request(request)
        url = GENERATE_CONTENT_URL.format(model=self._config.model)
        headers = {"content-type": "application/json", "x-goog-api-key": self._config.api_key}
        try:
            response = self._transport(url, headers, body)
        except (CompilerUnavailableError, ProviderResponseError):
            raise
        except Exception as exc:
            raise CompilerUnavailableError("Gemini policy compilation failed; no offline fallback exists") from exc
        output = self._parse_output(response)
        return assemble_compilation(request, output, compiler=self.compiler_identity)

    def _parse_output(self, response: Mapping[str, Any]) -> PolicyFloorOutput:
        if not isinstance(response, Mapping):
            raise ProviderResponseError("Gemini response was not an object")
        prompt_feedback = response.get("promptFeedback")
        if isinstance(prompt_feedback, Mapping) and prompt_feedback.get("blockReason"):
            raise ProviderResponseError("Gemini blocked the policy compilation prompt")

        candidates = response.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], Mapping):
            raise ProviderResponseError("Gemini response must contain exactly one candidate")
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        if finish_reason != "STOP":
            raise ProviderResponseError(f"Gemini response did not complete normally: finishReason={finish_reason!r}")

        content = candidate.get("content")
        if not isinstance(content, Mapping) or content.get("role") != "model":
            raise ProviderResponseError("Gemini candidate is not a model response")
        parts = content.get("parts")
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], Mapping):
            raise ProviderResponseError("Gemini response must contain exactly one structured text part")
        text = parts[0].get("text")
        if not isinstance(text, str):
            raise ProviderResponseError("Gemini response lacks structured text output")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Gemini structured output was not valid JSON") from exc
        try:
            return PolicyFloorOutput.parse(parsed)
        except ContractError as exc:
            raise ProviderResponseError(f"Gemini output violated the policy output contract: {exc}") from exc
