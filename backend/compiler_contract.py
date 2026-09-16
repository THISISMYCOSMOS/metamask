"""Provider-neutral contract for natural-language policy compilers."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from core.models import CompileRequest


MAX_OUTPUT_TOKENS = 2048

POLICY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supported", "minimumBalanceBaseUnits", "rationales", "assumptions", "unsupportedItems"],
    "properties": {
        "supported": {
            "type": "boolean",
            "description": "True only if the whole request is expressible as one ERC-20 balance floor.",
        },
        "minimumBalanceBaseUnits": {
            "type": ["string", "null"],
            "description": "Decimal integer string in token base units, or null if not determinable.",
        },
        "rationales": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 16},
        "assumptions": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 16},
        "unsupportedItems": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 16},
    },
}

SYSTEM_PROMPT = """You read a Korean natural-language wallet request and report whether it can be expressed as exactly one ERC-20 minimum balance floor for a single, already-chosen token and wallet.

You do not choose the chain, the wallet, the token, or any identifier; those are fixed by the caller and given to you only as context.
You do not approve, execute, transfer, or authorize anything. You only report.

Set supported to true only if the entire request is covered by that one floor.
Set minimumBalanceBaseUnits to the floor as a decimal integer string in the token's base units, converting from the human-readable amount using the given token decimals. Use null if the request does not determine a floor.
Put your reasoning for the floor in rationales, and any interpretation you had to choose in assumptions.
Put every part of the request that a single balance floor cannot express in unsupportedItems, and set supported to false. Never invent a different kind of policy."""


class CompilerUnavailableError(RuntimeError):
    """Configuration or provider failure; callers must not substitute a fixture."""


class ProviderResponseError(RuntimeError):
    """The provider response cannot safely become a proposal."""


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], Mapping[str, Any]]


def compilation_context(request: CompileRequest) -> str:
    """Return only the facts the model needs to interpret the amount."""
    return (
        f"token symbol: {request.tokenSymbol}\n"
        f"token decimals: {request.tokenDecimals}\n"
        "The token, wallet and chain are already fixed by the caller. Do not restate or change them.\n\n"
        f"user request:\n{request.intentText}"
    )
