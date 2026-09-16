"""Gemini adapter for the four RQ2 invariant kinds; no fixture fallback."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import BenchmarkCase, CompilationOutput


DEFAULT_MODEL = "gemini-3.5-flash-lite"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], Mapping[str, Any]]


class CompilerUnavailableError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    pass


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supported", "invariant", "rationales", "assumptions", "unsupportedItems"],
    "properties": {
        "supported": {"type": "boolean"},
        "invariant": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": [
                "kind", "floorValue1e18", "maxDrawdownBps", "windowSeconds",
                "maxLossValue1e18", "maxLossBps",
            ],
            "properties": {
                "kind": {"type": "string", "enum": [
                    "portfolioValueFloor", "portfolioDrawdownCapBps",
                    "cumulativeLossCap", "cumulativeLossCapBps",
                ]},
                "floorValue1e18": {"type": ["string", "null"]},
                "maxDrawdownBps": {"type": ["string", "null"]},
                "windowSeconds": {"type": ["string", "null"]},
                "maxLossValue1e18": {"type": ["string", "null"]},
                "maxLossBps": {"type": ["string", "null"]},
            },
        },
        "rationales": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "unsupportedItems": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
    },
}

SYSTEM_PROMPT = """Translate one wallet intent into exactly one deterministic portfolio invariant.
Allowed kinds are portfolioValueFloor, portfolioDrawdownCapBps, cumulativeLossCap, and cumulativeLossCapBps.
USD values are unsigned decimal strings scaled by 1e18. Percentages are integer basis points (2% = 200).
Time windows are positive integer seconds. Use only fields belonging to the selected kind and set all other invariant fields to null.
The current portfolio value, when present, is caller-owned context for later deterministic binding; do not copy it into output or alter it.
Set supported=false and invariant=null when the request is ambiguous, asks for multiple conditions, requires another policy kind, or omits a required amount/window.
Never approve, judge a transaction, invent wallet facts, or emit executable Python/Solidity code."""


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> "GeminiConfig":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip()
        if not key or not MODEL_PATTERN.fullmatch(model):
            raise CompilerUnavailableError("valid GEMINI_API_KEY and GEMINI_MODEL are required")
        return cls(key, model)


def _transport(url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310: fixed Google endpoint
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CompilerUnavailableError("Gemini invariant compilation failed") from exc
    if not isinstance(value, dict):
        raise ProviderResponseError("Gemini returned a non-object response")
    return value


class GeminiInvariantCompiler:
    def __init__(self, config: GeminiConfig, transport: Transport | None = None) -> None:
        if not config.api_key.strip() or not MODEL_PATTERN.fullmatch(config.model):
            raise CompilerUnavailableError("valid Gemini configuration is required")
        self._config = config
        self._transport = transport or _transport

    def build_request(self, case: BenchmarkCase) -> dict[str, Any]:
        context = "none"
        if case.context.currentPortfolioValue1e18 is not None:
            context = case.context.currentPortfolioValue1e18
        prompt = f"caller currentPortfolioValue1e18: {context}\nuser intent:\n{case.intentText}"
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
                "responseJsonSchema": OUTPUT_SCHEMA,
            },
        }

    def compile(self, case: BenchmarkCase) -> CompilationOutput:
        response = self._transport(
            URL.format(model=self._config.model),
            {"content-type": "application/json", "x-goog-api-key": self._config.api_key},
            self.build_request(case),
        )
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ProviderResponseError("Gemini response must contain exactly one candidate")
        candidate = candidates[0]
        if not isinstance(candidate, Mapping) or candidate.get("finishReason") != "STOP":
            raise ProviderResponseError("Gemini response did not complete normally")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], Mapping):
            raise ProviderResponseError("Gemini response must contain exactly one JSON text part")
        text = parts[0].get("text")
        if not isinstance(text, str):
            raise ProviderResponseError("Gemini response text is missing")
        try:
            return CompilationOutput.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderResponseError("Gemini output violated the local invariant contract") from exc
