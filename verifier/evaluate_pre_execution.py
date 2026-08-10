#!/usr/bin/env python3
"""Evaluate an exact public transaction request before any wallet broadcast."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evaluate_approved_candidate import evaluate_approved_candidate
from evaluate_invariants import EvaluationInputError
from pre_execution_models import PreExecutionCandidate
from synthesis_models import PolicyApproval, canonical_model_sha256


def canonical_json_sha256(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def evaluate_pre_execution(
    approval: PolicyApproval,
    candidate: PreExecutionCandidate,
) -> dict[str, Any]:
    if approval.approvalScope != "user":
        raise EvaluationInputError("pre-execution evaluation requires user-scoped approval")
    evaluation = evaluate_approved_candidate(approval, candidate.portfolioCandidate)
    return {
        "schemaVersion": 1,
        "kind": "pre-execution-decision",
        "candidateId": candidate.candidateId,
        "candidateSha256": canonical_json_sha256(candidate),
        "executionSha256": canonical_json_sha256(candidate.execution),
        "historySha256": candidate.historySha256,
        "approvalSha256": canonical_model_sha256(approval),
        "policySha256": approval.policySha256,
        "accepted": evaluation["accepted"],
        "evaluations": evaluation["evaluations"],
    }


def _read_candidate(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approval", type=Path)
    parser.add_argument("candidate", help="candidate JSON path or - for stdin")
    args = parser.parse_args()
    try:
        approval = PolicyApproval.model_validate(
            json.loads(args.approval.read_text(encoding="utf-8"))
        )
        candidate = PreExecutionCandidate.model_validate(_read_candidate(args.candidate))
        decision = evaluate_pre_execution(approval, candidate)
    except (OSError, json.JSONDecodeError, ValidationError, EvaluationInputError) as exc:
        print(f"[evaluate_pre_execution] invalid input: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if decision["accepted"] else 2


if __name__ == "__main__":
    sys.exit(main())
