#!/usr/bin/env python3
"""Deterministically adapt the validated Phase 1 G3 trace into the MVP candidate schema."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from candidate_models import CandidateTrace
from evaluate_invariants import (
    EvaluationInputError,
    canonical_trace_hashed_sha256,
    normalize_phase1_trace,
)
from invariant_models import CumulativeLossCap, InvariantPolicy
from models import Trace


def build_candidate_fixture(policy: InvariantPolicy, source: Trace) -> CandidateTrace:
    if policy.traceKind != "portfolio-candidate":
        raise EvaluationInputError("fixture policy must target portfolio-candidate")
    if (
        policy.fork.chainId != source.hashed.fork.chainId
        or policy.fork.blockNumber != source.hashed.fork.blockNumber
        or policy.fork.blockHash.lower() != source.hashed.fork.blockHash.lower()
    ):
        raise EvaluationInputError("policy fork binding does not match source trace")

    points = normalize_phase1_trace(source)
    windows = [int(item.windowSeconds) for item in policy.invariants if isinstance(item, CumulativeLossCap)]
    if not windows:
        raise EvaluationInputError("candidate fixture requires a cumulativeLossCap invariant")
    candidate = points[-1]
    coverage_start = candidate.timestamp - max(windows)
    if points[0].timestamp > coverage_start:
        raise EvaluationInputError("source trace does not cover the requested window start")
    selected = [point for point in points if point.timestamp >= coverage_start]
    if not selected:
        raise EvaluationInputError("source trace does not cover the requested window")

    data = {
        "schemaVersion": 1,
        "kind": "portfolio-candidate",
        "traceId": "g3-final-step-candidate",
        "fork": {
            "chainId": source.hashed.fork.chainId,
            "blockNumber": source.hashed.fork.blockNumber,
            "blockHash": source.hashed.fork.blockHash,
        },
        "valuation": {
            "unit": "usd-1e18",
            "mode": "pinned-fork-snapshot",
            "priceBlockNumber": source.hashed.fork.blockNumber,
            "priceBlockHash": source.hashed.fork.blockHash,
        },
        "sourceTraceHashedContentSha256": canonical_trace_hashed_sha256(source),
        "coverageStartTimestamp": str(coverage_start),
        "initialValue1e18": str(selected[0].before_value1e18),
        "transitions": [
            {
                "transitionIndex": index,
                "timestamp": str(point.timestamp),
                "beforeValue1e18": str(point.before_value1e18),
                "afterValue1e18": str(point.after_value1e18),
                "role": "candidate" if index == len(selected) - 1 else "history",
            }
            for index, point in enumerate(selected)
        ],
    }
    return CandidateTrace.model_validate(data)


def candidate_fixture_json(candidate: CandidateTrace) -> str:
    return json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        policy = InvariantPolicy.model_validate(json.loads(args.policy.read_text(encoding="utf-8")))
        source = Trace.model_validate(json.loads(args.source.read_text(encoding="utf-8")))
        candidate = build_candidate_fixture(policy, source)
        args.output.write_text(candidate_fixture_json(candidate), encoding="utf-8", newline="\n")
    except (OSError, json.JSONDecodeError, ValidationError, EvaluationInputError) as exc:
        print(f"[build_candidate_fixture] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
