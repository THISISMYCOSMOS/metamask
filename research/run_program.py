#!/usr/bin/env python3
"""Two-step RQ2/RQ3 CLI. Compile first; decide only after exact-hash approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.gemini_invariant_compiler import GeminiConfig, GeminiInvariantCompiler
from research.models import BenchmarkDataset
from research.program import approve_and_decide, build_proposal

from candidate_models import CandidateTrace
from invariant_models import ForkBinding
from synthesis_models import PolicyProposal, canonical_model_sha256


def compile_command(args: argparse.Namespace) -> int:
    dataset = BenchmarkDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    matches = [case for case in dataset.cases if case.caseId == args.case_id]
    if len(matches) != 1:
        raise ValueError("case id must identify exactly one benchmark case")
    candidate = CandidateTrace.model_validate_json(args.candidate.read_text(encoding="utf-8"))
    config = GeminiConfig.from_env()
    compiler = GeminiInvariantCompiler(config)
    output = compiler.compile(matches[0])
    proposal = build_proposal(
        matches[0],
        output,
        fork=ForkBinding.model_validate(candidate.fork.model_dump(mode="json")),
        compiler_provider="google-gemini",
        compiler_model=config.model,
    )
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n")
    print(f"APPROVE {canonical_model_sha256(proposal)}")
    return 0


def decide_command(args: argparse.Namespace) -> int:
    proposal = PolicyProposal.model_validate_json(args.proposal.read_text(encoding="utf-8"))
    candidate = CandidateTrace.model_validate_json(args.candidate.read_text(encoding="utf-8"))
    report = approve_and_decide(proposal, confirmation=args.confirmation, candidate=candidate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["accepted"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("dataset", type=Path)
    compile_parser.add_argument("case_id")
    compile_parser.add_argument("candidate", type=Path)
    compile_parser.add_argument("output", type=Path)
    compile_parser.set_defaults(handler=compile_command)
    decide_parser = subparsers.add_parser("decide")
    decide_parser.add_argument("proposal", type=Path)
    decide_parser.add_argument("candidate", type=Path)
    decide_parser.add_argument("confirmation")
    decide_parser.set_defaults(handler=decide_command)
    args = parser.parse_args()
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001 - one fail-closed CLI boundary
        print(f"[research-program] failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
