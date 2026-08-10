#!/usr/bin/env python3
"""Deterministically evaluate approved invariants against a validated G3 trace."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from invariant_models import (
    CumulativeLossCap,
    CumulativeLossCapBps,
    InvariantPolicy,
    PortfolioDrawdownCapBps,
    PortfolioValueFloor,
    canonical_policy_sha256,
)
from models import Trace
from validate_trace import (
    ETH_TOKEN_DECIMALS,
    ORACLE_DECIMALS,
    USDC_TOKEN_DECIMALS,
    USD_VALUE_SCALE,
    cross_validate,
)


class EvaluationInputError(ValueError):
    """The policy or trace cannot be safely evaluated."""


@dataclass(frozen=True)
class PortfolioPoint:
    step_index: int
    timestamp: int
    before_value1e18: int
    after_value1e18: int


def canonical_trace_hashed_sha256(trace: Trace) -> str:
    encoded = json.dumps(
        trace.hashed.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def _asset_value_1e18(amount: int, token_decimals: int, answer: int) -> int:
    numerator = amount * answer * (10**USD_VALUE_SCALE)
    denominator = (10**token_decimals) * (10**ORACLE_DECIMALS)
    quotient, remainder = divmod(numerator, denominator)
    if remainder != 0:
        raise EvaluationInputError("portfolio valuation is not exact integer arithmetic")
    return quotient


def _portfolio_value(usdc: str, eth_wei: str, *, usdc_answer: int, eth_answer: int) -> int:
    return _asset_value_1e18(int(usdc), USDC_TOKEN_DECIMALS, usdc_answer) + _asset_value_1e18(
        int(eth_wei), ETH_TOKEN_DECIMALS, eth_answer
    )


def _validate_trace_links(trace: Trace) -> None:
    """Validate relationships not expressible in the Phase 1 field schema."""
    steps = trace.hashed.steps
    states = trace.hashed.states
    if len(steps) != len(states):
        raise EvaluationInputError("step/state lengths do not match")
    for index, (step, state) in enumerate(zip(steps, states)):
        if state.stepIndex != step.stepIndex:
            raise EvaluationInputError(f"state/step index mismatch at {index}")
        if state.before.usdc != step.usdcBefore or state.before.ethWei != step.ethBefore:
            raise EvaluationInputError(f"state/step before mismatch at {index}")
        if state.after.usdc != step.usdcAfter or state.after.ethWei != step.ethAfter:
            raise EvaluationInputError(f"state/step after mismatch at {index}")
        if index > 0:
            previous = steps[index - 1]
            if previous.usdcAfter != step.usdcBefore or previous.ethAfter != step.ethBefore:
                raise EvaluationInputError(f"discontinuous portfolio state at {index}")
            if int(previous.timestamp) >= int(step.timestamp):
                raise EvaluationInputError(f"non-increasing timestamp at {index}")
            if int(previous.blockNumber) >= int(step.blockNumber):
                raise EvaluationInputError(f"non-increasing block number at {index}")

    result = trace.hashed.result
    first = steps[0]
    last = steps[-1]
    if result.startingPortfolio.usdc != first.usdcBefore or result.startingPortfolio.ethWei != first.ethBefore:
        raise EvaluationInputError("result starting portfolio does not match first step")
    if result.endingPortfolio.usdc != last.usdcAfter or result.endingPortfolio.ethWei != last.ethAfter:
        raise EvaluationInputError("result ending portfolio does not match last step")


def normalize_phase1_trace(trace: Trace) -> list[PortfolioPoint]:
    errors = cross_validate(trace)
    if errors:
        raise EvaluationInputError("trace cross-validation failed: " + "; ".join(errors))
    _validate_trace_links(trace)

    usdc_answer = int(trace.hashed.oracle.usdcUsd.answer)
    eth_answer = int(trace.hashed.oracle.ethUsd.answer)
    return [
        PortfolioPoint(
            step_index=step.stepIndex,
            timestamp=int(step.timestamp),
            before_value1e18=_portfolio_value(
                step.usdcBefore,
                step.ethBefore,
                usdc_answer=usdc_answer,
                eth_answer=eth_answer,
            ),
            after_value1e18=_portfolio_value(
                step.usdcAfter,
                step.ethAfter,
                usdc_answer=usdc_answer,
                eth_answer=eth_answer,
            ),
        )
        for step in trace.hashed.steps
    ]


def _ratio_greater(loss_a: int, denom_a: int, loss_b: int, denom_b: int) -> bool:
    """Return True iff loss_a/denom_a > loss_b/denom_b, without division. 0/0 is 0."""
    if denom_a == 0:
        return False
    if denom_b == 0:
        return loss_a > 0
    return loss_a * denom_b > loss_b * denom_a


def evaluate_points(
    policy: InvariantPolicy,
    points: Sequence[PortfolioPoint],
    *,
    final_candidate_only: bool = False,
) -> list[dict[str, Any]]:
    """Pure evaluator. Bounds are inclusive and all arithmetic is integer-only."""
    if not points:
        raise EvaluationInputError("at least one portfolio point is required")
    for index, point in enumerate(points):
        if point.step_index < 0 or point.timestamp < 0 or point.before_value1e18 < 0 or point.after_value1e18 < 0:
            raise EvaluationInputError(f"negative portfolio point field at {index}")
        if index > 0:
            previous = points[index - 1]
            if previous.timestamp >= point.timestamp:
                raise EvaluationInputError(f"non-increasing point timestamp at {index}")
            if previous.after_value1e18 != point.before_value1e18:
                raise EvaluationInputError(f"discontinuous point value at {index}")

    evaluations: list[dict[str, Any]] = []
    for invariant in policy.invariants:
        if isinstance(invariant, PortfolioValueFloor):
            if final_candidate_only:
                final = points[-1]
                candidates = [(final.step_index, "after", final.after_value1e18)]
            else:
                candidates = [(points[0].step_index, "before", points[0].before_value1e18)]
                candidates.extend((point.step_index, "after", point.after_value1e18) for point in points)
            step_index, position, minimum = min(candidates, key=lambda candidate: candidate[2])
            limit = int(invariant.floorValue1e18)
            evaluations.append(
                {
                    "id": invariant.id,
                    "kind": invariant.kind,
                    "passed": minimum >= limit,
                    "observedMinimumValue1e18": str(minimum),
                    "floorValue1e18": invariant.floorValue1e18,
                    "evidence": {"stepIndex": step_index, "position": position},
                }
            )
            continue

        if isinstance(invariant, PortfolioDrawdownCapBps):
            first = points[0]
            reference = int(invariant.referenceValue1e18)
            if first.before_value1e18 != reference:
                raise EvaluationInputError(
                    f"portfolioDrawdownCapBps referenceValue1e18 does not match the first portfolio point for invariant {invariant.id}"
                )
            if final_candidate_only:
                final = points[-1]
                step_index, position, observed = final.step_index, "after", final.after_value1e18
            else:
                candidates = [(points[0].step_index, "before", points[0].before_value1e18)]
                candidates.extend((point.step_index, "after", point.after_value1e18) for point in points)
                step_index, position, observed = min(candidates, key=lambda candidate: candidate[2])
            max_drawdown_bps = int(invariant.maxDrawdownBps)
            evaluations.append(
                {
                    "id": invariant.id,
                    "kind": invariant.kind,
                    "passed": observed * 10000 >= reference * (10000 - max_drawdown_bps),
                    "observedMinimumValue1e18": str(observed),
                    "referenceValue1e18": invariant.referenceValue1e18,
                    "maxDrawdownBps": invariant.maxDrawdownBps,
                    "evidence": {"stepIndex": step_index, "position": position},
                }
            )
            continue

        if isinstance(invariant, CumulativeLossCap):
            window_seconds = int(invariant.windowSeconds)
            maximum_loss = -1
            maximum_window = (points[0], points[0])
            end_candidates = [(len(points) - 1, points[-1])] if final_candidate_only else list(enumerate(points))
            for end_index, end in end_candidates:
                for start in points[: end_index + 1]:
                    if end.timestamp - start.timestamp > window_seconds:
                        continue
                    loss = max(0, start.before_value1e18 - end.after_value1e18)
                    if loss > maximum_loss:
                        maximum_loss = loss
                        maximum_window = (start, end)
            cap = int(invariant.maxLossValue1e18)
            start, end = maximum_window
            evaluations.append(
                {
                    "id": invariant.id,
                    "kind": invariant.kind,
                    "passed": maximum_loss <= cap,
                    "observedMaximumLossValue1e18": str(maximum_loss),
                    "maxLossValue1e18": invariant.maxLossValue1e18,
                    "windowSeconds": invariant.windowSeconds,
                    "evidence": {
                        "startStepIndex": start.step_index,
                        "endStepIndex": end.step_index,
                        "startTimestamp": str(start.timestamp),
                        "endTimestamp": str(end.timestamp),
                    },
                }
            )
            continue

        if isinstance(invariant, CumulativeLossCapBps):
            window_seconds = int(invariant.windowSeconds)
            end_candidates = [(len(points) - 1, points[-1])] if final_candidate_only else list(enumerate(points))
            best_loss = 0
            best_denom = 0
            best_window = (points[0], points[0])
            first_candidate = True
            for end_index, end in end_candidates:
                for start in points[: end_index + 1]:
                    if end.timestamp - start.timestamp > window_seconds:
                        continue
                    loss = max(0, start.before_value1e18 - end.after_value1e18)
                    denom = start.before_value1e18
                    if first_candidate or _ratio_greater(loss, denom, best_loss, best_denom):
                        best_loss = loss
                        best_denom = denom
                        best_window = (start, end)
                        first_candidate = False
            max_loss_bps = int(invariant.maxLossBps)
            start, end = best_window
            evaluations.append(
                {
                    "id": invariant.id,
                    "kind": invariant.kind,
                    "passed": best_loss * 10000 <= best_denom * max_loss_bps,
                    "observedMaximumLossValue1e18": str(best_loss),
                    "observedReferenceValue1e18": str(best_denom),
                    "maxLossBps": invariant.maxLossBps,
                    "windowSeconds": invariant.windowSeconds,
                    "evidence": {
                        "startStepIndex": start.step_index,
                        "endStepIndex": end.step_index,
                        "startTimestamp": str(start.timestamp),
                        "endTimestamp": str(end.timestamp),
                    },
                }
            )
            continue

        raise EvaluationInputError(f"unsupported invariant type: {type(invariant).__name__}")

    return evaluations


def evaluate(policy: InvariantPolicy, trace: Trace) -> dict[str, Any]:
    if policy.traceKind != trace.kind:
        raise EvaluationInputError("policy traceKind does not match trace kind")
    trace_fork = trace.hashed.fork
    if (
        policy.fork.chainId != trace_fork.chainId
        or policy.fork.blockNumber != trace_fork.blockNumber
        or policy.fork.blockHash.lower() != trace_fork.blockHash.lower()
    ):
        raise EvaluationInputError("policy fork binding does not match trace fork")

    points = normalize_phase1_trace(trace)
    evaluations = evaluate_points(policy, points)
    return {
        "schemaVersion": 1,
        "kind": "invariant-evaluation",
        "policyId": policy.policyId,
        "policySha256": canonical_policy_sha256(policy),
        "traceHashedContentSha256": canonical_trace_hashed_sha256(trace),
        "accepted": all(item["passed"] for item in evaluations),
        "evaluations": evaluations,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument(
        "--expect",
        choices=("accept", "reject"),
        default="accept",
        help="required decision for exit 0; defaults to accept for fail-closed integration",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        policy = InvariantPolicy.model_validate(json.loads(args.policy.read_text(encoding="utf-8")))
        trace = Trace.model_validate(json.loads(args.trace.read_text(encoding="utf-8")))
        report = evaluate(policy, trace)
    except (OSError, json.JSONDecodeError, ValidationError, EvaluationInputError) as exc:
        print(f"[evaluate_invariants] invalid input: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    accepted = bool(report["accepted"])
    if args.expect == "accept":
        return 0 if accepted else 1
    return 0 if not accepted else 1


if __name__ == "__main__":
    sys.exit(main())
