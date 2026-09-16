#!/usr/bin/env python3
"""Lower-level evaluator for demo policies; runtime MVP uses evaluate_approved_candidate.py."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from candidate_models import CandidateTrace
from evaluate_invariants import EvaluationInputError, PortfolioPoint, evaluate_points
from invariant_models import (
    CumulativeLossCap,
    CumulativeLossCapBps,
    InvariantPolicy,
    canonical_policy_sha256,
)


def canonical_candidate_sha256(trace: CandidateTrace) -> str:
    encoded = json.dumps(
        trace.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def _candidate_points(trace: CandidateTrace) -> list[PortfolioPoint]:
    points: list[PortfolioPoint] = []
    coverage_start = int(trace.coverageStartTimestamp)
    first_timestamp = int(trace.transitions[0].timestamp)
    if coverage_start < first_timestamp:
        initial = int(trace.initialValue1e18)
        points.append(
            PortfolioPoint(
                step_index=0,
                timestamp=coverage_start,
                before_value1e18=initial,
                after_value1e18=initial,
            )
        )

    offset = len(points)
    points.extend(
        PortfolioPoint(
            step_index=transition.transitionIndex + offset,
            timestamp=int(transition.timestamp),
            before_value1e18=int(transition.beforeValue1e18),
            after_value1e18=int(transition.afterValue1e18),
        )
        for transition in trace.transitions
    )
    return points


def evaluate_candidate(policy: InvariantPolicy, trace: CandidateTrace) -> dict[str, Any]:
    if policy.traceKind != trace.kind:
        raise EvaluationInputError("policy traceKind does not match candidate trace kind")
    if (
        policy.fork.chainId != trace.fork.chainId
        or policy.fork.blockNumber != trace.fork.blockNumber
        or policy.fork.blockHash.lower() != trace.fork.blockHash.lower()
    ):
        raise EvaluationInputError("policy fork binding does not match candidate trace fork")

    candidate_timestamp = int(trace.transitions[-1].timestamp)
    coverage_start = int(trace.coverageStartTimestamp)
    for invariant in policy.invariants:
        if isinstance(invariant, (CumulativeLossCap, CumulativeLossCapBps)):
            required_start = candidate_timestamp - int(invariant.windowSeconds)
            if required_start < 0 or coverage_start > required_start:
                raise EvaluationInputError(
                    f"history coverage is incomplete for invariant {invariant.id}"
                )

    evaluations = evaluate_points(policy, _candidate_points(trace), final_candidate_only=True)
    return {
        "schemaVersion": 1,
        "kind": "candidate-invariant-evaluation",
        "policyId": policy.policyId,
        "policySha256": canonical_policy_sha256(policy),
        "candidateTraceId": trace.traceId,
        "candidateTraceSha256": canonical_candidate_sha256(trace),
        "sourceTraceHashedContentSha256": trace.sourceTraceHashedContentSha256,
        "accepted": all(item["passed"] for item in evaluations),
        "evaluations": evaluations,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("candidate", type=Path)
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
        candidate = CandidateTrace.model_validate(json.loads(args.candidate.read_text(encoding="utf-8")))
        report = evaluate_candidate(policy, candidate)
    except (OSError, json.JSONDecodeError, ValidationError, EvaluationInputError) as exc:
        print(f"[evaluate_candidate] invalid input: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    accepted = bool(report["accepted"])
    if args.expect == "accept":
        return 0 if accepted else 1
    return 0 if not accepted else 1


if __name__ == "__main__":
    sys.exit(main())
