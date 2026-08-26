from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import server
from backend.gemini_compiler import GeminiConfig, GeminiPolicyCompiler
from backend.compiler_contract import CompilerUnavailableError
from backend.policy_service import PolicyProposalService
from core.canonical import canonical_sha256
from core.rpc_simulator import RpcSimulationError
from synthesis_workflow import SynthesisInputError


class PolicyUiWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        server.policy_state = None

    def exact_intent(self) -> str:
        return server.INTENT_PATH.read_text(encoding="utf-8").strip()

    def test_committed_intent_reaches_unapproved_offline_proposal(self) -> None:
        state = server.build_policy_flow(self.exact_intent())

        self.assertEqual("proposal-ready", state["stage"])
        self.assertEqual("offline-fixture", state["compilerSource"])
        self.assertIsNotNone(state["proposal"])
        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])
        self.assertEqual(
            "0xed84bfa046632e62ab288ec91328897e10b1b856346749862eda835de7149b21",
            state["proposalSha256"],
        )

    def test_unbound_intent_stops_after_request_creation(self) -> None:
        state = server.build_policy_flow("포트폴리오 손실을 제한해줘.")

        self.assertEqual("request-created", state["stage"])
        self.assertEqual("provider-required", state["compilerSource"])
        self.assertIsNone(state["proposal"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_exact_hash_approval_records_audit_artifact_but_not_broadcast_eligibility(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertEqual("user", state["approval"]["approvalScope"])
        self.assertEqual("user", state["approval"]["approvedBy"])
        self.assertEqual(
            "0x6bc7ec4fccbd7478aa597b83b41cb91161f3483bc2a6fe66f3f8311c44e77828",
            state["approvalSha256"],
        )
        self.assertFalse(state["candidateEvaluation"]["accepted"])
        self.assertEqual(
            [False, False],
            [item["passed"] for item in state["candidateEvaluation"]["evaluations"]],
        )
        self.assertEqual(
            "18981111516100000000000",
            state["candidateEvaluation"]["evaluations"][0]["observedMinimumValue1e18"],
        )
        self.assertEqual(
            "2499418800000000000000",
            state["candidateEvaluation"]["evaluations"][1]["observedMaximumLossValue1e18"],
        )
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_wrong_hash_approval_fails_closed(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())

        with self.assertRaises(SynthesisInputError):
            server.approve_current_policy("APPROVE 0x" + "00" * 32)

        self.assertIsNone(server.policy_state["approval"])
        self.assertIsNone(server.policy_state["approvalSha256"])
        self.assertIsNone(server.policy_state["candidateEvaluation"])

    def test_valid_approval_records_even_when_candidate_history_coverage_is_insufficient(self) -> None:
        server.policy_state = server.build_structured_policy_flow(
            "48시간 손실 한도 - 후보 이력 커버리지 부족 테스트",
            [
                {
                    "id": "insufficient-coverage-loss-cap",
                    "kind": "cumulativeLossCap",
                    "windowSeconds": "172800",
                    "maxLossValue1e18": "1999535040000000000000",
                }
            ],
        )
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertIsNotNone(state["approval"])
        self.assertIsNotNone(state["approvalSha256"])
        self.assertEqual("evaluation-invalid", state["candidateEvaluation"]["status"])
        self.assertIn("history coverage is incomplete", state["candidateEvaluation"]["reason"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_valid_approval_records_even_when_drawdown_reference_mismatches(self) -> None:
        server.policy_state = server.build_structured_policy_flow(
            "낙폭 참조값 불일치 테스트",
            [
                {
                    "id": "mismatched-reference-drawdown",
                    "kind": "portfolioDrawdownCapBps",
                    "referenceValue1e18": "1",
                    "maxDrawdownBps": "2000",
                }
            ],
        )
        proposal_hash = server.policy_state["proposalSha256"]

        state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        self.assertEqual("approved", state["stage"])
        self.assertIsNotNone(state["approval"])
        self.assertEqual("evaluation-invalid", state["candidateEvaluation"]["status"])
        self.assertIn("referenceValue1e18", state["candidateEvaluation"]["reason"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_structured_conditions_create_fresh_local_proposal_without_provider(self) -> None:
        state = server.build_structured_policy_flow(
            "구조화된 조건 편집기로 최저 가치 조건을 새로 설정",
            [
                {
                    "id": "portfolio-value-floor-structured",
                    "kind": "portfolioValueFloor",
                    "floorValue1e18": "1000000000000000000000",
                }
            ],
        )

        self.assertEqual("proposal-ready", state["stage"])
        self.assertEqual("local-structured-editor", state["compilerSource"])
        self.assertIsNotNone(state["proposal"])
        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])
        self.assertEqual(["portfolioValueFloor"], [i["kind"] for i in state["proposal"]["policy"]["invariants"]])

    def test_structured_conditions_invalidate_prior_approval_with_fresh_hashes(self) -> None:
        server.policy_state = server.build_policy_flow(self.exact_intent())
        proposal_hash_before_approval = server.policy_state["proposalSha256"]
        approved = server.approve_current_policy(f"APPROVE {proposal_hash_before_approval}")
        server.policy_state = approved
        previous_proposal_hash = approved["proposalSha256"]

        state = server.replace_structured_policy_flow(
            "구조화된 조건 편집기로 조건 변경",
            [
                {
                    "id": "rolling-loss-cap-structured",
                    "kind": "cumulativeLossCap",
                    "windowSeconds": "3600",
                    "maxLossValue1e18": "500000000000000000000",
                }
            ],
        )

        self.assertIsNone(state["approval"])
        self.assertIsNone(state["candidateEvaluation"])
        self.assertNotEqual(previous_proposal_hash, state["proposalSha256"])
        with self.assertRaises(SynthesisInputError):
            server.approve_current_policy(f"APPROVE {previous_proposal_hash}")

    def test_structured_conditions_reject_duplicate_kind(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow(
                "중복 조건",
                [
                    {"id": "floor-a", "kind": "portfolioValueFloor", "floorValue1e18": "1"},
                    {"id": "floor-b", "kind": "portfolioValueFloor", "floorValue1e18": "2"},
                ],
            )

    def test_structured_conditions_reject_missing_drawdown_reference(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow(
                "낙폭 조건 누락 테스트",
                [
                    {
                        "id": "portfolio-drawdown-structured",
                        "kind": "portfolioDrawdownCapBps",
                        "maxDrawdownBps": "2000",
                    }
                ],
            )

    def test_structured_conditions_reject_empty_list(self) -> None:
        with self.assertRaises(SynthesisInputError):
            server.build_structured_policy_flow("빈 조건", [])


class LiveGeminiPolicyUiWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        server.policy_state = None

    @staticmethod
    def service(*, supported: bool = True) -> PolicyProposalService:
        output = {
            "supported": supported,
            "minimumBalanceBaseUnits": "20000000" if supported else None,
            "rationales": ["USDC 잔액 하한을 base-unit으로 고정합니다."],
            "assumptions": ["20개는 6 decimals 기준으로 해석했습니다."],
            "unsupportedItems": [] if supported else ["잔액 하한으로 전체 요청을 표현할 수 없습니다."],
        }
        response = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"role": "model", "parts": [{"text": json.dumps(output)}]},
                }
            ]
        }
        compiler = GeminiPolicyCompiler(GeminiConfig("test-key"), lambda u, h, b: response)
        return PolicyProposalService(compiler)

    def test_initial_get_state_never_calls_a_provider(self) -> None:
        state = server.current_policy_flow()
        self.assertEqual(state["stage"], "request-created")
        self.assertEqual(state["compilerSource"], "gemini-required")
        self.assertIsNone(state["proposal"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_controlled_binding_is_caller_configured_as_one_complete_set(self) -> None:
        values = {
            "CONTROLLED_CHAIN_ID": "31337",
            "CONTROLLED_WALLET_ADDRESS": "0x" + "11" * 20,
            "CONTROLLED_TOKEN_ADDRESS": "0x" + "33" * 20,
            "CONTROLLED_TOKEN_SYMBOL": "CTT",
            "CONTROLLED_TOKEN_DECIMALS": "18",
        }
        with patch.dict(server.os.environ, values, clear=False):
            request = server._core_compile_request("CTT를 남겨줘")
        self.assertEqual(31337, request.chainId)
        self.assertEqual(values["CONTROLLED_WALLET_ADDRESS"], request.walletAddress)
        self.assertEqual(values["CONTROLLED_TOKEN_ADDRESS"], request.tokenAddress)

    def test_partial_controlled_binding_fails_closed(self) -> None:
        values = {name: "" for name in server.CONTROLLED_BINDING_ENV}
        values["CONTROLLED_CHAIN_ID"] = "31337"
        with patch.dict(server.os.environ, values, clear=False):
            with self.assertRaisesRegex(CompilerUnavailableError, "incomplete"):
                server._core_compile_request("CTT를 남겨줘")

    @staticmethod
    def metamask_env():
        return {
            "METAMASK_CHAIN_ID": "11155111",
            "METAMASK_TOKEN_ADDRESS": "0x" + "33" * 20,
            "METAMASK_TOKEN_SYMBOL": "TESTUSDC",
            "METAMASK_TOKEN_DECIMALS": "6",
        }

    def test_metamask_binding_uses_connected_wallet_and_server_token(self) -> None:
        wallet = "0x" + "44" * 20
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            request = server._core_compile_request(
                "TESTUSDC를 20개 이상 남겨줘",
                wallet_binding={"walletAddress": wallet, "chainId": 11155111},
            )
        self.assertEqual(11155111, request.chainId)
        self.assertEqual(wallet, request.walletAddress)
        self.assertEqual(self.metamask_env()["METAMASK_TOKEN_ADDRESS"], request.tokenAddress)

    def test_metamask_binding_rejects_wrong_network(self) -> None:
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            with self.assertRaisesRegex(CompilerUnavailableError, "wrong chain"):
                server._core_compile_request(
                    "TESTUSDC를 남겨줘",
                    wallet_binding={"walletAddress": "0x" + "44" * 20, "chainId": 1},
                )

    def approved_metamask_state(self):
        wallet = "0x" + "44" * 20
        server.policy_state = server.build_live_policy_flow(
            "TESTUSDC를 20개 이상 남겨줘",
            self.service(),
            wallet_binding={"walletAddress": wallet, "chainId": 11155111},
        )
        proposal_hash = server.policy_state["proposalSha256"]
        server.policy_state = server.approve_current_policy(f"APPROVE {proposal_hash}")
        return wallet

    @staticmethod
    def successful_receipt(wallet, token, recipient, transaction_hash, amount=1000000):
        return {
            "transactionHash": transaction_hash,
            "status": "0x1",
            "blockHash": "0x" + "77" * 32,
            "blockNumber": "0x123",
            "from": wallet,
            "to": token,
            "logs": [
                {
                    "address": token,
                    "topics": [
                        server.ERC20_TRANSFER_TOPIC,
                        "0x" + "0" * 24 + wallet[2:],
                        "0x" + "0" * 24 + recipient[2:],
                    ],
                    "data": "0x" + hex(amount)[2:].zfill(64),
                }
            ],
        }

    def test_metamask_preflight_authorizes_exact_erc20_request(self) -> None:
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            state = server.authorize_metamask_policy(
                wallet,
                11155111,
                "0x" + "55" * 20,
                "1000000",
                "65000",
                "25000000",
                "7",
                "0x" + "0" * 63 + "1",
            )
        self.assertEqual("wallet-authorized", state["transaction"]["status"])
        self.assertTrue(state["transaction"]["eligibleForBroadcast"])
        self.assertEqual(wallet, state["transaction"]["walletRequest"]["from"])
        self.assertTrue(state["transaction"]["walletRequest"]["data"].startswith("0xa9059cbb"))
        self.assertEqual("0x7", state["transaction"]["walletRequest"]["nonce"])

    def test_metamask_preflight_rejects_balance_floor_violation_without_wallet_request(self) -> None:
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            state = server.authorize_metamask_policy(
                wallet,
                11155111,
                "0x" + "55" * 20,
                "6000000",
                "65000",
                "25000000",
                "7",
                "0x" + "0" * 63 + "1",
            )
        self.assertEqual("rejected", state["transaction"]["status"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])
        self.assertIsNone(state["transaction"]["walletRequest"])
        self.assertIn("ASSET_BALANCE_FLOOR_VIOLATION", state["candidateEvaluation"]["decision"]["reasonCodes"])

    def test_metamask_submission_must_match_the_authorized_plan(self) -> None:
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            authorized = server.authorize_metamask_policy(
                wallet,
                11155111,
                "0x" + "55" * 20,
                "1000000",
                "65000",
                "25000000",
                "7",
                "0x" + "0" * 63 + "1",
            )
            with self.assertRaisesRegex(RpcSimulationError, "authorized plan"):
                server.record_metamask_submission("0x" + "00" * 32, "0x" + "66" * 32)
            submitted = server.record_metamask_submission(
                authorized["transaction"]["planSha256"], "0x" + "66" * 32
            )
        self.assertEqual("submitted", submitted["transaction"]["status"])
        self.assertEqual("0x" + "66" * 32, submitted["transaction"]["transactionHash"])

    def test_metamask_receipt_binds_transfer_event_and_post_state(self) -> None:
        recipient = "0x" + "55" * 20
        transaction_hash = "0x" + "66" * 32
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            authorized = server.authorize_metamask_policy(
                wallet, 11155111, recipient, "1000000", "65000", "25000000", "7", "0x" + "0" * 63 + "1"
            )
            server.record_metamask_submission(authorized["transaction"]["planSha256"], transaction_hash)
            receipt = self.successful_receipt(
                wallet, self.metamask_env()["METAMASK_TOKEN_ADDRESS"], recipient, transaction_hash
            )
            confirmed = server.record_metamask_receipt(
                authorized["transaction"]["planSha256"], transaction_hash, receipt, "24000000"
            )
        self.assertEqual("wallet-confirmed", confirmed["stage"])
        self.assertEqual("confirmed", confirmed["transaction"]["status"])
        self.assertEqual("24000000", confirmed["transaction"]["receipt"]["assetBalanceAfter"])
        self.assertEqual(transaction_hash, confirmed["transaction"]["receipt"]["transactionHash"])

    def test_metamask_receipt_fails_closed_on_revert_or_wrong_transfer(self) -> None:
        recipient = "0x" + "55" * 20
        transaction_hash = "0x" + "66" * 32
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            authorized = server.authorize_metamask_policy(
                wallet, 11155111, recipient, "1000000", "65000", "25000000", "7", "0x" + "0" * 63 + "1"
            )
            server.record_metamask_submission(authorized["transaction"]["planSha256"], transaction_hash)
            receipt = self.successful_receipt(
                wallet, self.metamask_env()["METAMASK_TOKEN_ADDRESS"], recipient, transaction_hash
            )
            reverted = dict(receipt, status="0x0")
            with self.assertRaisesRegex(RpcSimulationError, "did not succeed"):
                server.record_metamask_receipt(
                    authorized["transaction"]["planSha256"], transaction_hash, reverted, "24000000"
                )
            receipt["logs"][0]["data"] = "0x" + hex(2_000_000)[2:].zfill(64)
            with self.assertRaisesRegex(RpcSimulationError, "exactly one matching"):
                server.record_metamask_receipt(
                    authorized["transaction"]["planSha256"], transaction_hash, receipt, "24000000"
                )
        self.assertEqual("submitted", server.policy_state["transaction"]["status"])

    def test_metamask_receipt_rejects_post_state_below_approved_floor(self) -> None:
        recipient = "0x" + "55" * 20
        transaction_hash = "0x" + "66" * 32
        with patch.dict(server.os.environ, self.metamask_env(), clear=False):
            wallet = self.approved_metamask_state()
            authorized = server.authorize_metamask_policy(
                wallet, 11155111, recipient, "1000000", "65000", "25000000", "7", "0x" + "0" * 63 + "1"
            )
            server.record_metamask_submission(authorized["transaction"]["planSha256"], transaction_hash)
            receipt = self.successful_receipt(
                wallet, self.metamask_env()["METAMASK_TOKEN_ADDRESS"], recipient, transaction_hash
            )
            with self.assertRaisesRegex(RpcSimulationError, "violates"):
                server.record_metamask_receipt(
                    authorized["transaction"]["planSha256"], transaction_hash, receipt, "19999999"
                )

    def test_live_gemini_result_uses_the_shared_core_proposal(self) -> None:
        state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        self.assertEqual(state["stage"], "proposal-ready")
        self.assertEqual(state["compilerSource"], "gemini-api")
        self.assertEqual(state["proposal"]["compiler"]["provider"], "google-gemini")
        self.assertEqual(state["proposal"]["policy"]["kind"], "assetBalanceFloor")
        self.assertEqual(state["proposal"]["policy"]["assetBalanceFloor"], "20000000")
        self.assertEqual(state["proposalSha256"], canonical_sha256(state["proposal"]))
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_unsupported_live_result_never_creates_an_approvable_proposal(self) -> None:
        state = server.build_live_policy_flow("매일 손실도 막고 USDC도 남겨줘", self.service(supported=False))
        self.assertEqual(state["stage"], "request-created")
        self.assertIsNone(state["proposal"])
        self.assertIn("MODEL_REPORTED_UNSUPPORTED", state["reasonCodes"])

    def test_exact_core_hash_approval_records_but_does_not_broadcast(self) -> None:
        server.policy_state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        proposal_hash = server.policy_state["proposalSha256"]
        state = server.approve_current_policy(f"APPROVE {proposal_hash}")
        self.assertEqual(state["stage"], "approved")
        self.assertEqual(state["approval"]["proposalSha256"], proposal_hash)
        self.assertIsNone(state["candidateEvaluation"])
        self.assertFalse(state["transaction"]["eligibleForBroadcast"])

    def test_wrong_core_hash_does_not_record_approval(self) -> None:
        server.policy_state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        with self.assertRaisesRegex(Exception, "confirmation"):
            server.approve_current_policy("APPROVE 0x" + "00" * 32)
        self.assertIsNone(server.policy_state["approval"])

    def test_failed_recompile_invalidates_an_older_approval(self) -> None:
        server.policy_state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        proposal_hash = server.policy_state["proposalSha256"]
        server.policy_state = server.approve_current_policy(f"APPROVE {proposal_hash}")

        class FailingService:
            def compile(self, request):
                raise CompilerUnavailableError("free tier unavailable; no offline fallback exists")

        with self.assertRaises(CompilerUnavailableError):
            server.replace_policy_flow("USDC를 30개 이상 남겨줘", service=FailingService())
        self.assertEqual(server.policy_state["stage"], "request-created")
        self.assertIsNone(server.policy_state["proposal"])
        self.assertIsNone(server.policy_state["approval"])
        self.assertIn("Gemini 컴파일 실패", server.policy_state["logs"][-1])

    def approved_state(self):
        server.policy_state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        proposal_hash = server.policy_state["proposalSha256"]
        server.policy_state = server.approve_current_policy(f"APPROVE {proposal_hash}")

    @staticmethod
    def outcome(*, accepted: bool):
        class FakeModel:
            def __init__(self, data):
                self._data = data
                for key, value in data.items():
                    setattr(self, key, value)

            def model_dump(self, mode="json"):
                return self._data

        simulation = SimpleNamespace(afterAssetBalance="21000000")
        candidate = FakeModel({
            "candidateId": "ui-candidate-test",
            "simulation": {"afterAssetBalance": "21000000"},
        })
        candidate.simulation = simulation
        decision = FakeModel({
            "accepted": accepted,
            "candidateSha256": "0x" + "11" * 32,
            "executionSha256": "0x" + "22" * 32,
            "reasonCodes": [] if accepted else ["ASSET_BALANCE_FLOOR_VIOLATION"],
        })
        decision.reasonCodes = () if accepted else ("ASSET_BALANCE_FLOOR_VIOLATION",)
        return SimpleNamespace(
            candidate=candidate,
            decision=decision,
            sent=accepted,
            send_result="0xsent" if accepted else None,
        )

    def test_execute_requires_exact_hash_approval(self) -> None:
        server.policy_state = server.build_live_policy_flow("USDC를 20개 이상 남겨줘", self.service())
        with self.assertRaisesRegex(Exception, "approval"):
            server.execute_current_policy("0x" + "22" * 20, "1000000", runner=lambda *_: None)

    def test_approved_plan_reaches_simulation_decision_and_local_send_result(self) -> None:
        self.approved_state()
        calls = []

        def runner(approval, transfer, candidate_id):
            calls.append((approval, transfer, candidate_id))
            return self.outcome(accepted=True)

        state = server.execute_current_policy("0x" + "22" * 20, "1000000", runner=runner)

        self.assertEqual(1, len(calls))
        self.assertEqual(server.USDC_ADDRESS.lower(), calls[0][1].token)
        self.assertEqual("submitted", state["transaction"]["status"])
        self.assertEqual("0xsent", state["transaction"]["transactionHash"])
        self.assertTrue(state["candidateEvaluation"]["accepted"])
        self.assertEqual("executed", state["stage"])

    def test_rejected_plan_records_zero_send_decision(self) -> None:
        self.approved_state()
        state = server.execute_current_policy(
            "0x" + "22" * 20,
            "1000000",
            runner=lambda *_: self.outcome(accepted=False),
        )
        self.assertEqual("rejected", state["transaction"]["status"])
        self.assertIsNone(state["transaction"]["transactionHash"])
        self.assertFalse(state["candidateEvaluation"]["accepted"])

    def test_exact_execution_plan_cannot_be_submitted_twice(self) -> None:
        self.approved_state()
        server.execute_current_policy(
            "0x" + "22" * 20,
            "1000000",
            runner=lambda *_: self.outcome(accepted=True),
        )
        with self.assertRaisesRegex(RpcSimulationError, "already consumed"):
            server.execute_current_policy(
                "0x" + "22" * 20,
                "1000000",
                runner=lambda *_: self.outcome(accepted=True),
            )


if __name__ == "__main__":
    unittest.main()
