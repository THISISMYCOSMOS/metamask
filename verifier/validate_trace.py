#!/usr/bin/env python3
"""G3 트레이스 검증기 — traces/cumulative-loss.json을 Pydantic 스키마 + 독립 교차 검증으로
확인한다.

사용법:
    python validate_trace.py <trace.json> [--quiet]

Pydantic 검증(models.py)을 통과해도 그것만으로는 "숫자가 맞는 자리에 있다"는 것만 보장하고
값 자체가 맞는지는 보장하지 않는다. 이 스크립트가 독립적인 검사자가 되도록, 트레이스에
기록된 계산 결과를 신뢰하지 않고 Python에서 int로 전부 다시 계산해 대조한다. float은 쓰지
않는다.

절대경로·비밀값은 출력하지 않는다. 예외는 모두 잡아서 exit 1로 통일한다(exit 2 없음).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from models import Trace

BASE_TIMESTAMP = 1_786_068_491
EXPECTED_CHAIN_ID = 1
EXPECTED_FORK_BLOCK_NUMBER = 25_700_000
EXPECTED_FORK_BLOCK_HASH = "0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf"
EXPECTED_FRAMEWORK_COMMIT = "197463b4aba3409adef1df544dabafc3636ee82d"
STEP_TIMESTAMP_OFFSET = 21_600
STEP_COUNT = 20
STEP_AMOUNT = 500_000_000
DAILY_LIMIT = 2_000_000_000
STARTING_USDC = 10_000_000_000
EXPECTED_DISTRIBUTION = [3, 4, 4, 4, 4, 1]
FORK_BLOCK_TIMESTAMP = 1_786_068_491
ORACLE_MAX_STALENESS_SECONDS = 86_400
EXPECTED_ETH_ANSWER = 189_811_115_161
EXPECTED_ETH_UPDATED_AT = 1_786_066_847
EXPECTED_USDC_ANSWER = 99_976_752
EXPECTED_USDC_UPDATED_AT = 1_786_003_223
USDC_TOKEN_DECIMALS = 6
ETH_TOKEN_DECIMALS = 18
ORACLE_DECIMALS = 8
USD_VALUE_SCALE = 18
# caveat 6종 termsBytes — docs/caveat-encoding.md 정본 순서 그대로.
EXPECTED_TERMS_BYTES = [20, 4, 32, 32, 116, 73]
EXPECTED_DELEGATION_HASH = "0x9c79a1b3758c54c83757c4d724957df8333966500183b78986b7abcf7bbe7ebb"
DELEGATION_MANAGER = "0xea6f34e56c9bea6d9114a30b52e040af2b594373"
DELEGATOR = "0x09e68b4a2335a2aaa1944bc3938d285b883f11e1"
DELEGATE = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
COUNTERPARTY = "0x90f79bf6eb2c4f870365e785982e1f101e93b906"
USDC_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
EXPECTED_ENFORCERS = [
    "0xef2f79e2a6cda4f31bd213b0d1877a9b93f70038",
    "0x27af251f5cd8ae094925aef1722655ea822edbe1",
    "0x0b5c5bea5df2fa9879fac0aa3690ae2cad9ec498",
    "0x8af1d7a43158697106953f7f2efada603984269a",
    "0x5c0fd678387dd9a4f6d7ae4a4a2798439a0aebb0",
    "0x2ab40067d719bc5938aa1875cb409a9dbf50022c",
]
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
PERIOD_TRANSFER_TOPIC0 = "0xb2a345c7f80b4be490c405f4a994faf85384dd05da7d70be0801dc31a8c253af"


def uint256_word(value: int) -> str:
    return f"{value:064x}"


def uint128_word(value: int) -> str:
    return f"{value:032x}"


def address_topic(address: str) -> str:
    return "0x" + address[2:].lower().zfill(64)


EXPECTED_TERMS = [
    USDC_ADDRESS,
    "0xa9059cbb",
    "0x" + uint256_word(0),
    "0x" + uint128_word(BASE_TIMESTAMP) + uint128_word(BASE_TIMESTAMP + 2_592_000),
    "0x" + USDC_ADDRESS[2:] + uint256_word(DAILY_LIMIT) + uint256_word(86_400) + uint256_word(BASE_TIMESTAMP),
    "0x01" + USDC_ADDRESS[2:] + DELEGATOR[2:] + uint256_word(STEP_AMOUNT),
]


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def asset_value_1e18(amount: int, token_decimals: int, answer: int, errors: list[str], label: str) -> int:
    """value1e18 = amount * answer * 10^18 / (10^tokenDecimals * 10^8). 나머지가 0이 아니면 에러 기록."""
    numerator = amount * answer * (10**USD_VALUE_SCALE)
    denominator = (10**token_decimals) * (10**ORACLE_DECIMALS)
    if numerator % denominator != 0:
        fail(errors, f"{label}: 포트폴리오 가치 환산 나머지가 0이 아니다")
    return numerator // denominator


def cross_validate(trace: Trace) -> list[str]:
    errors: list[str] = []
    h = trace.hashed
    steps = h.steps
    states = h.states

    for field, actual, expected in (
        ("chainId", h.fork.chainId, EXPECTED_CHAIN_ID),
        ("blockNumber", int(h.fork.blockNumber), EXPECTED_FORK_BLOCK_NUMBER),
        ("blockHash", h.fork.blockHash.lower(), EXPECTED_FORK_BLOCK_HASH),
        ("blockTimestamp", int(h.fork.blockTimestamp), FORK_BLOCK_TIMESTAMP),
        ("delegationFrameworkCommit", h.fork.delegationFrameworkCommit.lower(), EXPECTED_FRAMEWORK_COMMIT),
    ):
        if actual != expected:
            fail(errors, f"fork.{field}={actual} (expected {expected})")

    if h.delegation.delegationHash.lower() != EXPECTED_DELEGATION_HASH:
        fail(errors, f"delegationHash={h.delegation.delegationHash}가 정본과 다르다")
    for field, actual, expected in (
        ("delegationManager", h.baseline.delegationManager.lower(), DELEGATION_MANAGER),
        ("delegator", h.baseline.delegator.lower(), DELEGATOR),
        ("delegate", h.baseline.delegate.lower(), DELEGATE),
        ("counterparty", h.baseline.counterparty.lower(), COUNTERPARTY),
    ):
        if actual != expected:
            fail(errors, f"baseline.{field}={actual} (기대 {expected})")

    parameters = h.baseline.parameters
    for field, actual, expected in (
        ("dailyLimitUsdc", int(parameters.dailyLimitUsdc), DAILY_LIMIT),
        ("periodDurationSeconds", int(parameters.periodDurationSeconds), 86_400),
        ("perRedemptionMaxDecreaseUsdc", int(parameters.perRedemptionMaxDecreaseUsdc), STEP_AMOUNT),
        ("valueLteMax", int(parameters.valueLteMax), 0),
        ("stepCount", parameters.stepCount, STEP_COUNT),
        ("stepAmountUsdc", int(parameters.stepAmountUsdc), STEP_AMOUNT),
    ):
        if actual != expected:
            fail(errors, f"baseline.parameters.{field}={actual} (기대 {expected})")

    if len(steps) != STEP_COUNT:
        fail(errors, f"steps 길이가 {len(steps)}다 (기대 {STEP_COUNT})")
    if len(states) != STEP_COUNT:
        fail(errors, f"states 길이가 {len(states)}다 (기대 {STEP_COUNT})")

    period_seen = [0, 0, 0, 0, 0, 0]
    for i, step in enumerate(steps):
        if step.stepIndex != i:
            fail(errors, f"steps[{i}].stepIndex={step.stepIndex} (기대 {i})")
        if i < len(states) and states[i].stepIndex != i:
            fail(errors, f"states[{i}].stepIndex={states[i].stepIndex} (기대 {i})")
        if step.receiptStatus != "success":
            fail(errors, f"steps[{i}].receiptStatus={step.receiptStatus} (기대 success)")
        if int(step.transferAmount) != STEP_AMOUNT:
            fail(errors, f"steps[{i}].transferAmount={step.transferAmount} (기대 {STEP_AMOUNT})")
        expected_ts = BASE_TIMESTAMP + STEP_TIMESTAMP_OFFSET * (i + 1)
        if int(step.timestamp) != expected_ts:
            fail(errors, f"steps[{i}].timestamp={step.timestamp} (기대 {expected_ts})")
        usdc_before = int(step.usdcBefore)
        usdc_after = int(step.usdcAfter)
        if usdc_before - usdc_after != STEP_AMOUNT:
            fail(errors, f"steps[{i}]: usdcBefore-usdcAfter != {STEP_AMOUNT}")
        if step.ethBefore != step.ethAfter:
            fail(errors, f"steps[{i}]: ethBefore != ethAfter")
        if i < len(states):
            state = states[i]
            if state.before.usdc != step.usdcBefore or state.before.ethWei != step.ethBefore:
                fail(errors, f"states[{i}].before does not match steps[{i}] before")
            if state.after.usdc != step.usdcAfter or state.after.ethWei != step.ethAfter:
                fail(errors, f"states[{i}].after does not match steps[{i}] after")
        if i > 0:
            previous = steps[i - 1]
            if previous.usdcAfter != step.usdcBefore or previous.ethAfter != step.ethBefore:
                fail(errors, f"steps[{i}] is not continuous with steps[{i - 1}]")
            if int(previous.timestamp) >= int(step.timestamp):
                fail(errors, f"steps[{i}].timestamp is not strictly increasing")
            if int(previous.blockNumber) >= int(step.blockNumber):
                fail(errors, f"steps[{i}].blockNumber is not strictly increasing")

        transfer = step.transferEvent
        expected_transfer_topics = [
            TRANSFER_TOPIC0,
            address_topic(DELEGATOR),
            address_topic(COUNTERPARTY),
        ]
        if transfer.address.lower() != USDC_ADDRESS:
            fail(errors, f"steps[{i}].transferEvent.address가 USDC가 아니다")
        if [topic.lower() for topic in transfer.topics] != expected_transfer_topics:
            fail(errors, f"steps[{i}].transferEvent.topics가 정본과 다르다")
        if transfer.data.lower() != "0x" + uint256_word(STEP_AMOUNT):
            fail(errors, f"steps[{i}].transferEvent.data가 500 USDC가 아니다")

        allowance = step.periodicAllowanceAfter
        expected_period = ((expected_ts - BASE_TIMESTAMP) // 86_400)
        if not 0 <= expected_period < len(period_seen):
            fail(errors, f"steps[{i}] 계산 period={expected_period} 범위 밖")
            expected_consumed = 0
        else:
            period_seen[expected_period] += 1
            expected_consumed = period_seen[expected_period] * STEP_AMOUNT
        for field, actual, expected in (
            ("periodAmount", int(allowance.periodAmount), DAILY_LIMIT),
            ("periodDuration", int(allowance.periodDuration), 86_400),
            ("startDate", int(allowance.startDate), BASE_TIMESTAMP),
            ("lastTransferPeriod", int(allowance.lastTransferPeriod), expected_period + 1),
            ("transferredInCurrentPeriod", int(allowance.transferredInCurrentPeriod), expected_consumed),
        ):
            if actual != expected:
                fail(errors, f"steps[{i}].periodicAllowanceAfter.{field}={actual} (기대 {expected})")

        period_event = step.periodEnforcerEvent
        expected_period_topics = [
            PERIOD_TRANSFER_TOPIC0,
            address_topic(DELEGATION_MANAGER),
            address_topic(DELEGATE),
            EXPECTED_DELEGATION_HASH,
        ]
        if period_event.address.lower() != EXPECTED_ENFORCERS[4]:
            fail(errors, f"steps[{i}].periodEnforcerEvent.address가 정본 enforcer가 아니다")
        if [topic.lower() for topic in period_event.topics] != expected_period_topics:
            fail(errors, f"steps[{i}].periodEnforcerEvent.topics가 정본과 다르다")
        expected_period_data = "0x" + "".join(
            [
                USDC_ADDRESS[2:].zfill(64),
                uint256_word(DAILY_LIMIT),
                uint256_word(86_400),
                uint256_word(BASE_TIMESTAMP),
                uint256_word(expected_consumed),
                uint256_word(expected_ts),
            ]
        )
        if period_event.data.lower() != expected_period_data:
            fail(errors, f"steps[{i}].periodEnforcerEvent.data가 정본과 다르다")

    # period 분포 — 트레이스가 기록한 periodIndex를 신뢰하지 않고, 온체인
    # periodicAllowanceAfter.lastTransferPeriod(컨트랙트 규약: periodIndex0 + 1)에서 재계산한다.
    distribution = [0, 0, 0, 0, 0, 0]
    for i, step in enumerate(steps):
        last_period = int(step.periodicAllowanceAfter.lastTransferPeriod)
        period_index0 = last_period - 1
        if period_index0 != step.periodIndex:
            fail(errors, f"steps[{i}]: periodIndex={step.periodIndex} != lastTransferPeriod-1={period_index0}")
        if 0 <= period_index0 < 6:
            distribution[period_index0] += 1
        else:
            fail(errors, f"steps[{i}]: periodIndex0={period_index0} 범위 밖(0..5)")

    if distribution != EXPECTED_DISTRIBUTION:
        fail(errors, f"period 분포 불일치: 실측 {distribution}, 기대 {EXPECTED_DISTRIBUTION}")

    for i, count in enumerate(distribution):
        total = count * STEP_AMOUNT
        if total > DAILY_LIMIT:
            fail(errors, f"period[{i}] 합계 {total}가 일일 한도 {DAILY_LIMIT} 초과")

    total_transferred = sum(int(s.transferAmount) for s in steps)
    if total_transferred != STARTING_USDC:
        fail(errors, f"총 이체액 {total_transferred} != {STARTING_USDC}")

    ending_usdc = int(h.result.endingPortfolio.usdc)
    if ending_usdc != 0:
        fail(errors, f"종료 USDC {ending_usdc} != 0")
    if h.result.endingPortfolio.ethWei != h.result.startingPortfolio.ethWei:
        fail(errors, "종료 ETH != 시작 ETH")
    if steps:
        if (
            h.result.startingPortfolio.usdc != steps[0].usdcBefore
            or h.result.startingPortfolio.ethWei != steps[0].ethBefore
        ):
            fail(errors, "startingPortfolio does not match first step before")
        if (
            h.result.endingPortfolio.usdc != steps[-1].usdcAfter
            or h.result.endingPortfolio.ethWei != steps[-1].ethAfter
        ):
            fail(errors, "endingPortfolio does not match last step after")

    # ── 오라클 ───────────────────────────────────────────────────────────
    for label, feed, expected_answer, expected_updated_at in (
        ("ethUsd", h.oracle.ethUsd, EXPECTED_ETH_ANSWER, EXPECTED_ETH_UPDATED_AT),
        ("usdcUsd", h.oracle.usdcUsd, EXPECTED_USDC_ANSWER, EXPECTED_USDC_UPDATED_AT),
    ):
        if feed.decimals != 8:
            fail(errors, f"oracle.{label}.decimals={feed.decimals} (기대 8)")
        answer = int(feed.answer)
        if answer <= 0:
            fail(errors, f"oracle.{label}.answer={answer} (양수여야 한다)")
        updated_at = int(feed.updatedAt)
        if updated_at > FORK_BLOCK_TIMESTAMP:
            fail(errors, f"oracle.{label}.updatedAt={updated_at}가 포크 블록 ts보다 미래다")
        if int(feed.answeredInRound) < int(feed.roundId):
            fail(errors, f"oracle.{label}.answeredInRound < roundId")
        age = FORK_BLOCK_TIMESTAMP - updated_at
        if age != int(feed.ageSeconds):
            fail(errors, f"oracle.{label}.ageSeconds={feed.ageSeconds} != 재계산 {age}")
        if age > ORACLE_MAX_STALENESS_SECONDS:
            fail(errors, f"oracle.{label}.ageSeconds={age}가 신선도 상한 {ORACLE_MAX_STALENESS_SECONDS} 초과")
        if answer != expected_answer:
            fail(errors, f"oracle.{label}.answer={answer} != 핀 값 {expected_answer}")
        if updated_at != expected_updated_at:
            fail(errors, f"oracle.{label}.updatedAt={updated_at} != 핀 값 {expected_updated_at}")

    # ── 포트폴리오 정수 재계산 (float 금지) ────────────────────────────────
    start_usdc = int(h.result.startingPortfolio.usdc)
    start_eth = int(h.result.startingPortfolio.ethWei)
    end_usdc = int(h.result.endingPortfolio.usdc)
    end_eth = int(h.result.endingPortfolio.ethWei)
    usdc_answer = int(h.oracle.usdcUsd.answer)
    eth_answer = int(h.oracle.ethUsd.answer)

    start_usdc_value = asset_value_1e18(start_usdc, USDC_TOKEN_DECIMALS, usdc_answer, errors, "startUsdcValue")
    start_eth_value = asset_value_1e18(start_eth, ETH_TOKEN_DECIMALS, eth_answer, errors, "startEthValue")
    end_usdc_value = asset_value_1e18(end_usdc, USDC_TOKEN_DECIMALS, usdc_answer, errors, "endUsdcValue")
    end_eth_value = asset_value_1e18(end_eth, ETH_TOKEN_DECIMALS, eth_answer, errors, "endEthValue")

    start_total = start_usdc_value + start_eth_value
    end_total = end_usdc_value + end_eth_value
    loss = start_total - end_total
    loss_bps = (loss * 10_000) // start_total if start_total != 0 else 0

    if str(start_usdc_value) != h.result.startingPortfolio.usdcValue1e18:
        fail(errors, "startingPortfolio.usdcValue1e18 재계산 불일치")
    if str(start_eth_value) != h.result.startingPortfolio.ethValue1e18:
        fail(errors, "startingPortfolio.ethValue1e18 재계산 불일치")
    if str(start_total) != h.result.startingPortfolio.totalValue1e18:
        fail(errors, "startingPortfolio.totalValue1e18 재계산 불일치")
    if str(end_usdc_value) != h.result.endingPortfolio.usdcValue1e18:
        fail(errors, "endingPortfolio.usdcValue1e18 재계산 불일치")
    if str(end_eth_value) != h.result.endingPortfolio.ethValue1e18:
        fail(errors, "endingPortfolio.ethValue1e18 재계산 불일치")
    if str(end_total) != h.result.endingPortfolio.totalValue1e18:
        fail(errors, "endingPortfolio.totalValue1e18 재계산 불일치")
    if str(loss) != h.result.loss:
        fail(errors, f"loss 재계산 불일치: 기록 {h.result.loss}, 재계산 {loss}")
    if str(loss_bps) != h.result.lossBps:
        fail(errors, f"lossBps 재계산 불일치: 기록 {h.result.lossBps}, 재계산 {loss_bps}")
    if loss <= 0:
        fail(errors, f"loss={loss} <= 0 (양수여야 한다)")

    # ── caveat 6종 순서/termsBytes (docs/caveat-encoding.md) ────────────────
    caveats = h.baseline.caveats
    if len(caveats) != 6:
        fail(errors, f"baseline.caveats 길이 {len(caveats)} != 6")
    else:
        for i, (c, expected_bytes, expected_enforcer, expected_terms) in enumerate(
            zip(caveats, EXPECTED_TERMS_BYTES, EXPECTED_ENFORCERS, EXPECTED_TERMS)
        ):
            if c.termsBytes != expected_bytes:
                fail(errors, f"caveats[{i}].termsBytes={c.termsBytes} (기대 {expected_bytes})")
            if c.enforcer.lower() != expected_enforcer:
                fail(errors, f"caveats[{i}].enforcer={c.enforcer} (기대 {expected_enforcer})")
            if c.terms.lower() != expected_terms.lower():
                fail(errors, f"caveats[{i}].terms가 정본 바이트와 다르다")

    return errors


def main() -> int:
    args = sys.argv[1:]
    quiet = "--quiet" in args
    positional = [a for a in args if a != "--quiet"]
    if len(positional) != 1:
        print("사용법: python validate_trace.py <trace.json> [--quiet]", file=sys.stderr)
        return 1

    trace_path = Path(positional[0])
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
        trace = Trace.model_validate(data)
        errors = cross_validate(trace)
    except ValidationError as exc:
        print("[validate_trace] Pydantic 스키마 검증 실패:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — 예외는 모아서 exit 1로 통일한다 (exit 2 없음).
        print(f"[validate_trace] 검증 중 예외 발생: {exc}", file=sys.stderr)
        return 1

    if errors:
        print(f"[validate_trace] 교차 검증 실패 ({len(errors)}건):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if not quiet:
        h = trace.hashed
        distribution = [0, 0, 0, 0, 0, 0]
        for step in h.steps:
            distribution[step.periodIndex] += 1
        print(f"[validate_trace] 통과 - 회차 {len(h.steps)}건, period 분포 {distribution}")
        print(f"[validate_trace] 시작 포트폴리오(1e-18 USD)={h.result.startingPortfolio.totalValue1e18}")
        print(f"[validate_trace] 종료 포트폴리오(1e-18 USD)={h.result.endingPortfolio.totalValue1e18}")
        print(f"[validate_trace] loss={h.result.loss} lossBps={h.result.lossBps}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
