#!/usr/bin/env python3
"""Validate an LLM JSON response and compile it into an unapproved policy proposal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))

from synthesis_models import (  # noqa: E402
    IntentCompilerRequest,
    LlmPolicyResponse,
    canonical_model_sha256,
)
from synthesis_workflow import compile_policy_proposal, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        request = IntentCompilerRequest.model_validate(
            json.loads(args.request.read_text(encoding="utf-8"))
        )
        response = LlmPolicyResponse.model_validate(
            json.loads(args.response.read_text(encoding="utf-8"))
        )
        proposal = compile_policy_proposal(request, response)
        write_artifact(args.output, proposal)
        print(canonical_model_sha256(proposal))
    except Exception as exc:  # noqa: BLE001 - CLI normalizes all invalid input to exit 1.
        print(f"[compile_response] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
