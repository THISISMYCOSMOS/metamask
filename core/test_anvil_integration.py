"""Opt-in integration against a real local Anvil node with a deployed TestERC20.

Enable with ``RUN_ANVIL_INTEGRATION=1``.  Configuration comes from
``ANVIL_RPC_URL`` (loopback only), ``TEST_ERC20_ADDRESS``, ``ANVIL_SENDER`` and
``ANVIL_RECIPIENT``.  Nothing here reaches a non-loopback endpoint.

The accepted control performs a *real* ``eth_sendTransaction`` on that local
node.  That is the point: the reject path must call it zero times and the
accepted path exactly once.
"""
from __future__ import annotations

import os
import unittest

from backend.gemini_compiler import GeminiConfig, GeminiPolicyCompiler
from backend.policy_service import PolicyProposalService

from .execution_service import Erc20ExecutionService, anvil_sender, live_context_reader
from .models import ApprovedPolicyEnvelope, CompileRequest
from .rpc_simulator import (
    ControlledErc20Simulator,
    ERC20TransferRequest,
    JsonRpcClient,
    decode_erc20_uint256,
    encode_erc20_balance_of,
    parse_rpc_quantity,
)
from .test_integration import model_output, service


AMOUNT = 1000


@unittest.skipUnless(os.environ.get("RUN_ANVIL_INTEGRATION") == "1", "set RUN_ANVIL_INTEGRATION=1 to run")
class LocalAnvilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.url = os.environ.get("ANVIL_RPC_URL", "http://127.0.0.1:8545")
        cls.token = os.environ["TEST_ERC20_ADDRESS"].lower()
        cls.sender = os.environ["ANVIL_SENDER"].lower()
        cls.recipient = os.environ["ANVIL_RECIPIENT"].lower()
        cls.rpc = JsonRpcClient(cls.url)
        cls.chain_id = parse_rpc_quantity(cls.rpc.call("eth_chainId"), field="eth_chainId")

    def balance_of(self, account: str) -> int:
        data = encode_erc20_balance_of(account)
        return decode_erc20_uint256(self.rpc.call("eth_call", [{"to": self.token, "data": data}, "latest"]))

    def nonce(self) -> int:
        return parse_rpc_quantity(self.rpc.call("eth_getTransactionCount", [self.sender, "latest"]), field="nonce")

    def approve_floor(self, floor: int) -> tuple[ApprovedPolicyEnvelope, CompileRequest]:
        request = CompileRequest.model_validate(
            {
                "schemaVersion": 1,
                "kind": "policy-compile-request",
                "requestId": "anvil-request-1",
                "proposalId": "anvil-proposal-1",
                "policyId": "anvil-floor",
                "intentText": f"테스트 토큰을 {floor} base-unit 이상 남겨줘",
                "chainId": self.chain_id,
                "walletAddress": self.sender,
                "tokenAddress": self.token,
                "tokenSymbol": "TST",
                "tokenDecimals": 0,
            }
        )
        policy_service: PolicyProposalService = service(model_output(floor))
        proposal = policy_service.compile(request).approvable_proposal
        approval = policy_service.approve(
            proposal,
            approval_id="anvil-approval-1",
            approved_by="wallet-owner",
            confirmation=policy_service.confirmation_sentence(proposal),
            request=request,
        )
        return approval, request

    def executor(self, sends: list[object]) -> Erc20ExecutionService:
        def send(transaction: object) -> str:
            sends.append(transaction)
            return anvil_sender(self.rpc)(transaction)  # type: ignore[arg-type]

        return Erc20ExecutionService(
            ControlledErc20Simulator(JsonRpcClient(self.url)),
            read_context=live_context_reader(self.rpc, self.sender, self.token),
            send=send,
        )

    def test_compiler_config_is_required_and_no_live_call_is_made(self) -> None:
        """The default production compiler fails closed; this test never calls Gemini."""
        with self.assertRaises(Exception):
            GeminiPolicyCompiler(GeminiConfig(""))

    def test_rejected_control_restores_state_and_sends_zero_times(self) -> None:
        balance_before, nonce_before = self.balance_of(self.sender), self.nonce()
        approval, _ = self.approve_floor(balance_before - AMOUNT + 1)
        sends: list[object] = []

        outcome = self.executor(sends).run(
            approval,
            ERC20TransferRequest(token=self.token, sender=self.sender, recipient=self.recipient, amount=AMOUNT),
            candidate_id="anvil-candidate-reject",
        )

        self.assertTrue(outcome.evidence.reverted)
        self.assertFalse(outcome.sent)
        self.assertEqual(outcome.decision.reasonCodes, ("ASSET_BALANCE_FLOOR_VIOLATION",))
        self.assertEqual(sends, [])
        self.assertEqual(self.balance_of(self.sender), balance_before)
        self.assertEqual(self.nonce(), nonce_before)

    def test_accepted_control_sends_the_exact_transaction_exactly_once(self) -> None:
        balance_before, nonce_before = self.balance_of(self.sender), self.nonce()
        recipient_before = self.balance_of(self.recipient)
        approval, _ = self.approve_floor(balance_before - AMOUNT)
        sends: list[object] = []

        outcome = self.executor(sends).run(
            approval,
            ERC20TransferRequest(token=self.token, sender=self.sender, recipient=self.recipient, amount=AMOUNT),
            candidate_id="anvil-candidate-accept",
        )

        self.assertTrue(outcome.evidence.reverted)
        self.assertTrue(outcome.decision.accepted)
        self.assertEqual(outcome.decision.reasonCodes, ())
        self.assertTrue(outcome.sent)
        self.assertEqual(sends, [outcome.candidate.transaction])
        self.assertTrue(str(outcome.send_result).startswith("0x"))
        self.assertEqual(self.balance_of(self.sender), balance_before - AMOUNT)
        self.assertEqual(self.balance_of(self.recipient), recipient_before + AMOUNT)
        self.assertEqual(self.nonce(), nonce_before + 1)
        self.assertEqual(outcome.candidate.transaction.nonce, str(nonce_before))
        self.assertEqual(outcome.candidate.simulation.transferAmount, str(AMOUNT))


if __name__ == "__main__":
    unittest.main()
