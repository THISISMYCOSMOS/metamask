"""Offline unit tests for core.rpc_simulator; no Anvil process is required."""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from .rpc_simulator import (
    ControlledErc20Simulator,
    ERC20TransferRequest,
    JsonRpcClient,
    RpcSimulationError,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    encode_erc20_transfer,
)


SENDER = "0x1111111111111111111111111111111111111111"
RECIPIENT = "0x2222222222222222222222222222222222222222"
TOKEN = "0x3333333333333333333333333333333333333333"
TX_HASH = "0x" + "a" * 64
BLOCK_HASH = "0x" + "b" * 64
NONCE, ESTIMATED_GAS, GAS_USED = 3, 0xF000, 0xC000
SENDER_BEFORE, SENDER_AFTER = 100, 90
RECIPIENT_BEFORE, RECIPIENT_AFTER = 5, 15
AMOUNT = 10


class ScriptedNode:
    """Minimal Anvil stand-in: one successful transfer, then a confirmed revert."""

    def __init__(self, *, revert_result: bool = True, receipt_status: str = "0x1", gas_used: int = GAS_USED) -> None:
        self.calls: list[tuple[str, list[Any]]] = []
        self.revert_result = revert_result
        self.receipt_status = receipt_status
        self.gas_used = gas_used
        self.sent = False
        self.reverted = False

    @property
    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def sent_transaction(self) -> Mapping[str, str]:
        return next(params[0] for method, params in self.calls if method == "eth_sendTransaction")

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        method, params = payload["method"], payload["params"]
        self.calls.append((method, params))
        result: Any
        if method == "web3_clientVersion":
            result = "anvil/v1.5.1"
        elif method == "eth_chainId":
            result = "0x7a69"
        elif method == "eth_getBlockByNumber":
            result = {"number": "0x10", "hash": BLOCK_HASH}
        elif method == "eth_getTransactionCount":
            result = hex(NONCE)
        elif method == "eth_estimateGas":
            result = hex(ESTIMATED_GAS)
        elif method == "evm_snapshot":
            result = "0x1"
        elif method == "eth_call":
            account = "0x" + params[0]["data"][-40:]
            moved = self.sent and not self.reverted
            if account == SENDER:
                result = f"0x{(SENDER_AFTER if moved else SENDER_BEFORE):064x}"
            else:
                result = f"0x{(RECIPIENT_AFTER if moved else RECIPIENT_BEFORE):064x}"
        elif method == "eth_sendTransaction":
            self.sent = True
            result = TX_HASH
        elif method == "eth_getTransactionReceipt":
            result = {"status": self.receipt_status, "transactionHash": TX_HASH, "gasUsed": hex(self.gas_used)}
        elif method == "evm_revert":
            self.reverted = True
            result = self.revert_result
        else:
            raise AssertionError(f"unexpected RPC method {method}")
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}


def simulator(node: ScriptedNode) -> ControlledErc20Simulator:
    return ControlledErc20Simulator(JsonRpcClient(transport=node), poll_interval_seconds=0.001)


def request(**overrides: Any) -> ERC20TransferRequest:
    fields: dict[str, Any] = {"token": TOKEN, "sender": SENDER, "recipient": RECIPIENT, "amount": AMOUNT}
    fields.update(overrides)
    return ERC20TransferRequest(**fields)


class EncodingTests(unittest.TestCase):
    def test_manual_erc20_encoding_and_decoding(self) -> None:
        self.assertEqual(encode_erc20_transfer(RECIPIENT, 10), "0xa9059cbb" + "0" * 24 + RECIPIENT[2:] + "0" * 63 + "a")
        self.assertEqual(encode_erc20_balance_of(SENDER), "0x70a08231" + "0" * 24 + SENDER[2:])
        self.assertEqual(decode_erc20_uint256("0x" + "0" * 63 + "a"), 10)
        with self.assertRaises(RpcSimulationError):
            decode_erc20_uint256("0x1")


