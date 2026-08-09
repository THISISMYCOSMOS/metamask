"""Strict MVP schema for a fork-bound candidate portfolio-value series."""
from __future__ import annotations

from typing import Annotated, List, Literal

from pydantic import Field, model_validator

from invariant_models import Bytes32, ForkBinding, PolicyIdentifier, StrictModel, UintStr, UINT256_MAX


def _require_uint256(value: str, field_name: str) -> None:
    if int(value) > UINT256_MAX:
        raise ValueError(f"{field_name} exceeds uint256")


class ValuationContext(StrictModel):
    unit: Literal["usd-1e18"]
    mode: Literal["pinned-fork-snapshot"]
    priceBlockNumber: UintStr
    priceBlockHash: Bytes32

    @model_validator(mode="after")
    def _check_uints(self) -> "ValuationContext":
        _require_uint256(self.priceBlockNumber, "priceBlockNumber")
        return self


class CandidateTransition(StrictModel):
    transitionIndex: int = Field(ge=0)
    timestamp: UintStr
    beforeValue1e18: UintStr
    afterValue1e18: UintStr
    role: Literal["history", "candidate"]

    @model_validator(mode="after")
    def _check_uints(self) -> "CandidateTransition":
        _require_uint256(self.timestamp, "timestamp")
        _require_uint256(self.beforeValue1e18, "beforeValue1e18")
        _require_uint256(self.afterValue1e18, "afterValue1e18")
        return self


class CandidateTrace(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["portfolio-candidate"]
    traceId: PolicyIdentifier
    fork: ForkBinding
    valuation: ValuationContext
    sourceTraceHashedContentSha256: Bytes32
    coverageStartTimestamp: UintStr
    initialValue1e18: UintStr
    transitions: Annotated[List[CandidateTransition], Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def _check_series(self) -> "CandidateTrace":
        _require_uint256(self.coverageStartTimestamp, "coverageStartTimestamp")
        _require_uint256(self.initialValue1e18, "initialValue1e18")
        if self.valuation.priceBlockNumber != self.fork.blockNumber:
            raise ValueError("valuation priceBlockNumber must match fork blockNumber")
        if self.valuation.priceBlockHash.lower() != self.fork.blockHash.lower():
            raise ValueError("valuation priceBlockHash must match fork blockHash")

        coverage_start = int(self.coverageStartTimestamp)
        previous_after = self.initialValue1e18
        previous_timestamp: int | None = None
        for index, transition in enumerate(self.transitions):
            if transition.transitionIndex != index:
                raise ValueError(f"transitionIndex must be sequential at {index}")
            timestamp = int(transition.timestamp)
            if timestamp < coverage_start:
                raise ValueError(f"transition timestamp precedes coverage at {index}")
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(f"transition timestamps must increase at {index}")
            if transition.beforeValue1e18 != previous_after:
                raise ValueError(f"portfolio series is discontinuous at {index}")
            expected_role = "candidate" if index == len(self.transitions) - 1 else "history"
            if transition.role != expected_role:
                raise ValueError(f"transition role must be {expected_role} at {index}")
            previous_after = transition.afterValue1e18
            previous_timestamp = timestamp
        return self
