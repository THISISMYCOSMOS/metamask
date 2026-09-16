#!/usr/bin/env python3
"""Create a deterministic provider-neutral request for an LLM policy compiler."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))

from invariant_models import InvariantPolicy  # noqa: E402
from synthesis_workflow import create_intent_request, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intent", type=Path)
    parser.add_argument("policy_template", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--request-id", default="mvp-intent-001")
    args = parser.parse_args()
    try:
        intent_text = args.intent.read_text(encoding="utf-8").strip()
        template = InvariantPolicy.model_validate(
            json.loads(args.policy_template.read_text(encoding="utf-8"))
        )
        request = create_intent_request(
            request_id=args.request_id,
            intent_text=intent_text,
            policy_template=template,
        )
        write_artifact(args.output, request)
    except Exception as exc:  # noqa: BLE001 - CLI normalizes all invalid input to exit 1.
        print(f"[create_request] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
