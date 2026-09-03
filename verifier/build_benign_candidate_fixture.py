#!/usr/bin/env python3
"""Build a labeled counterfactual benign control for the G3 policy evaluator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "verifier"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(VERIFIER) not in sys.path:
    sys.path.insert(0, str(VERIFIER))

from core.canonical import canonical_sha256  # noqa: E402
from candidate_models import CandidateTrace  # noqa: E402
from evaluate_candidate import evaluate_candidate  # noqa: E402
from evaluate_invariants import (  # noqa: E402
    USDC_TOKEN_DECIMALS,
    _asset_value_1e18,
    canonical_trace_hashed_sha256,
)
from invariant_models import InvariantPolicy  # noqa: E402
from models import Trace  # noqa: E402


DEFAULT_STEP_AMOUNT_BASE_UNITS = 300_000_000
DEFAULT_TRANSITION_COUNT = 5


def build_benign_candidate(
    policy: InvariantPolicy,
    trace: Trace,
    *,
    step_amount_base_units: int = DEFAULT_STEP_AMOUNT_BASE_UNITS,
    transition_count: int = DEFAULT_TRANSITION_COUNT,
) -> tuple[dict, CandidateTrace]:
    """Construct a counterfactual series; never label it as observed G3 output."""
    if policy.traceKind != "portfolio-candidate":
        raise ValueError("benign control requires a portfolio-candidate policy")
    if step_amount_base_units <= 0 or step_amount_base_units >= 1 << 256:
        raise ValueError("step amount must be a positive uint256 base-unit value")
    if transition_count < 1 or transition_count > len(trace.hashed.steps):
        raise ValueError("transition count is outside the source trace")
    fork = trace.hashed.fork
    if (
        policy.fork.chainId != fork.chainId
        or policy.fork.blockNumber != fork.blockNumber
        or policy.fork.blockHash.lower() != fork.blockHash.lower()
    ):
        raise ValueError("policy and source trace fork bindings differ")

    source_trace_hash = canonical_trace_hashed_sha256(trace)
    per_step_loss = _asset_value_1e18(
        step_amount_base_units,
        USDC_TOKEN_DECIMALS,
        int(trace.hashed.oracle.usdcUsd.answer),
    )
    initial_value = int(trace.hashed.result.startingPortfolio.totalValue1e18)
    if per_step_loss * transition_count > initial_value:
        raise ValueError("counterfactual transfer series would make portfolio value negative")

    timestamps = [int(step.timestamp) for step in trace.hashed.steps[:transition_count]]
    source = {
        "schemaVersion": 1,
        "kind": "counterfactual-benign-control-source",
        "basedOnTraceHashedContentSha256": source_trace_hash,
        "reuse": ["fork-binding", "oracle-snapshot", "starting-portfolio-value", "step-timestamps"],
        "transformation": {
            "description": "Replace each G3 transfer with a configured smaller USDC amount and recompute portfolio values using the pinned USDC/USD oracle.",
            "stepAmountBaseUnits": str(step_amount_base_units),
            "transitionCount": transition_count,
            "perStepLossValue1e18": str(per_step_loss),
        },
        "classification": "constructed-counterfactual-not-onchain-observation",
    }
    source_hash = canonical_sha256(source)
    transitions: list[dict] = []
    before = initial_value
    for index, timestamp in enumerate(timestamps):
        after = before - per_step_loss
        transitions.append(
            {
                "transitionIndex": index,
                "timestamp": str(timestamp),
                "beforeValue1e18": str(before),
                "afterValue1e18": str(after),
                "role": "candidate" if index == transition_count - 1 else "history",
            }
        )
        before = after

    candidate = CandidateTrace.model_validate(
        {
            "schemaVersion": 1,
            "kind": "portfolio-candidate",
            "traceId": "g3-counterfactual-benign-control",
            "fork": {
                "chainId": fork.chainId,
                "blockNumber": fork.blockNumber,
                "blockHash": fork.blockHash.lower(),
            },
            "valuation": {
                "unit": "usd-1e18",
                "mode": "pinned-fork-snapshot",
                "priceBlockNumber": fork.blockNumber,
                "priceBlockHash": fork.blockHash.lower(),
            },
            "sourceTraceHashedContentSha256": source_hash,
            "coverageStartTimestamp": str(timestamps[0]),
            "initialValue1e18": str(initial_value),
            "transitions": transitions,
        }
    )
    decision = evaluate_candidate(policy, candidate)
    if not decision["accepted"]:
        raise ValueError("configured benign control is not accepted by the supplied policy")
    return source, candidate


def _write_json(path: Path, value: dict, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("candidate_output", type=Path)
    parser.add_argument("source_output", type=Path)
    parser.add_argument("--step-amount-base-units", type=int, default=DEFAULT_STEP_AMOUNT_BASE_UNITS)
    parser.add_argument("--transition-count", type=int, default=DEFAULT_TRANSITION_COUNT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        policy = InvariantPolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
        trace = Trace.model_validate_json(args.trace.read_text(encoding="utf-8"))
        source, candidate = build_benign_candidate(
            policy,
            trace,
            step_amount_base_units=args.step_amount_base_units,
            transition_count=args.transition_count,
        )
        _write_json(args.source_output, source, overwrite=args.overwrite)
        _write_json(args.candidate_output, candidate.model_dump(mode="json"), overwrite=args.overwrite)
    except Exception as exc:  # noqa: BLE001 - fail-closed CLI boundary
        print(f"[build-benign-candidate] failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_sha256(candidate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
