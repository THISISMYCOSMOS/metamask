#!/usr/bin/env python3
"""Build a live Agent Wallet floor-violation candidate from an approved policy."""
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
    encode_erc20_transfer,
    parse_rpc_quantity,
)


DEFAULT_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com"


def _transport(rpc_url: str):
    def send(payload):
        request = Request(
            rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "metamask-agent-wallet-reject/1.0"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # nosec B310 - explicit research RPC input
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("public RPC returned a non-object response")
        return value

    return send


def build(*, rpc_url: str, approved_bundle: dict, recipient: str, amount: int) -> dict:
    approval = approved_bundle["approval"]
    policy = approval["proposal"]["policy"]
    wallet = policy["walletAddress"].lower()
    token = policy["tokenAddress"].lower()
    chain_id = policy["chainId"]
    floor = int(policy["assetBalanceFloor"])
    if chain_id != 11155111 or amount <= 0 or amount > 1_000_000:
        raise ValueError("reject candidate is restricted to at most 1 Sepolia USDC")

    rpc = JsonRpcClient(transport=_transport(rpc_url))
    if parse_rpc_quantity(rpc.call("eth_chainId"), field="eth_chainId") != chain_id:
        raise ValueError("RPC chain does not match the approved policy")
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    if not isinstance(block, dict) or not isinstance(block.get("hash"), str):
        raise ValueError("latest block is missing its hash")
    block_number = parse_rpc_quantity(block.get("number"), field="block.number")
    balance = decode_erc20_uint256(
        rpc.call("eth_call", [{"to": token, "data": encode_erc20_balance_of(wallet)}, hex(block_number)])
    )
    if balance < amount or balance - amount >= floor:
        raise ValueError("candidate must be executable but leave the approved balance below its floor")
    nonce = parse_rpc_quantity(rpc.call("eth_getTransactionCount", [wallet, "pending"]), field="sender nonce")
    calldata = encode_erc20_transfer(recipient, amount)
    call = {"from": wallet, "to": token, "value": "0x0", "data": calldata}
    call_result = rpc.call("eth_call", [call, hex(block_number)])
    if call_result.lower() not in {"0x", "0x1", "0x" + "0" * 63 + "1"}:
        raise ValueError("ERC-20 transfer simulation did not return success")
    gas_estimate = parse_rpc_quantity(rpc.call("eth_estimateGas", [call]), field="gas estimate")
    candidate = {
        "schemaVersion": 1,
        "kind": "agent-wallet-direct-floor-candidate",
        "candidateId": "sepolia-agent-wallet-direct-usdc-floor-reject-001",
        "approvalSha256": canonical_sha256(approval),
        "policySha256": approval["policySha256"],
        "context": {
            "chainId": chain_id,
            "currentBlockNumber": str(block_number),
            "currentBlockHash": block["hash"].lower(),
            "senderNonce": str(nonce),
            "walletAddress": wallet,
            "tokenAddress": token,
            "assetBalance": str(balance),
        },
        "execution": {
            "chainId": chain_id,
            "fromAddress": wallet,
            "toAddress": token,
            "value": "0",
            "data": calldata,
            "gas": str((gas_estimate * 120 + 99) // 100),
            "nonce": str(nonce),
        },
        "effect": {
            "walletAddress": wallet,
            "tokenAddress": token,
            "recipientAddress": recipient.lower(),
            "transferAmount": str(amount),
            "afterAssetBalance": str(balance - amount),
        },
    }
    return {
        "schemaVersion": 1,
        "kind": "agent-wallet-direct-floor-bundle",
        "approval": approval,
        "candidate": candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approved_bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--recipient", default="0x9f85d965258624053734d8caea00dc3f452f3c27")
    parser.add_argument("--amount-base-units", type=int, default=500_000)
    parser.add_argument("--rpc-url-env", default="LIVE_EVIDENCE_RPC_URL")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        result = build(
            rpc_url=os.environ.get(args.rpc_url_env, "").strip() or DEFAULT_RPC_URL,
            approved_bundle=json.loads(args.approved_bundle.read_text(encoding="utf-8")),
            recipient=args.recipient,
            amount=args.amount_base_units,
        )
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite existing bundle: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed CLI boundary
        print(f"[build-agent-wallet-direct-reject] failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_sha256(result["candidate"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
