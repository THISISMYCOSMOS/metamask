#!/usr/bin/env python3
"""Verify and export the successful Agent Wallet Sepolia floor-guard run."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import canonical_sha256  # noqa: E402
from core.rpc_simulator import (  # noqa: E402
    JsonRpcClient,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    parse_rpc_quantity,
)
from verifier.evidence_bundle_models import EvidenceSource  # noqa: E402


DEFAULT_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _transport(rpc_url: str):
    def send(payload):
        request = Request(
            rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "metamask-agent-wallet-evidence/1.0"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # nosec B310 - explicit research RPC input
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("public RPC returned a non-object response")
        return value

    return send


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def capture(*, rpc_url: str, bundle: dict, runtime: dict, polling_id: str) -> EvidenceSource:
    candidate = bundle["candidate"]
    execution = candidate["execution"]
    effect = candidate["effect"]
    approval = bundle["approval"]
    tx_hash = runtime["transactionHash"].lower()

    if runtime.get("kind") != "agent-wallet-direct-broadcast-result" or not runtime.get("broadcast"):
        raise ValueError("runtime result is not a broadcast result")
    if runtime.get("candidateSha256") != canonical_sha256(candidate):
        raise ValueError("runtime result does not bind the candidate")

    rpc = JsonRpcClient(transport=_transport(rpc_url))
    chain_id = parse_rpc_quantity(rpc.call("eth_chainId"), field="eth_chainId")
    if chain_id != 11155111 or chain_id != execution["chainId"]:
        raise ValueError("capture requires the approved Ethereum Sepolia chain")
    transaction = rpc.call("eth_getTransactionByHash", [tx_hash])
    receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
    if not isinstance(transaction, dict) or not isinstance(receipt, dict):
        raise ValueError("transaction or receipt is not available")
    if receipt.get("status") != "0x1":
        raise ValueError("transaction receipt is not successful")
    exact_checks = {
        "from": transaction.get("from", "").lower() == execution["fromAddress"].lower(),
        "to": transaction.get("to", "").lower() == execution["toAddress"].lower(),
        "input": transaction.get("input", "").lower() == execution["data"].lower(),
        "value": parse_rpc_quantity(transaction.get("value"), field="transaction.value") == int(execution["value"]),
        "gas": parse_rpc_quantity(transaction.get("gas"), field="transaction.gas") == int(execution["gas"]),
        "nonce": parse_rpc_quantity(transaction.get("nonce"), field="transaction.nonce") == int(execution["nonce"]),
    }
    if not all(exact_checks.values()):
        raise ValueError("confirmed transaction differs from the approved exact execution")

    block_number = parse_rpc_quantity(receipt.get("blockNumber"), field="receipt.blockNumber")
    block_hash = receipt.get("blockHash", "").lower()
    if runtime["receipt"]["blockNumber"] != str(block_number) or runtime["receipt"]["blockHash"] != block_hash:
        raise ValueError("runtime receipt metadata differs from public RPC")
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise ValueError("receipt logs are missing")
    expected_log = {
        "address": effect["tokenAddress"].lower(),
        "topics": [
            TRANSFER_TOPIC,
            _topic_address(effect["walletAddress"]),
            _topic_address(effect["recipientAddress"]),
        ],
        "data": "0x" + int(effect["transferAmount"]).to_bytes(32, "big").hex(),
    }
    matching_logs = [
        log for log in logs
        if isinstance(log, dict)
        and log.get("address", "").lower() == expected_log["address"]
        and [topic.lower() for topic in log.get("topics", [])] == expected_log["topics"]
        and log.get("data", "").lower() == expected_log["data"]
    ]
    if len(matching_logs) != 1:
        raise ValueError("receipt does not contain exactly one expected Transfer event")
    post_balance = decode_erc20_uint256(
        rpc.call(
            "eth_call",
            [{"to": effect["tokenAddress"], "data": encode_erc20_balance_of(effect["walletAddress"])}, hex(block_number)],
        )
    )
    if str(post_balance) != effect["afterAssetBalance"] or runtime["receipt"]["assetBalanceAfter"] != str(post_balance):
        raise ValueError("post-state balance differs from the simulated effect")

    chain_record = {
        "transactionHash": tx_hash,
        "blockNumber": str(block_number),
        "blockHash": block_hash,
        "receiptStatus": "success",
        "exactExecutionChecks": exact_checks,
        "transferEvent": expected_log,
        "assetBalanceBefore": candidate["context"]["assetBalance"],
        "assetBalanceAfter": str(post_balance),
    }
    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": "rq3-sepolia-agent-wallet-direct-floor-accept",
            "case": "live-floor-accept",
            "claimScope": "application-level-testnet",
            "network": {
                "chainId": chain_id,
                "networkName": "ethereum-sepolia",
                "environment": "public-testnet",
                "rpcClass": "public-testnet",
                "blockNumber": str(block_number),
                "blockHash": block_hash,
            },
            "artifacts": [
                {
                    "name": "approval",
                    "evidenceClass": "generated",
                    "value": approval,
                    "source": "live Gemini proposal and exact-hash approval used by the direct Agent Wallet gate",
                },
                {
                    "name": "candidate",
                    "evidenceClass": "generated",
                    "value": candidate,
                    "source": "public Sepolia simulation snapshot bound to the exact direct ERC-20 request",
                },
                {
                    "name": "runtime-result",
                    "evidenceClass": "observed",
                    "value": runtime,
                    "source": "chain run-agent-wallet-direct broadcast result after CLI and receipt verification",
                },
                {
                    "name": "agent-wallet-request",
                    "evidenceClass": "observed",
                    "value": {
                        "pollingId": polling_id,
                        "status": "BROADCASTED",
                        "transactionHash": tx_hash,
                        "intent": "Send the exact approved Sepolia USDC research transfer",
                        "chainId": chain_id,
                    },
                    "source": "MetaMask Agent Wallet CLI request listing after MFA approval",
                },
                {
                    "name": "public-chain-verification",
                    "evidenceClass": "external-chain",
                    "value": chain_record,
                    "source": "independent public Ethereum Sepolia JSON-RPC verification; endpoint URL omitted",
                },
            ],
            "outcome": {
                "accepted": True,
                "broadcastAttempted": True,
                "txHash": tx_hash,
                "receiptStatus": "success",
                "walletNativeEnforcement": False,
            },
            "claims": [
                {
                    "path": "/artifacts/approval/value/proposal/compiler",
                    "evidenceClass": "observed",
                    "source": "live Gemini provider identity embedded in the approved proposal",
                },
                {
                    "path": "/artifacts/runtime-result/value/candidateSha256",
                    "evidenceClass": "observed",
                    "source": "runtime output bound to the exact candidate",
                },
                {
                    "path": "/artifacts/agent-wallet-request/value/status",
                    "evidenceClass": "observed",
                    "source": "MetaMask Agent Wallet CLI reported BROADCASTED after MFA approval",
                },
                {
                    "path": "/artifacts/public-chain-verification/value/transferEvent",
                    "evidenceClass": "external-chain",
                    "source": "one matching Sepolia ERC-20 Transfer log",
                },
                {
                    "path": "/artifacts/public-chain-verification/value/assetBalanceAfter",
                    "evidenceClass": "external-chain",
                    "source": "USDC balanceOf at the receipt block",
                },
            ],
            "limitations": [
                "The result-state guard is application-level; MetaMask Agent Wallet independently enforced its own Guard Mode and MFA but did not natively enforce assetBalanceFloor.",
                "This is one direct ERC-20 transfer on Ethereum Sepolia, not a signed Delegation Framework redemption or mainnet test.",
                "The exact-hash approval was submitted by the user-authorized research workflow and does not prove user comprehension or cryptographic approver identity.",
            ],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("runtime_result", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--polling-id", required=True)
    parser.add_argument("--rpc-url-env", default="LIVE_EVIDENCE_RPC_URL")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        source = capture(
            rpc_url=os.environ.get(args.rpc_url_env, "").strip() or DEFAULT_RPC_URL,
            bundle=json.loads(args.bundle.read_text(encoding="utf-8")),
            runtime=json.loads(args.runtime_result.read_text(encoding="utf-8")),
            polling_id=args.polling_id,
        )
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite existing source: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(source.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed evidence boundary
        print(f"[capture-agent-wallet-direct-accept] failed: {exc}", file=sys.stderr)
        return 1
    print(source.outcome.txHash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
