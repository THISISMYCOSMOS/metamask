#!/usr/bin/env python3
"""Create a user approval artifact only after exact proposal-hash confirmation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))

from synthesis_models import PolicyProposal, canonical_model_sha256  # noqa: E402
from synthesis_workflow import approve_policy_proposal, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposal", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--approved-by", required=True)
    args = parser.parse_args()
    try:
        proposal = PolicyProposal.model_validate(
            json.loads(args.proposal.read_text(encoding="utf-8"))
        )
        approval = approve_policy_proposal(
            proposal,
            confirmation=args.confirm,
            approved_by=args.approved_by,
            approval_scope="user",
        )
        write_artifact(args.output, approval)
        print(canonical_model_sha256(approval))
    except Exception as exc:  # noqa: BLE001 - CLI normalizes all invalid input to exit 1.
        print(f"[approve_policy] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