class SimulatorTests(unittest.TestCase):
    def test_exact_fields_are_resolved_before_the_snapshot_and_then_sent(self) -> None:
        node = ScriptedNode()
        evidence = simulator(node).simulate_transfer(request(), lambda _: True).evidence

        methods = node.methods
        for early in ("eth_getBlockByNumber", "eth_getTransactionCount", "eth_estimateGas"):
            self.assertLess(methods.index(early), methods.index("evm_snapshot"), early)
        sent = node.sent_transaction()
        self.assertEqual(
            sent,
            {
                "from": SENDER,
                "to": TOKEN,
                "value": "0x0",
                "data": encode_erc20_transfer(RECIPIENT, AMOUNT),
                "nonce": hex(NONCE),
                "gas": hex(ESTIMATED_GAS),
            },
        )
        self.assertEqual(evidence.transaction, sent)
        self.assertEqual(evidence.gas_limit, ESTIMATED_GAS)
        self.assertEqual(evidence.gas_used, GAS_USED)
        self.assertEqual(evidence.context.sender_nonce, NONCE)
        self.assertTrue(evidence.reverted)

    def test_explicit_gas_limit_is_used_without_estimating(self) -> None:
        node = ScriptedNode()
        evidence = simulator(node).simulate_transfer(request(gas_limit=0x10000), lambda _: True).evidence
        self.assertNotIn("eth_estimateGas", node.methods)
        self.assertEqual(node.sent_transaction()["gas"], "0x10000")
        self.assertEqual(evidence.gas_limit, 0x10000)

    def test_state_and_nonce_restoration_is_proven_after_revert(self) -> None:
        node = ScriptedNode()
        result = simulator(node).simulate_transfer(request(), lambda _: True)
        self.assertTrue(result.gate_accepted)
        methods = node.methods
        self.assertEqual(methods[-3:], ["eth_call", "eth_call", "eth_getTransactionCount"])
        self.assertLess(methods.index("evm_revert"), len(methods) - 1)

    def test_the_decision_callback_runs_only_after_a_confirmed_revert(self) -> None:
        node = ScriptedNode()
        seen: list[bool] = []
        simulator(node).simulate_transfer(request(), lambda evidence: seen.append(node.reverted and evidence.reverted) or True)
        self.assertEqual(seen, [True])

    def test_a_declined_decision_is_reported_not_raised(self) -> None:
        result = simulator(ScriptedNode()).simulate_transfer(request(), lambda _: False)
        self.assertFalse(result.gate_accepted)
        self.assertTrue(result.evidence.reverted)

    def test_receipt_revert_fails_closed_but_still_reverts_snapshot(self) -> None:
        node = ScriptedNode(receipt_status="0x0")
        with self.assertRaisesRegex(RpcSimulationError, "transaction reverted"):
            simulator(node).simulate_transfer(request(), lambda _: True)
        self.assertIn("evm_revert", node.methods)

    def test_gas_used_above_the_submitted_limit_fails_closed(self) -> None:
        node = ScriptedNode(gas_used=ESTIMATED_GAS + 1)
        with self.assertRaisesRegex(RpcSimulationError, "within the submitted gas limit"):
            simulator(node).simulate_transfer(request(), lambda _: True)
        self.assertIn("evm_revert", node.methods)

    def test_unconfirmed_revert_fails_closed(self) -> None:
        with self.assertRaisesRegex(RpcSimulationError, "evm_revert was not confirmed"):
            simulator(ScriptedNode(revert_result=False)).simulate_transfer(request(), lambda _: True)

    def test_missing_decision_callback_is_refused(self) -> None:
        node = ScriptedNode()
        with self.assertRaisesRegex(RpcSimulationError, "explicit gate is required"):
            simulator(node).simulate_transfer(request(), None)
        self.assertEqual(node.methods, [])

    def test_remote_endpoint_is_rejected_before_any_transaction(self) -> None:
        remote = ControlledErc20Simulator(JsonRpcClient("http://192.0.2.1:8545"), poll_interval_seconds=0.001)
        with self.assertRaisesRegex(RpcSimulationError, "not loopback"):
            remote.simulate_transfer(request(), lambda _: True)

    def test_non_anvil_loopback_node_is_rejected(self) -> None:
        def geth(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            assert payload["method"] == "web3_clientVersion"
            return {"jsonrpc": "2.0", "id": payload["id"], "result": "Geth/v1.13.0"}

        node = ControlledErc20Simulator(JsonRpcClient(transport=geth), poll_interval_seconds=0.001)
        with self.assertRaisesRegex(RpcSimulationError, "not Anvil"):
            node.simulate_transfer(request(), lambda _: True)

    def test_self_transfer_and_zero_address_are_refused(self) -> None:
        node = ScriptedNode()
        with self.assertRaisesRegex(RpcSimulationError, "self-transfer"):
            simulator(node).simulate_transfer(request(recipient=SENDER), lambda _: True)
        with self.assertRaisesRegex(RpcSimulationError, "zero address"):
            simulator(node).simulate_transfer(request(recipient="0x" + "0" * 40), lambda _: True)
        self.assertEqual(node.methods, [])


if __name__ == "__main__":
    unittest.main()
