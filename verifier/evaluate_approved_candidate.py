#!/usr/bin/env python3
"""Evaluate a candidate using only a validated policy-approval artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from candidate_models import CandidateTrace
from evaluate_candidate import evaluate_candidate
from evaluate_invariants import EvaluationInputError
from synthesis_models import PolicyApproval, canonical_model_sha256


def evaluate_approved_candidate(
    approval: PolicyApproval,
    candidate: CandidateTrace,
    *,
    allow_test_fixture: bool = False,
) -> dict[str, Any]:
    if approval.approvalScope != "user" and not allow_test_fixture:
        raise EvaluationInputError("runtime evaluation requires a user-scoped approval artifact")
    decision = evaluate_candidate(approval.proposal.policy, candidate)
    return {
        "schemaVersion": 1,
        "kind": "approved-candidate-evaluation",
        "approvalScope": approval.approvalScope,
        "approvedBy": approval.approvedBy,
        "approvalSha256": canonical_model_sha256(approval),
        "proposalSha256": approval.proposalSha256,
        "policySha256": approval.policySha256,
        "candidateTraceId": decision["candidateTraceId"],
        "candidateTraceSha256": decision["candidateTraceSha256"],
        "accepted": decision["accepted"],
        "evaluations": decision["evaluations"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approval", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--allow-test-fixture", action="store_true")
    parser.add_argument(
        "--expect",
        choices=("accept", "reject"),
        default="accept",
        help="required decision for exit 0; defaults to accept for fail-closed integration",
    )
    args = parser.parse_args()
    try:
        approval = PolicyApproval.model_validate(
            json.loads(args.approval.read_text(encoding="utf-8"))
        )
        candidate = CandidateTrace.model_validate(
            json.loads(args.candidate.read_text(encoding="utf-8"))
        )
        report = evaluate_approved_candidate(
            approval,
            candidate,
            allow_test_fixture=args.allow_test_fixture,
        )
    except (OSError, json.JSONDecodeError, ValidationError, EvaluationInputError) as exc:
        print(f"[evaluate_approved_candidate] invalid input: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    accepted = bool(report["accepted"])
    if args.expect == "accept":
        return 0 if accepted else 1
    return 0 if not accepted else 1


if __name__ == "__main__":
    sys.exit(main())
