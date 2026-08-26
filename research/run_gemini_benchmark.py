#!/usr/bin/env python3
"""Run the fixed RQ2 benchmark through Gemini. Requires explicit live configuration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.gemini_invariant_compiler import GeminiConfig, GeminiInvariantCompiler
from research.models import BenchmarkDataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    dataset = BenchmarkDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    compiler = GeminiInvariantCompiler(GeminiConfig.from_env())
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        for case in dataset.cases:
            output = compiler.compile(case)
            stream.write(json.dumps({"caseId": case.caseId, "output": output.model_dump(mode="json")}, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
