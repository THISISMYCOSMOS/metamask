#!/usr/bin/env python3
"""Validate and export the live Agent Wallet floor rejection and no-send audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import canonical_sha256  # noqa: E402
from verifier.evidence_bundle_models import EvidenceSource  # noqa: E402


def capture(*, bundle: dict, result: dict, audit: dict) -> EvidenceSource:
    candidate = bundle["candidate"]
    approval = bundle["approval"]
    policy = approval["proposal"]["policy"]
    if result.get("kind") != "agent-wallet-direct-preflight-result":
        raise ValueError("result is not a direct Agent Wallet preflight result")
    if result.get("eligibleForBroadcast") is not False or result.get("broadcast") is not False:
        raise ValueError("preflight result is not a rejection")
    if result.get("reasonCodes") != ["ASSET_BALANCE_FLOOR_VIOLATION"]:
        raise ValueError("preflight did not reject only for the expected floor violation")
    if result.get("candidateSha256") != canonical_sha256(candidate):
        raise ValueError("preflight result does not bind the exact candidate")
    if audit.get("beforeRequestIds") != audit.get("afterRequestIds") or audit.get("newRequestIds") != []:
        raise ValueError("Agent Wallet request list changed during the rejected broadcast invocation")
    if audit.get("transactionHash") is not None:
        raise ValueError("a rejected candidate must not have a transaction hash")
    before = int(candidate["context"]["assetBalance"])
    amount = int(candidate["effect"]["transferAmount"])
    after = int(candidate["effect"]["afterAssetBalance"])
    floor = int(policy["assetBalanceFloor"])
    if before - amount != after or after >= floor:
        raise ValueError("candidate arithmetic does not exhibit the claimed floor violation")

    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": "rq3-sepolia-agent-wallet-direct-floor-reject",
            "case": "live-floor-preflight-reject",
            "claimScope": "application-level-testnet",
            "network": {
                "chainId": candidate["context"]["chainId"],
                "networkName": "ethereum-sepolia",
                "environment": "public-testnet",
                "rpcClass": "public-testnet",
                "blockNumber": candidate["context"]["currentBlockNumber"],
                "blockHash": candidate["context"]["currentBlockHash"],
            },
            "artifacts": [
                {
                    "name": "approval",
                    "evidenceClass": "observed",
                    "value": approval,
                    "source": "same live Gemini proposal and exact-hash approval used by the successful Agent Wallet control",
                },
                {
                    "name": "candidate",
                    "evidenceClass": "generated",
                    "value": candidate,
                    "source": "live Sepolia Agent Wallet state and executable ERC-20 eth_call candidate",
                },
                {
                    "name": "preflight-result",
                    "evidenceClass": "observed",
                    "value": result,
                    "source": "direct Agent Wallet runtime simulation, context revalidation and deterministic decision",
                },
                {
                    "name": "no-send-audit",
                    "evidenceClass": "observed",
                    "value": audit,
                    "source": "Agent Wallet request ids captured before and after rejected --broadcast invocation",
                },
                {
                    "name": "balance-arithmetic",
                    "evidenceClass": "generated",
                    "value": {
                        "assetBalanceBefore": str(before),
                        "transferAmount": str(amount),
                        "assetBalanceAfter": str(after),
                        "assetBalanceFloor": str(floor),
                        "afterBelowFloor": after < floor,
                    },
                    "source": "deterministic uint256 balance-floor arithmetic",
                },
            ],
            "outcome": {
                "accepted": False,
                "broadcastAttempted": False,
                "txHash": None,
                "receiptStatus": "not-broadcast",
                "walletNativeEnforcement": False,
            },
            "claims": [
                {
                    "path": "/artifacts/preflight-result/value/reasonCodes",
                    "evidenceClass": "observed",
                    "source": "deterministic direct runtime returned ASSET_BALANCE_FLOOR_VIOLATION",
                },
                {
                    "path": "/artifacts/no-send-audit/value/newRequestIds",
                    "evidenceClass": "observed",
                    "source": "Agent Wallet request list was unchanged by the rejected broadcast invocation",
                },
                {
                    "path": "/outcome/txHash",
                    "evidenceClass": "observed",
                    "source": "no Agent Wallet request or transaction hash was created",
                },
            ],
            "limitations": [
                "The no-send audit compares Agent Wallet CLI request identifiers around one rejected invocation; it is not wallet-native assetBalanceFloor enforcement.",
                "This is one direct ERC-20 candidate on Ethereum Sepolia and not a signed Delegation Framework redemption.",
                "The successful control changed the wallet from 1.0 to 0.9 USDC before this rejection candidate was captured.",
            ],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("audit", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        source = capture(
            bundle=json.loads(args.bundle.read_text(encoding="utf-8")),
            result=json.loads(args.result.read_text(encoding="utf-8")),
            audit=json.loads(args.audit.read_text(encoding="utf-8")),
        )
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite existing source: {args.output}")
        args.output.write_text(
            json.dumps(source.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed evidence boundary
        print(f"[capture-agent-wallet-direct-reject] failed: {exc}", file=sys.stderr)
        return 1
    print(source.artifacts[2].value["candidateSha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
