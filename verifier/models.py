"""Pydantic v2 정본 입력 모델 — traces/cumulative-loss.json(G3)의 구조를 정확히 반영한다.

TS(chain/src/cumulative-loss.ts)가 쓰는 문자열/number 규약과 정확히 일치해야 한다:
  - 256비트 값(잔액·금액·가치·타임스탬프·블록번호·가스)은 전부 문자열.
  - 작은 카운터/인덱스(schemaVersion, stepIndex, periodIndex, logIndex, decimals,
    분포 카운트, 회차 수)만 JSON number.

모든 모델은 extra="forbid" + strict=True다. 필드 하나라도 어긋나면 스키마 검증에서 실패한다.
"""
from __future__ import annotations

from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ── 공통 타입 ────────────────────────────────────────────────────────────
HexStr = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]+$")]
Address = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
Bytes32 = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{64}$")]
UintStr = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
CommitHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{40}$")]

# 고정 caveat 순서 (docs/caveat-encoding.md, 변경 금지) — 인덱스 0..5.
CAVEAT_NAME_ORDER: tuple[str, ...] = (
    "AllowedTargetsEnforcer",
    "AllowedMethodsEnforcer",
    "ValueLteEnforcer",
    "TimestampEnforcer",
    "ERC20PeriodTransferEnforcer",
    "ERC20BalanceChangeEnforcer",
)
CaveatName = Literal[
    "AllowedTargetsEnforcer",
    "AllowedMethodsEnforcer",
    "ValueLteEnforcer",
    "TimestampEnforcer",
    "ERC20PeriodTransferEnforcer",
    "ERC20BalanceChangeEnforcer",
]


def _assert_caveat_order(caveats: List["CaveatRecord | CaveatPassed"]) -> None:
    if len(caveats) != 6:
        raise ValueError(f"caveats 길이는 정확히 6이어야 한다: 실측 {len(caveats)}")
    for i, c in enumerate(caveats):
        if c.index != i:
            raise ValueError(f"caveats[{i}].index가 {c.index}다 (기대 {i})")
        if c.name != CAVEAT_NAME_ORDER[i]:
            raise ValueError(f"caveats[{i}].name이 {c.name}다 (기대 {CAVEAT_NAME_ORDER[i]})")


# ── caveat ───────────────────────────────────────────────────────────────
class CaveatRecord(StrictModel):
    index: int
    name: CaveatName
    enforcer: Address
    terms: HexStr
    termsBytes: int


class CaveatPassed(CaveatRecord):
    evidence: str


# ── fork / baseline / delegation ────────────────────────────────────────
class Fork(StrictModel):
    chainId: int
    blockNumber: UintStr
    blockHash: Bytes32
    blockTimestamp: UintStr
    delegationFrameworkCommit: CommitHash


class BaselineParameters(StrictModel):
    dailyLimitUsdc: UintStr
    periodDurationSeconds: UintStr
    perRedemptionMaxDecreaseUsdc: UintStr
    valueLteMax: UintStr
    stepCount: int
    stepAmountUsdc: UintStr


class Baseline(StrictModel):
    delegationManager: Address
    delegator: Address
    delegate: Address
    counterparty: Address
    caveats: Annotated[List[CaveatRecord], Field(min_length=6, max_length=6)]
    parameters: BaselineParameters

    @model_validator(mode="after")
    def _check_caveat_order(self) -> "Baseline":
        _assert_caveat_order(self.caveats)
        return self


class DelegationInfo(StrictModel):
    delegate: Address
    delegator: Address
    authority: Bytes32
    salt: UintStr
    signature: HexStr
    delegationHash: Bytes32


# ── steps / states ────────────────────────────────────────────────────────
class TransferEvent(StrictModel):
    logIndex: int
    address: Address
    topics: Annotated[List[Bytes32], Field(min_length=3, max_length=3)]
    data: Bytes32


class PeriodEnforcerEvent(StrictModel):
    logIndex: int
    address: Address
    topics: Annotated[List[Bytes32], Field(min_length=4, max_length=4)]
    data: HexStr


class PeriodicAllowance(StrictModel):
    periodAmount: UintStr
    periodDuration: UintStr
    startDate: UintStr
    lastTransferPeriod: UintStr
    transferredInCurrentPeriod: UintStr


class Step(StrictModel):
    stepIndex: int
    n: int
    timestamp: UintStr
    txHash: Bytes32
    blockNumber: UintStr
    receiptStatus: Literal["success", "reverted"]
    gasUsed: UintStr
    transferAmount: UintStr
    usdcBefore: UintStr
    usdcAfter: UintStr
    ethBefore: UintStr
    ethAfter: UintStr
    transferEvent: TransferEvent
    periodEnforcerEvent: PeriodEnforcerEvent
    periodicAllowanceAfter: PeriodicAllowance
    periodIndex: int
    caveatsPassed: Annotated[List[CaveatPassed], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def _check_caveats_passed_order(self) -> "Step":
        _assert_caveat_order(self.caveatsPassed)
        return self


class Balance(StrictModel):
    usdc: UintStr
    ethWei: UintStr


class State(StrictModel):
    stepIndex: int
    before: Balance
    after: Balance


# ── oracle ───────────────────────────────────────────────────────────────
class Check(StrictModel):
    name: str
    passed: bool


class OracleFields(StrictModel):
    feed: Address
    roundId: UintStr
    answer: UintStr
    startedAt: UintStr
    updatedAt: UintStr
    answeredInRound: UintStr
    decimals: int
    ageSeconds: UintStr
    checks: List[Check]


class Oracle(StrictModel):
    ethUsd: OracleFields
    usdcUsd: OracleFields
    freshnessCeilingSeconds: UintStr


# ── result ───────────────────────────────────────────────────────────────
class Portfolio(StrictModel):
    usdc: UintStr
    ethWei: UintStr
    usdcValue1e18: UintStr
    ethValue1e18: UintStr
    totalValue1e18: UintStr


class PeriodSummary(StrictModel):
    periodIndex: int
    count: int
    totalTransferredUsdc: UintStr
    dailyLimitUsdc: UintStr
    withinLimit: bool


class Result(StrictModel):
    startingPortfolio: Portfolio
    endingPortfolio: Portfolio
    loss: UintStr
    lossBps: UintStr
    periods: Annotated[List[PeriodSummary], Field(min_length=6, max_length=6)]
    checks: List[Check]


# ── 최상위 ───────────────────────────────────────────────────────────────
class Hashed(StrictModel):
    fork: Fork
    baseline: Baseline
    delegation: DelegationInfo
    steps: Annotated[List[Step], Field(min_length=20, max_length=20)]
    states: Annotated[List[State], Field(min_length=20, max_length=20)]
    oracle: Oracle
    result: Result


class Meta(StrictModel):
    generatedAtWallClock: str
    note: str


class Trace(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["cumulative-loss"]
    note: str
    hashed: Hashed
    meta: Meta
