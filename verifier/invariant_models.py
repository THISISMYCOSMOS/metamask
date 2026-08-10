"""Strict, versioned policy models for deterministic invariant evaluation."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


UINT256_MAX = (1 << 256) - 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


UintStr = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Bytes32 = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{64}$")]
PolicyIdentifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


def _require_uint256(value: str, field_name: str, *, positive: bool = False) -> None:
    parsed = int(value)
    if parsed > UINT256_MAX:
        raise ValueError(f"{field_name} exceeds uint256")
    if positive and parsed == 0:
        raise ValueError(f"{field_name} must be greater than zero")


def _require_bps(value: str, field_name: str) -> None:
    if int(value) > 10000:
        raise ValueError(f"{field_name} must be within 0..10000")


class ForkBinding(StrictModel):
    chainId: int = Field(ge=1)
    blockNumber: UintStr
    blockHash: Bytes32

    @model_validator(mode="after")
    def _check_uints(self) -> "ForkBinding":
        _require_uint256(self.blockNumber, "blockNumber")
        return self


class PortfolioValueFloor(StrictModel):
    id: PolicyIdentifier
    kind: Literal["portfolioValueFloor"]
    floorValue1e18: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "PortfolioValueFloor":
        _require_uint256(self.floorValue1e18, "floorValue1e18", positive=True)
        return self


class PortfolioDrawdownCapBps(StrictModel):
    id: PolicyIdentifier
    kind: Literal["portfolioDrawdownCapBps"]
    referenceValue1e18: UintStr
    maxDrawdownBps: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "PortfolioDrawdownCapBps":
        _require_uint256(self.referenceValue1e18, "referenceValue1e18", positive=True)
        _require_uint256(self.maxDrawdownBps, "maxDrawdownBps")
        _require_bps(self.maxDrawdownBps, "maxDrawdownBps")
        return self


class CumulativeLossCap(StrictModel):
    id: PolicyIdentifier
    kind: Literal["cumulativeLossCap"]
    windowSeconds: UintStr
    maxLossValue1e18: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "CumulativeLossCap":
        _require_uint256(self.windowSeconds, "windowSeconds", positive=True)
        _require_uint256(self.maxLossValue1e18, "maxLossValue1e18")
        return self


class CumulativeLossCapBps(StrictModel):
    id: PolicyIdentifier
    kind: Literal["cumulativeLossCapBps"]
    windowSeconds: UintStr
    maxLossBps: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "CumulativeLossCapBps":
        _require_uint256(self.windowSeconds, "windowSeconds", positive=True)
        _require_uint256(self.maxLossBps, "maxLossBps")
        _require_bps(self.maxLossBps, "maxLossBps")
        return self


CANONICAL_INVARIANT_KINDS: tuple[str, ...] = (
    "portfolioValueFloor",
    "portfolioDrawdownCapBps",
    "cumulativeLossCap",
    "cumulativeLossCapBps",
)


Invariant = Annotated[
    Union[PortfolioValueFloor, PortfolioDrawdownCapBps, CumulativeLossCap, CumulativeLossCapBps],
    Field(discriminator="kind"),
]


class InvariantPolicy(StrictModel):
    schemaVersion: Literal[1]
    policyId: PolicyIdentifier
    traceKind: Literal["cumulative-loss", "portfolio-candidate"]
    fork: ForkBinding
    invariants: Annotated[List[Invariant], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def _unique_ids(self) -> "InvariantPolicy":
        ids = [invariant.id for invariant in self.invariants]
        if len(ids) != len(set(ids)):
            raise ValueError("invariant ids must be unique")
        return self


def canonical_policy_sha256(policy: InvariantPolicy) -> str:
    encoded = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()
