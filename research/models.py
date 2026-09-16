"""Strict contracts for the RQ2 natural-language invariant benchmark."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


UINT256_MAX = (1 << 256) - 1
Uint = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _uint(value: str | None, field: str, *, maximum: int = UINT256_MAX, positive: bool = False) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    parsed = int(value)
    if parsed > maximum:
        raise ValueError(f"{field} exceeds its maximum")
    if positive and parsed == 0:
        raise ValueError(f"{field} must be positive")


class BenchmarkContext(StrictModel):
    """Caller-owned facts. The LLM may use but must not invent these values."""

    currentPortfolioValue1e18: Uint | None = None

    @model_validator(mode="after")
    def _check(self) -> "BenchmarkContext":
        if self.currentPortfolioValue1e18 is not None:
            _uint(self.currentPortfolioValue1e18, "currentPortfolioValue1e18", positive=True)
        return self


class CompiledInvariant(StrictModel):
    """Provider output before caller-owned identifiers and fork data are attached."""

    kind: Literal[
        "portfolioValueFloor",
        "portfolioDrawdownCapBps",
        "cumulativeLossCap",
        "cumulativeLossCapBps",
    ]
    floorValue1e18: Uint | None = None
    maxDrawdownBps: Uint | None = None
    windowSeconds: Uint | None = None
    maxLossValue1e18: Uint | None = None
    maxLossBps: Uint | None = None

    @model_validator(mode="after")
    def _shape(self) -> "CompiledInvariant":
        fields = {
            "floorValue1e18": self.floorValue1e18,
            "maxDrawdownBps": self.maxDrawdownBps,
            "windowSeconds": self.windowSeconds,
            "maxLossValue1e18": self.maxLossValue1e18,
            "maxLossBps": self.maxLossBps,
        }
        required = {
            "portfolioValueFloor": {"floorValue1e18"},
            "portfolioDrawdownCapBps": {"maxDrawdownBps"},
            "cumulativeLossCap": {"windowSeconds", "maxLossValue1e18"},
            "cumulativeLossCapBps": {"windowSeconds", "maxLossBps"},
        }[self.kind]
        present = {name for name, value in fields.items() if value is not None}
        if present != required:
            raise ValueError(f"{self.kind} requires exactly {sorted(required)}")
        for name in required:
            _uint(fields[name], name, positive=name == "windowSeconds")
        if self.maxDrawdownBps is not None:
            _uint(self.maxDrawdownBps, "maxDrawdownBps", maximum=10_000)
        if self.maxLossBps is not None:
            _uint(self.maxLossBps, "maxLossBps", maximum=10_000)
        return self


class CompilationOutput(StrictModel):
    supported: bool
    invariant: CompiledInvariant | None
    rationales: Annotated[list[Sentence], Field(max_length=16)]
    assumptions: Annotated[list[Sentence], Field(max_length=16)]
    unsupportedItems: Annotated[list[Sentence], Field(max_length=16)]

    @model_validator(mode="after")
    def _approvable_shape(self) -> "CompilationOutput":
        if self.supported:
            if self.invariant is None or self.unsupportedItems or not self.rationales:
                raise ValueError("supported output requires one invariant, rationale, and no unsupported items")
        elif self.invariant is not None or not self.unsupportedItems:
            raise ValueError("unsupported output must carry no invariant and explain what is unsupported")
        return self


class BenchmarkCase(StrictModel):
    caseId: Identifier
    intentText: Text
    context: BenchmarkContext
    expected: CompilationOutput

    @model_validator(mode="after")
    def _context_binding(self) -> "BenchmarkCase":
        invariant = self.expected.invariant
        if invariant is not None and invariant.kind == "portfolioDrawdownCapBps":
            if self.context.currentPortfolioValue1e18 is None:
                raise ValueError("drawdown benchmark cases require caller-owned current portfolio value")
        return self


class BenchmarkDataset(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["intent-invariant-benchmark"]
    cases: Annotated[list[BenchmarkCase], Field(min_length=50, max_length=100)]

    @model_validator(mode="after")
    def _unique_ids(self) -> "BenchmarkDataset":
        ids = [case.caseId for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case ids must be unique")
        return self


class PredictionRecord(StrictModel):
    caseId: Identifier
    output: CompilationOutput
