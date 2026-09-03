#!/usr/bin/env python3
"""Build strict source records for the committed research evidence cases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "verifier"
for path in (ROOT, VERIFIER):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.canonical import canonical_sha256  # noqa: E402
from candidate_models import CandidateTrace  # noqa: E402
from evidence_bundle_models import EvidenceSource  # noqa: E402
from evaluate_approved_candidate import evaluate_approved_candidate  # noqa: E402
from synthesis_models import PolicyApproval  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _artifact(name: str, evidence_class: str, value: dict, source: str) -> dict:
    return {"name": name, "evidenceClass": evidence_class, "value": value, "source": source}


def _source_name(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def build_offline_source(
    approval_path: Path,
    candidate_path: Path,
    *,
    case: str,
    provenance_path: Path | None = None,
) -> EvidenceSource:
    approval = PolicyApproval.model_validate(_read(approval_path))
    candidate = CandidateTrace.model_validate(_read(candidate_path))
    decision = evaluate_approved_candidate(approval, candidate)
    expected_accept = case == "offline-benign-accept"
    if decision["accepted"] != expected_accept:
        raise ValueError(f"{case} decision does not match the required outcome")

    artifacts = [
        _artifact(
            "approval",
            "configured",
            approval.model_dump(mode="json"),
            _source_name(approval_path),
        ),
        _artifact(
            "candidate",
            "generated" if expected_accept else "observed",
            candidate.model_dump(mode="json"),
            _source_name(candidate_path),
        ),
        _artifact("decision", "generated", decision, "verifier/evaluate_approved_candidate.py"),
    ]
    claims = [
        {
            "path": "/artifacts/decision/value/accepted",
            "evidenceClass": "generated",
            "source": "deterministic approved candidate evaluator",
        },
        {
            "path": "/artifacts/decision/value/evaluations",
            "evidenceClass": "generated",
            "source": "deterministic integer-only invariant evaluation",
        },
    ]
    limitations = [
        "This is an offline pinned-fork evaluation and does not prove wallet-native enforcement or a live broadcast.",
    ]
    if provenance_path is not None:
        provenance = _read(provenance_path)
        artifacts.append(
            _artifact(
                "counterfactual-provenance",
                "generated",
                provenance,
                _source_name(provenance_path),
            )
        )
        claims.append(
            {
                "path": "/artifacts/candidate/value/transitions",
                "evidenceClass": "inferred",
                "source": "constructed counterfactual transformation, not an on-chain observation",
            }
        )
        limitations.append(
            "The benign sequence is a constructed counterfactual that shares the G3 fork, oracle snapshot, starting value and timestamps; it was not executed on-chain."
        )
    else:
        claims.append(
            {
                "path": "/artifacts/candidate/value/sourceTraceHashedContentSha256",
                "evidenceClass": "observed",
                "source": "committed mainnet-fork G3 trace adapter",
            }
        )

    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": "rq3-offline-benign-accept" if expected_accept else "rq3-offline-g3-reject",
            "case": case,
            "claimScope": "offline-counterfactual-control" if expected_accept else "offline-counterexample",
            "network": {
                "chainId": candidate.fork.chainId,
                "networkName": "ethereum-mainnet-fork",
                "environment": "pinned-mainnet-fork",
                "rpcClass": "pinned-fork",
                "blockNumber": candidate.fork.blockNumber,
                "blockHash": candidate.fork.blockHash.lower(),
            },
            "artifacts": artifacts,
            "outcome": {
                "accepted": expected_accept,
                "broadcastAttempted": False,
                "txHash": None,
                "receiptStatus": "not-applicable",
                "walletNativeEnforcement": False,
            },
            "claims": claims,
            "limitations": limitations,
        }
    )


def build_historical_live_accept_source(record_path: Path) -> EvidenceSource:
    record = _read(record_path)
    transaction = record.get("transaction")
    policy = record.get("reportedPolicy")
    if not isinstance(transaction, dict) or not isinstance(policy, dict):
        raise ValueError("historical live record requires transaction and reportedPolicy objects")
    if transaction.get("status") != "success" or not transaction.get("transactionHash"):
        raise ValueError("historical live accept record requires a successful transaction")
    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": "rq3-sepolia-historical-accept",
            "case": "live-floor-accept",
            "claimScope": "application-level-testnet",
            "network": record["network"],
            "artifacts": [
                _artifact("reported-policy", "reported", policy, _source_name(record_path)),
                _artifact("external-transaction", "external-chain", transaction, _source_name(record_path)),
            ],
            "outcome": {
                "accepted": True,
                "broadcastAttempted": True,
                "txHash": transaction["transactionHash"],
                "receiptStatus": "success",
                "walletNativeEnforcement": False,
            },
            "claims": [
                {
                    "path": "/artifacts/external-transaction/value/transactionHash",
                    "evidenceClass": "external-chain",
                    "source": "public Ethereum Sepolia transaction and receipt",
                },
                {
                    "path": "/artifacts/external-transaction/value/assetBalanceAfter",
                    "evidenceClass": "external-chain",
                    "source": "public Ethereum Sepolia post-state eth_call",
                },
                {
                    "path": "/artifacts/reported-policy/value/assetBalanceFloor",
                    "evidenceClass": "reported",
                    "source": "historical local execution record; not encoded on-chain",
                },
                {
                    "path": "/artifacts/reported-policy/value/assetBalanceBefore",
                    "evidenceClass": "inferred",
                    "source": "reconstructed from post-state and the single related outgoing Transfer event",
                },
            ],
            "limitations": [
                "The original proposal, approval and plan hashes were not durably stored and cannot be reconstructed from the public chain.",
                "This bundle proves the successful Sepolia transfer and post-state, but does not independently prove traversal of the historical application gate.",
                "The pre-transaction balance is reconstructed rather than directly read from an archive-state RPC.",
                "The policy was application-level and was not installed as wallet-native or contract-native enforcement.",
            ],
        }
    )


def _write(path: Path, source: EvidenceSource, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing source: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(source.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    offline = subparsers.add_parser("offline")
    offline.add_argument("case", choices=("offline-g3-reject", "offline-benign-accept"))
    offline.add_argument("approval", type=Path)
    offline.add_argument("candidate", type=Path)
    offline.add_argument("output", type=Path)
    offline.add_argument("--provenance", type=Path)
    historical = subparsers.add_parser("historical-live-accept")
    historical.add_argument("record", type=Path)
    historical.add_argument("output", type=Path)
    for command in (offline, historical):
        command.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "offline":
            source = build_offline_source(
                args.approval,
                args.candidate,
                case=args.case,
                provenance_path=args.provenance,
            )
        else:
            source = build_historical_live_accept_source(args.record)
        _write(args.output, source, overwrite=args.overwrite)
    except Exception as exc:  # noqa: BLE001 - fail-closed CLI boundary
        print(f"[build-evidence-source] failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_sha256(source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
