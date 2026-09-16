#!/usr/bin/env python3
"""Capture a real Sepolia preflight floor rejection without broadcasting."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
VERIFIER = ROOT / "verifier"
for path in (ROOT, UI, VERIFIER):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.rpc_simulator import (  # noqa: E402
    JsonRpcClient,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    encode_erc20_transfer,
    parse_rpc_quantity,
)
from evidence_bundle_models import EvidenceSource  # noqa: E402
import server  # noqa: E402


DEFAULT_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com"
DEFAULT_CHAIN_ID = 11155111
DEFAULT_TOKEN = "0x1c7d4b196cb0c7b01d743fbc6116a902379c7238"
DEFAULT_WALLET = "0x9f85d965258624053734d8caea00dc3f452f3c27"
DEFAULT_RECIPIENT = "0xb11539d7b6423c4523e1fba35953154b6b393df9"
DEFAULT_AMOUNT = 25_000_000


def _public_rpc_transport(rpc_url: str):
    def send(payload):
        request = Request(
            rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "metamask-research-evidence/1.0"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:  # nosec B310 - explicit research RPC input
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("public RPC returned a non-object response")
        return value

    return send


def capture(
    *,
    rpc_url: str,
    wallet: str,
    token: str,
    recipient: str,
    amount: int,
) -> EvidenceSource:
    rpc = JsonRpcClient(transport=_public_rpc_transport(rpc_url))
    chain_id = parse_rpc_quantity(rpc.call("eth_chainId"), field="eth_chainId")
    if chain_id != DEFAULT_CHAIN_ID:
        raise ValueError("capture requires Ethereum Sepolia")
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    if not isinstance(block, dict):
        raise ValueError("latest block is missing")
    block_number_hex = block.get("number")
    block_hash = block.get("hash")
    block_number = parse_rpc_quantity(block_number_hex, field="block.number")
    if not isinstance(block_hash, str):
        raise ValueError("latest block hash is missing")
    block_tag = hex(block_number)
    balance = decode_erc20_uint256(
        rpc.call("eth_call", [{"to": token, "data": encode_erc20_balance_of(wallet)}, block_tag])
    )
    nonce = parse_rpc_quantity(
        rpc.call("eth_getTransactionCount", [wallet, block_tag]),
        field="sender nonce",
    )
    transfer_data = encode_erc20_transfer(recipient, amount)
    call = {"from": wallet, "to": token, "value": "0x0", "data": transfer_data}
    transfer_call_result = rpc.call("eth_call", [call, block_tag])
    gas_limit = parse_rpc_quantity(rpc.call("eth_estimateGas", [call]), field="gas estimate")

    os.environ.update(
        {
            "METAMASK_CHAIN_ID": str(chain_id),
            "METAMASK_TOKEN_ADDRESS": token,
            "METAMASK_TOKEN_SYMBOL": "USDC",
            "METAMASK_TOKEN_DECIMALS": "6",
        }
    )
    server.policy_state = server.build_live_policy_flow(
        "USDC를 20개 이상 남겨줘",
        wallet_binding={"walletAddress": wallet, "chainId": chain_id},
    )
    proposal_hash = server.policy_state["proposalSha256"]
    if not isinstance(proposal_hash, str):
        raise ValueError("Gemini did not produce an approvable balance-floor policy")
    server.policy_state = server.approve_current_policy(f"APPROVE {proposal_hash}")
    state = server.authorize_metamask_policy(
        wallet,
        chain_id,
        recipient,
        str(amount),
        str(gas_limit),
        str(balance),
        str(nonce),
        transfer_call_result,
    )
    decision = state["candidateEvaluation"]["decision"]
    transaction = state["transaction"]
    policy = state["approval"]["proposal"]["policy"]
    if decision["accepted"] or "ASSET_BALANCE_FLOOR_VIOLATION" not in decision["reasonCodes"]:
        raise ValueError("the captured candidate did not reach the expected floor rejection")
    if transaction["walletRequest"] is not None or transaction["transactionHash"] is not None:
        raise ValueError("a rejected preflight unexpectedly created a wallet request or transaction hash")

    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": "rq3-sepolia-live-floor-reject",
            "case": "live-floor-preflight-reject",
            "claimScope": "application-level-testnet",
            "network": {
                "chainId": chain_id,
                "networkName": "ethereum-sepolia",
                "environment": "public-testnet",
                "rpcClass": "public-testnet",
                "blockNumber": str(block_number),
                "blockHash": block_hash.lower(),
            },
            "artifacts": [
                {
                    "name": "request",
                    "evidenceClass": "observed",
                    "value": state["request"],
                    "source": "live Gemini application compile request",
                },
                {
                    "name": "proposal",
                    "evidenceClass": "observed",
                    "value": state["proposal"],
                    "source": "live Gemini structured output after local schema validation",
                },
                {
                    "name": "approval",
                    "evidenceClass": "generated",
                    "value": state["approval"],
                    "source": "exact proposal-hash approval submitted by the authorized research capture",
                },
                {
                    "name": "rpc-observation",
                    "evidenceClass": "external-chain",
                    "value": {
                        "blockNumber": str(block_number),
                        "blockHash": block_hash.lower(),
                        "assetBalance": str(balance),
                        "senderNonce": str(nonce),
                        "recipientAddress": recipient.lower(),
                        "amountBaseUnits": str(amount),
                        "gasEstimate": str(gas_limit),
                        "transferCallResult": transfer_call_result,
                    },
                    "source": "public Ethereum Sepolia JSON-RPC reads; endpoint URL omitted",
                },
                {
                    "name": "plan-decision",
                    "evidenceClass": "generated",
                    "value": {
                        "policy": policy,
                        "decision": decision,
                        "transaction": transaction,
                    },
                    "source": "ui/server.py authorize_metamask_policy application gate",
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
                    "path": "/artifacts/proposal/value/compiler",
                    "evidenceClass": "observed",
                    "source": "live Gemini API response bound into the proposal",
                },
                {
                    "path": "/artifacts/rpc-observation/value/assetBalance",
                    "evidenceClass": "external-chain",
                    "source": "Sepolia balanceOf at the captured block",
                },
                {
                    "path": "/artifacts/plan-decision/value/decision/reasonCodes",
                    "evidenceClass": "generated",
                    "source": "deterministic application-level floor evaluator",
                },
                {
                    "path": "/outcome/broadcastAttempted",
                    "evidenceClass": "observed",
                    "source": "rejection returned no walletRequest and the capture script has no send operation",
                },
            ],
            "limitations": [
                "This is an application-level preflight gate and is bypassable outside the integrated client path.",
                "No MetaMask confirmation or eth_sendTransaction call was made for the rejected candidate.",
                "The exact-hash approval was submitted by an automated user-authorized research capture and is not evidence of user comprehension or cryptographic identity.",
            ],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rpc-url-env", default="LIVE_EVIDENCE_RPC_URL")
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--amount-base-units", type=int, default=DEFAULT_AMOUNT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        rpc_url = os.environ.get(args.rpc_url_env, "").strip() or DEFAULT_RPC_URL
        source = capture(
            rpc_url=rpc_url,
            wallet=args.wallet.lower(),
            token=args.token.lower(),
            recipient=args.recipient.lower(),
            amount=args.amount_base_units,
        )
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite existing capture: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(source.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed CLI boundary
        print(f"[capture-live-floor-reject] failed: {exc}", file=sys.stderr)
        return 1
    print("captured live Gemini proposal and Sepolia preflight rejection; broadcastAttempted=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
