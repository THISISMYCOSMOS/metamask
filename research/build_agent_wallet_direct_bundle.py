#!/usr/bin/env python3
"""Build a live Sepolia Agent Wallet direct-transfer bundle without broadcasting."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
for path in (ROOT, UI):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.canonical import canonical_sha256  # noqa: E402
from core.rpc_simulator import (  # noqa: E402
    JsonRpcClient,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    encode_erc20_transfer,
    parse_rpc_quantity,
)
import server  # noqa: E402


DEFAULT_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com"
CHAIN_ID = 11155111
TOKEN = "0x1c7d4b196cb0c7b01d743fbc6116a902379c7238"
WALLET = "0xb11539d7b6423c4523e1fba35953154b6b393df9"
RECIPIENT = "0x9f85d965258624053734d8caea00dc3f452f3c27"
FLOOR = 500_000
AMOUNT = 100_000


def _transport(rpc_url: str):
    def send(payload):
        request = Request(
            rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "metamask-agent-wallet-research/1.0"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # nosec B310 - explicit research RPC input
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("public RPC returned a non-object response")
        return value

    return send


def build(*, rpc_url: str, wallet: str, token: str, recipient: str, floor: int, amount: int) -> dict:
    if floor < 0 or amount <= 0:
        raise ValueError("floor must be non-negative and amount must be positive")
    if amount > 1_000_000:
        raise ValueError("research transfer is capped at 1 USDC")

    rpc = JsonRpcClient(transport=_transport(rpc_url))
    chain_id = parse_rpc_quantity(rpc.call("eth_chainId"), field="eth_chainId")
    if chain_id != CHAIN_ID:
        raise ValueError("direct Agent Wallet evidence is restricted to Ethereum Sepolia")
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    if not isinstance(block, dict) or not isinstance(block.get("hash"), str):
        raise ValueError("latest block is missing its hash")
    block_number = parse_rpc_quantity(block.get("number"), field="block.number")
    block_tag = hex(block_number)
    balance = decode_erc20_uint256(
        rpc.call("eth_call", [{"to": token, "data": encode_erc20_balance_of(wallet)}, block_tag])
    )
    if balance < amount or balance - amount < floor:
        raise ValueError("current balance cannot satisfy the requested transfer and floor")
    nonce = parse_rpc_quantity(
        rpc.call("eth_getTransactionCount", [wallet, "pending"]),
        field="sender nonce",
    )
    calldata = encode_erc20_transfer(recipient, amount)
    call = {"from": wallet, "to": token, "value": "0x0", "data": calldata}
    call_result = rpc.call("eth_call", [call, block_tag])
    if call_result.lower() not in {"0x", "0x1", "0x" + "0" * 63 + "1"}:
        raise ValueError("ERC-20 transfer simulation did not return success")
    gas_estimate = parse_rpc_quantity(rpc.call("eth_estimateGas", [call]), field="gas estimate")
    gas_limit = (gas_estimate * 120 + 99) // 100

    os.environ.update(
        {
            "METAMASK_CHAIN_ID": str(chain_id),
            "METAMASK_TOKEN_ADDRESS": token,
            "METAMASK_TOKEN_SYMBOL": "USDC",
            "METAMASK_TOKEN_DECIMALS": "6",
        }
    )
    server.policy_state = server.build_live_policy_flow(
        "이 Sepolia Agent Wallet에서 USDC를 0.5개 이상 남겨줘",
        wallet_binding={"walletAddress": wallet, "chainId": chain_id},
    )
    proposal_hash = server.policy_state.get("proposalSha256")
    if not isinstance(proposal_hash, str):
        raise ValueError("Gemini did not produce an approvable balance-floor policy")
    server.policy_state = server.approve_current_policy(f"APPROVE {proposal_hash}")
    approval = server.policy_state.get("approval")
    approval_hash = server.policy_state.get("approvalSha256")
    if not isinstance(approval, dict) or not isinstance(approval_hash, str):
        raise ValueError("exact-hash approval record was not created")
    policy = approval["proposal"]["policy"]
    if int(policy["assetBalanceFloor"]) != floor:
        raise ValueError("compiled floor does not equal the explicitly requested base-unit floor")

    candidate = {
        "schemaVersion": 1,
        "kind": "agent-wallet-direct-floor-candidate",
        "candidateId": "sepolia-agent-wallet-direct-usdc-001",
        "approvalSha256": approval_hash,
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
            "gas": str(gas_limit),
            "nonce": str(nonce),
        },
        "effect": {
            "walletAddress": wallet,
            "tokenAddress": token,
            "recipientAddress": recipient,
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
    parser.add_argument("output", type=Path)
    parser.add_argument("--rpc-url-env", default="LIVE_EVIDENCE_RPC_URL")
    parser.add_argument("--wallet", default=WALLET)
    parser.add_argument("--token", default=TOKEN)
    parser.add_argument("--recipient", default=RECIPIENT)
    parser.add_argument("--floor-base-units", type=int, default=FLOOR)
    parser.add_argument("--amount-base-units", type=int, default=AMOUNT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        result = build(
            rpc_url=os.environ.get(args.rpc_url_env, "").strip() or DEFAULT_RPC_URL,
            wallet=args.wallet.lower(),
            token=args.token.lower(),
            recipient=args.recipient.lower(),
            floor=args.floor_base_units,
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
        print(f"[build-agent-wallet-direct-bundle] failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_sha256(result["candidate"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
