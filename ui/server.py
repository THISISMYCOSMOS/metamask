#!/usr/bin/env python3
import argparse
import copy
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent
TRACE_PATH = REPO_ROOT / "traces" / "cumulative-loss.json"
VERIFIER_DIR = REPO_ROOT / "verifier"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(VERIFIER_DIR))

from backend.compiler_contract import CompilerUnavailableError, ProviderResponseError  # noqa: E402
from backend.policy_service import PolicyProposalService  # noqa: E402
from core.canonical import canonical_sha256  # noqa: E402
from core.execution_service import Erc20ExecutionService, anvil_sender, live_context_reader  # noqa: E402
from core.gate import ExecutionGate  # noqa: E402
from core.models import (  # noqa: E402
    ApprovedPolicyEnvelope,
    CompileRequest,
    PolicyProposal as CorePolicyProposal,
)
from core.policy_binding import PolicyApprovalError  # noqa: E402
from core.rpc_simulator import (  # noqa: E402
    ControlledErc20Simulator,
    ERC20TransferRequest,
    JsonRpcClient,
    RpcSimulationError,
    decode_erc20_uint256,
    encode_erc20_transfer,
)

from invariant_models import CANONICAL_INVARIANT_KINDS, InvariantPolicy  # noqa: E402
from candidate_models import CandidateTrace  # noqa: E402
from evaluate_approved_candidate import evaluate_approved_candidate  # noqa: E402
from evaluate_invariants import EvaluationInputError  # noqa: E402
from synthesis_models import (  # noqa: E402
    LlmPolicyResponse,
    PolicyProposal as LegacyPolicyProposal,
    canonical_model_sha256,
)
from synthesis_workflow import (  # noqa: E402
    STRUCTURED_EDITOR_REQUEST_ID,
    SynthesisInputError,
    approve_policy_proposal,
    build_structured_policy,
    compile_local_structured_proposal,
    compile_policy_proposal,
    create_intent_request,
)

INTENT_PATH = REPO_ROOT / "specs" / "mvp-intent.ko.txt"
POLICY_TEMPLATE_PATH = REPO_ROOT / "specs" / "mvp-candidate-invariants.json"
OFFLINE_RESPONSE_PATH = REPO_ROOT / "specs" / "mvp-llm-response.fixture.json"
CANDIDATE_PATH = REPO_ROOT / "traces" / "mvp-candidate-reject.json"
DEPLOYMENT_MANIFEST_PATH = REPO_ROOT / "chain" / "deployments" / "manifest.json"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LIVE_DEFAULT_INTENT = "USDC를 20개 이상 남겨줘"
CONTROLLED_BINDING_ENV = (
    "CONTROLLED_CHAIN_ID",
    "CONTROLLED_WALLET_ADDRESS",
    "CONTROLLED_TOKEN_ADDRESS",
    "CONTROLLED_TOKEN_SYMBOL",
    "CONTROLLED_TOKEN_DECIMALS",
)
METAMASK_BINDING_ENV = (
    "METAMASK_CHAIN_ID",
    "METAMASK_TOKEN_ADDRESS",
    "METAMASK_TOKEN_SYMBOL",
    "METAMASK_TOKEN_DECIMALS",
)

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

ALLOWED_COMMANDS = ("status", "validate", "evaluate", "run g3")

MAX_BODY_BYTES = 16384

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

run_lock = threading.Lock()
policy_lock = threading.Lock()
policy_state = None
execution_gate = ExecutionGate()


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def metamask_wallet_config():
    values = {name: os.environ.get(name, "").strip() for name in METAMASK_BINDING_ENV}
    if not any(values.values()):
        return {"enabled": False}
    if not all(values.values()):
        missing = [name for name, value in values.items() if not value]
        raise CompilerUnavailableError("MetaMask testnet binding is incomplete: " + ", ".join(missing))
    try:
        chain_id = int(values["METAMASK_CHAIN_ID"])
        token_decimals = int(values["METAMASK_TOKEN_DECIMALS"])
    except ValueError as exc:
        raise CompilerUnavailableError("MetaMask chain id and token decimals must be integers") from exc
    if chain_id < 1 or token_decimals < 0 or token_decimals > 36:
        raise CompilerUnavailableError("MetaMask chain id or token decimals are out of range")
    token_address = values["METAMASK_TOKEN_ADDRESS"]
    ERC20TransferRequest(
        token=token_address,
        sender="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        amount=1,
    ).validated()
    return {
        "enabled": True,
        "chainId": chain_id,
        "tokenAddress": token_address.lower(),
        "tokenSymbol": values["METAMASK_TOKEN_SYMBOL"],
        "tokenDecimals": token_decimals,
    }


def _core_compile_request(intent_text, wallet_binding=None):
    if wallet_binding is not None:
        if not isinstance(wallet_binding, dict):
            raise CompilerUnavailableError("walletBinding must be an object")
        config = metamask_wallet_config()
        if not config.get("enabled"):
            raise CompilerUnavailableError("MetaMask testnet binding is not configured")
        wallet_address = wallet_binding.get("walletAddress")
        chain_id = wallet_binding.get("chainId")
        if not isinstance(wallet_address, str) or not isinstance(chain_id, int) or isinstance(chain_id, bool):
            raise CompilerUnavailableError("MetaMask wallet address and chain id are required")
        if chain_id != config["chainId"]:
            raise CompilerUnavailableError("MetaMask is connected to the wrong chain")
        digest = canonical_sha256(
            {"intentText": intent_text, "chainId": chain_id, "walletAddress": wallet_address.lower()}
        )[2:18]
        return CompileRequest(
            schemaVersion=1,
            kind="policy-compile-request",
            requestId=f"ui-request-{digest}",
            proposalId=f"ui-proposal-{digest}",
            policyId=f"ui-token-floor-{digest}",
            intentText=intent_text,
            chainId=chain_id,
            walletAddress=wallet_address,
            tokenAddress=config["tokenAddress"],
            tokenSymbol=config["tokenSymbol"],
            tokenDecimals=config["tokenDecimals"],
        )
    configured = {name: os.environ.get(name, "").strip() for name in CONTROLLED_BINDING_ENV}
    if any(configured.values()) and not all(configured.values()):
        missing = [name for name, value in configured.items() if not value]
        raise CompilerUnavailableError("controlled execution binding is incomplete: " + ", ".join(missing))
    if all(configured.values()):
        try:
            chain_id = int(configured["CONTROLLED_CHAIN_ID"])
            token_decimals = int(configured["CONTROLLED_TOKEN_DECIMALS"])
        except ValueError as exc:
            raise CompilerUnavailableError("controlled chain id and token decimals must be integers") from exc
        wallet_address = configured["CONTROLLED_WALLET_ADDRESS"]
        token_address = configured["CONTROLLED_TOKEN_ADDRESS"]
        token_symbol = configured["CONTROLLED_TOKEN_SYMBOL"]
    else:
        manifest = _read_json(DEPLOYMENT_MANIFEST_PATH)
        fork = manifest.get("fork")
        if not isinstance(fork, dict):
            raise CompilerUnavailableError("deployment manifest has no authoritative fork")
        chain_id = fork["chainId"]
        wallet_address = manifest["delegator"]
        token_address = USDC_ADDRESS
        token_symbol = "USDC"
        token_decimals = 6
    digest = canonical_sha256({"intentText": intent_text})[2:18]
    return CompileRequest(
        schemaVersion=1,
        kind="policy-compile-request",
        requestId=f"ui-request-{digest}",
        proposalId=f"ui-proposal-{digest}",
        policyId=f"ui-usdc-floor-{digest}",
        intentText=intent_text,
        chainId=chain_id,
        walletAddress=wallet_address,
        tokenAddress=token_address,
        tokenSymbol=token_symbol,
        tokenDecimals=token_decimals,
    )


def _empty_core_policy_state(request):
    return {
        "stage": "request-created",
        "compilerSource": "gemini-required",
        "request": request.model_dump(mode="json"),
        "requestSha256": canonical_sha256(request),
        "proposal": None,
        "proposalSha256": None,
        "approval": None,
        "approvalSha256": None,
        "candidateEvaluation": None,
        "transaction": {
            "status": "not-created",
            "eligibleForBroadcast": False,
            "reason": "정확한 거래 요청과 실행 바인딩이 아직 생성되지 않았습니다.",
        },
        "logs": ["공유 policy-compile-request 생성 완료", "Gemini 구조화 출력 대기"],
    }


def build_live_policy_flow(intent_text, service=None, wallet_binding=None):
    """Compile one UI intent through the real Gemini-backed shared service."""
    request = _core_compile_request(intent_text, wallet_binding=wallet_binding)
    state = _empty_core_policy_state(request)
    compiler = service or PolicyProposalService.from_env()
    result = compiler.compile(request)
    state["logs"].append("Gemini 응답 및 로컬 정책 계약 검증 완료")
    if not result.supported:
        state["compilerSource"] = "gemini-api"
        state["unsupportedItems"] = result.unsupportedItems
        state["reasonCodes"] = result.reasonCodes
        state["logs"].append("요청 전체를 지원하지 않아 승인 가능한 제안 생성 중단")
        return state
    proposal = result.approvable_proposal
    state.update(
        {
            "stage": "proposal-ready",
            "compilerSource": "gemini-api",
            "proposal": proposal.model_dump(mode="json"),
            "proposalSha256": result.proposalSha256,
        }
    )
    state["logs"].append("공유 미승인 policy-proposal 생성 완료")
    return state


def build_policy_flow(intent_text):
    template = InvariantPolicy.model_validate(_read_json(POLICY_TEMPLATE_PATH))
    request = create_intent_request(
        request_id="mvp-intent-001",
        intent_text=intent_text,
        policy_template=template,
    )
    state = {
        "stage": "request-created",
        "compilerSource": "provider-required",
        "request": request.model_dump(mode="json"),
        "requestSha256": canonical_model_sha256(request),
        "proposal": None,
        "proposalSha256": None,
        "approval": None,
        "approvalSha256": None,
        "candidateEvaluation": None,
        "transaction": {
            "status": "not-created",
            "eligibleForBroadcast": False,
            "reason": "정확한 거래 요청과 실행 바인딩이 아직 생성되지 않았습니다.",
        },
        "logs": ["intent-compiler-request 생성 완료"],
    }

    response = LlmPolicyResponse.model_validate(_read_json(OFFLINE_RESPONSE_PATH))
    if response.requestSha256.lower() != state["requestSha256"]:
        state["logs"].append("현재 입력에 결합된 LLM 응답 없음 — 제안 생성 중단")
        return state
    proposal = compile_policy_proposal(request, response)

    state.update(
        {
            "stage": "proposal-ready",
            "compilerSource": "offline-fixture",
            "proposal": proposal.model_dump(mode="json"),
            "proposalSha256": canonical_model_sha256(proposal),
        }
    )
    state["logs"].extend(
        [
            "오프라인 llm-policy-response 스키마 검증 완료",
            "request/fork/invariant/rationale 결합 검증 완료",
            "미승인 policy-proposal 생성 완료",
        ]
    )
    return state


def build_structured_policy_flow(intent_text, invariants_data):
    if not isinstance(invariants_data, list) or not invariants_data:
        raise SynthesisInputError("at least one structured condition is required")
    if len(invariants_data) > 4:
        raise SynthesisInputError("at most four structured conditions are supported")

    kind_order = {kind: index for index, kind in enumerate(CANONICAL_INVARIANT_KINDS)}
    try:
        ordered = sorted(invariants_data, key=lambda item: kind_order.get(item.get("kind"), len(kind_order)))
    except AttributeError as exc:
        raise SynthesisInputError("each structured condition must be an object with a kind field") from exc

    template = InvariantPolicy.model_validate(_read_json(POLICY_TEMPLATE_PATH))
    probe_policy = build_structured_policy(
        policy_id=template.policyId,
        fork=template.fork,
        invariants=ordered,
    )
    request = create_intent_request(
        request_id=STRUCTURED_EDITOR_REQUEST_ID,
        intent_text=intent_text,
        policy_template=probe_policy,
    )
    proposal = compile_local_structured_proposal(request, probe_policy.invariants)

    return {
        "stage": "proposal-ready",
        "compilerSource": "local-structured-editor",
        "request": request.model_dump(mode="json"),
        "requestSha256": canonical_model_sha256(request),
        "proposal": proposal.model_dump(mode="json"),
        "proposalSha256": canonical_model_sha256(proposal),
        "approval": None,
        "approvalSha256": None,
        "candidateEvaluation": None,
        "transaction": {
            "status": "not-created",
            "eligibleForBroadcast": False,
            "reason": "정확한 거래 요청과 실행 바인딩이 아직 생성되지 않았습니다.",
        },
        "logs": [
            "구조화된 조건 편집기에서 intent-compiler-request 생성 완료",
            "로컬 구조화 편집기 컴파일 완료 — 실시간 LLM 호출 없음",
            "미승인 policy-proposal 생성 완료 (compilerSource=local-structured-editor)",
            "이전 승인·후보 평가가 있었다면 새 제안으로 무효화됨",
        ],
    }


def replace_structured_policy_flow(intent_text, invariants_data):
    global policy_state
    next_state = build_structured_policy_flow(intent_text, invariants_data)
    with policy_lock:
        policy_state = next_state
        return copy.deepcopy(policy_state)


def current_policy_flow():
    global policy_state
    with policy_lock:
        if policy_state is None:
            request = _core_compile_request(LIVE_DEFAULT_INTENT)
            policy_state = _empty_core_policy_state(request)
        return copy.deepcopy(policy_state)


def replace_policy_flow(intent_text, service=None, wallet_binding=None):
    global policy_state
    request = _core_compile_request(intent_text, wallet_binding=wallet_binding)
    pending_state = _empty_core_policy_state(request)
    with policy_lock:
        policy_state = pending_state
    try:
        next_state = build_live_policy_flow(intent_text, service=service, wallet_binding=wallet_binding)
    except Exception:
        with policy_lock:
            if policy_state is not None and policy_state.get("requestSha256") == pending_state["requestSha256"]:
                policy_state["logs"].append("Gemini 컴파일 실패 — 제안·승인 생성 안 함")
        raise
    with policy_lock:
        # A slower earlier request must never overwrite a newer intent.
        if policy_state is None or policy_state.get("requestSha256") != pending_state["requestSha256"]:
            return copy.deepcopy(policy_state)
        policy_state = next_state
        return copy.deepcopy(policy_state)


def approve_current_policy(confirmation):
    global policy_state
    with policy_lock:
        if policy_state is None or policy_state.get("proposal") is None:
            raise SynthesisInputError("승인할 정책 제안이 없습니다")
        proposal_data = policy_state["proposal"]
        policy_data = proposal_data.get("policy")
        is_core_proposal = (
            proposal_data.get("kind") == "policy-proposal"
            and "proposalId" in proposal_data
            and isinstance(policy_data, dict)
            and policy_data.get("kind") == "assetBalanceFloor"
        )
        if is_core_proposal:
            proposal = CorePolicyProposal.model_validate(policy_state["proposal"])
            request = CompileRequest.model_validate(policy_state["request"])
            approval = PolicyProposalService.approve(
                proposal,
                approval_id=f"approval-{proposal.proposalId}",
                approved_by="user",
                confirmation=confirmation,
                request=request,
            )
            policy_state["stage"] = "approved"
            policy_state["approval"] = approval.model_dump(mode="json")
            policy_state["approvalSha256"] = canonical_sha256(approval)
            policy_state["logs"].append("공유 proposalSha256 사용자 승인 기록 완료")
            policy_state["logs"].append("거래 요청/실행 바인딩 미생성 — 브로드캐스트 불가")
            return copy.deepcopy(policy_state)

        proposal = LegacyPolicyProposal.model_validate(policy_state["proposal"])
        # approve_policy_proposal raises SynthesisInputError (HTTP 422) on a
        # mismatched confirmation hash; that is the only approval failure.
        # An approval that hashes correctly must be recorded even if the
        # downstream candidate evaluation input later turns out to be invalid.
        approval = approve_policy_proposal(
            proposal,
            confirmation=confirmation,
            approved_by="user",
            approval_scope="user",
        )
        policy_state["stage"] = "approved"
        policy_state["approval"] = approval.model_dump(mode="json")
        policy_state["approvalSha256"] = canonical_model_sha256(approval)
        policy_state["logs"].append("정확한 proposalSha256 사용자 승인 기록 완료")

        candidate = CandidateTrace.model_validate(_read_json(CANDIDATE_PATH))
        try:
            evaluation = evaluate_approved_candidate(approval, candidate)
        except EvaluationInputError as exc:
            policy_state["candidateEvaluation"] = {
                "status": "evaluation-invalid",
                "reason": str(exc),
            }
            policy_state["logs"].append(f"승인 기록 완료 — 후보 평가 입력 무효: {exc}")
            policy_state["logs"].append("거래 요청/실행 바인딩 미생성 — 브로드캐스트 불가")
            return copy.deepcopy(policy_state)

        policy_state["candidateEvaluation"] = {**evaluation, "status": "evaluated"}
        policy_state["logs"].append("승인된 정책으로 G3 후보 결정론 평가 완료")
        accepted_label = "true — 정책 승인" if evaluation["accepted"] else "false — 정책 거절"
        policy_state["logs"].append(f"candidate accepted={accepted_label}")
        policy_state["logs"].append("거래 요청/실행 바인딩 미생성 — 브로드캐스트 불가")
        return copy.deepcopy(policy_state)


def _default_execution_runner(approval, transfer, candidate_id):
    rpc_url = os.environ.get("ANVIL_RPC_URL", "").strip()
    if not rpc_url:
        raise RpcSimulationError("ANVIL_RPC_URL is required for controlled execution")
    rpc = JsonRpcClient(rpc_url)
    service = Erc20ExecutionService(
        ControlledErc20Simulator(JsonRpcClient(rpc_url)),
        read_context=live_context_reader(rpc, transfer.sender, transfer.token),
        send=anvil_sender(rpc),
        gate=execution_gate,
    )
    return service.run(approval, transfer, candidate_id=candidate_id)


def execute_current_policy(recipient, amount, gas_limit=None, runner=None):
    """Run the approved Core policy through simulation, decision, and local send.

    The endpoint never accepts a wallet or token from the browser. Those facts
    come from the exact proposal the user approved. The only executable target
    is an explicitly configured loopback Anvil node.
    """
    global policy_state
    if not isinstance(recipient, str):
        raise RpcSimulationError("recipientAddress must be an address string")
    if not isinstance(amount, str) or not amount.isdigit():
        raise RpcSimulationError("amountBaseUnits must be an unsigned decimal string")
    if gas_limit is not None and (not isinstance(gas_limit, str) or not gas_limit.isdigit()):
        raise RpcSimulationError("gasLimit must be an unsigned decimal string")

    with policy_lock:
        if policy_state is None or policy_state.get("approval") is None:
            raise PolicyApprovalError("an exact-hash Core policy approval is required")
        approval_data = copy.deepcopy(policy_state["approval"])
        approval_hash = policy_state.get("approvalSha256")
        request_hash = policy_state.get("requestSha256")
        previous_transaction = copy.deepcopy(policy_state.get("transaction") or {})

    approval = ApprovedPolicyEnvelope.model_validate(approval_data)
    policy = approval.proposal.policy
    transfer = ERC20TransferRequest(
        token=policy.tokenAddress,
        sender=policy.walletAddress,
        recipient=recipient,
        amount=int(amount),
        gas_limit=int(gas_limit) if gas_limit is not None else None,
    ).validated()
    plan = {
        "approvalSha256": approval_hash,
        "recipientAddress": transfer.recipient,
        "amountBaseUnits": str(transfer.amount),
        "gasLimit": str(transfer.gas_limit) if transfer.gas_limit is not None else None,
    }
    plan_hash = canonical_sha256(plan)
    if previous_transaction.get("planSha256") == plan_hash and previous_transaction.get("status") in {
        "submitted",
        "rejected",
    }:
        raise RpcSimulationError("this exact execution plan was already consumed")

    candidate_id = f"ui-candidate-{plan_hash[2:18]}"
    outcome = (runner or _default_execution_runner)(approval, transfer, candidate_id)
    decision = outcome.decision
    candidate_hash = canonical_sha256(outcome.candidate)
    evaluation = {
        "status": "core-decision",
        "accepted": decision.accepted,
        "candidateTraceId": outcome.candidate.candidateId,
        "candidateTraceSha256": candidate_hash,
        "evaluations": [
            {
                "id": policy.policyId,
                "kind": policy.kind,
                "passed": decision.accepted,
                "assetBalanceFloor": policy.assetBalanceFloor,
                "observedAfterBalance": outcome.candidate.simulation.afterAssetBalance,
                "evidence": {
                    "candidateSha256": decision.candidateSha256,
                    "executionSha256": decision.executionSha256,
                    "reasonCodes": list(decision.reasonCodes),
                },
            }
        ],
        "decision": decision.model_dump(mode="json"),
    }
    transaction = {
        "status": "submitted" if outcome.sent else "rejected",
        "eligibleForBroadcast": False,
        "planSha256": plan_hash,
        "candidateSha256": candidate_hash,
        "executionSha256": decision.executionSha256,
        "transactionHash": str(outcome.send_result) if outcome.sent else None,
        "reason": (
            "로컬 Anvil에 게이트 승인 거래를 1회 제출했습니다."
            if outcome.sent
            else "결정론적 실행 게이트가 거래를 거절해 전송하지 않았습니다."
        ),
    }

    with policy_lock:
        if (
            policy_state is None
            or policy_state.get("requestSha256") != request_hash
            or policy_state.get("approvalSha256") != approval_hash
        ):
            raise RpcSimulationError("policy state changed during execution; result was not attached")
        policy_state["stage"] = "executed" if outcome.sent else "rejected"
        policy_state["candidateEvaluation"] = evaluation
        policy_state["transaction"] = transaction
        policy_state["logs"].append("Anvil 스냅샷 시뮬레이션 및 상태 복원 확인")
        policy_state["logs"].append(
            "결정론적 게이트 승인 — 로컬 전송 1회 제출"
            if outcome.sent
            else "결정론적 게이트 거절 — 외부 전송 0회"
        )
        return copy.deepcopy(policy_state)


def _decimal_uint(value, field, *, positive=False):
    if not isinstance(value, str) or not value.isdigit():
        raise RpcSimulationError(f"{field} must be an unsigned decimal string")
    parsed = int(value)
    if parsed >= 1 << 256 or (positive and parsed == 0):
        raise RpcSimulationError(f"{field} is out of range")
    return parsed


def authorize_metamask_policy(
    wallet_address,
    chain_id,
    recipient,
    amount,
    gas_limit,
    asset_balance,
    sender_nonce,
    transfer_call_result,
):
    """Authorize one exact MetaMask transaction from wallet-supplied RPC evidence.

    This is an application-level testnet gate. MetaMask remains the signer and
    shows its own confirmation UI; the policy is not installed in the wallet.
    """
    global policy_state
    if not isinstance(chain_id, int) or isinstance(chain_id, bool) or chain_id < 1:
        raise RpcSimulationError("chainId must be a positive integer")
    amount_value = _decimal_uint(amount, "amountBaseUnits", positive=True)
    gas_value = _decimal_uint(gas_limit, "gasLimit", positive=True)
    balance_value = _decimal_uint(asset_balance, "assetBalance")
    nonce_value = _decimal_uint(sender_nonce, "senderNonce")
    if not isinstance(transfer_call_result, str):
        raise RpcSimulationError("transferCallResult must be an RPC hex result")
    if decode_erc20_uint256(transfer_call_result) != 1:
        raise RpcSimulationError("ERC-20 transfer preflight did not return true")

    with policy_lock:
        if policy_state is None or policy_state.get("approval") is None:
            raise PolicyApprovalError("an exact-hash Core policy approval is required")
        approval_data = copy.deepcopy(policy_state["approval"])
        approval_hash = policy_state.get("approvalSha256")
        request_hash = policy_state.get("requestSha256")
        previous_transaction = copy.deepcopy(policy_state.get("transaction") or {})

    approval = ApprovedPolicyEnvelope.model_validate(approval_data)
    policy = approval.proposal.policy
    config = metamask_wallet_config()
    if not config.get("enabled"):
        raise RpcSimulationError("MetaMask testnet binding is not configured")
    transfer = ERC20TransferRequest(
        token=policy.tokenAddress,
        sender=wallet_address,
        recipient=recipient,
        amount=amount_value,
        gas_limit=gas_value,
    ).validated()

    reasons = []
    if chain_id != policy.chainId or chain_id != config["chainId"]:
        reasons.append("POLICY_CHAIN_MISMATCH")
    if transfer.sender.lower() != policy.walletAddress.lower():
        reasons.append("POLICY_WALLET_MISMATCH")
    if transfer.token.lower() != config["tokenAddress"].lower():
        reasons.append("POLICY_TOKEN_MISMATCH")
    if balance_value < amount_value:
        reasons.append("INSUFFICIENT_ASSET_BALANCE")
        after_balance = 0
    else:
        after_balance = balance_value - amount_value
        if after_balance < int(policy.assetBalanceFloor):
            reasons.append("ASSET_BALANCE_FLOOR_VIOLATION")

    wallet_request = {
        "from": transfer.sender,
        "to": transfer.token,
        "value": "0x0",
        "data": encode_erc20_transfer(transfer.recipient, transfer.amount),
        "nonce": hex(nonce_value),
        "gas": hex(gas_value),
    }
    plan = {
        "schemaVersion": 1,
        "kind": "metamask-erc20-transfer-plan",
        "approvalSha256": approval_hash,
        "chainId": chain_id,
        "walletAddress": transfer.sender,
        "tokenAddress": transfer.token,
        "recipientAddress": transfer.recipient,
        "amountBaseUnits": str(amount_value),
        "senderNonce": str(nonce_value),
        "gasLimit": str(gas_value),
        "assetBalanceBefore": str(balance_value),
        "assetBalanceAfter": str(after_balance),
        "walletRequest": wallet_request,
    }
    plan_hash = canonical_sha256(plan)
    if previous_transaction.get("planSha256") == plan_hash and previous_transaction.get("status") == "submitted":
        raise RpcSimulationError("this exact MetaMask execution plan was already consumed")
    decision = {
        "schemaVersion": 1,
        "kind": "metamask-preflight-decision",
        "planSha256": plan_hash,
        "approvalSha256": approval_hash,
        "policySha256": approval.policySha256,
        "accepted": not reasons,
        "reasonCodes": reasons,
    }
    decision_hash = canonical_sha256(decision)
    evaluation = {
        "status": "wallet-preflight-decision",
        "accepted": decision["accepted"],
        "candidateTraceId": f"metamask-plan-{plan_hash[2:18]}",
        "candidateTraceSha256": plan_hash,
        "evaluations": [
            {
                "id": policy.policyId,
                "kind": policy.kind,
                "passed": decision["accepted"],
                "assetBalanceFloor": policy.assetBalanceFloor,
                "observedAfterBalance": str(after_balance),
                "evidence": {
                    "source": "metamask-eip1193-preflight",
                    "decisionSha256": decision_hash,
                    "reasonCodes": reasons,
                },
            }
        ],
        "decision": decision,
    }
    transaction = {
        "mode": "metamask",
        "status": "wallet-authorized" if decision["accepted"] else "rejected",
        "eligibleForBroadcast": decision["accepted"],
        "planSha256": plan_hash,
        "decisionSha256": decision_hash,
        "walletRequest": wallet_request if decision["accepted"] else None,
        "recipientAddress": transfer.recipient,
        "amountBaseUnits": str(amount_value),
        "transactionHash": None,
        "receipt": None,
        "reason": (
            "결정론적 사전 판정을 통과했습니다. MetaMask 확인 후 테스트넷에 제출할 수 있습니다."
            if decision["accepted"]
            else "결정론적 사전 판정이 거래를 거절해 MetaMask를 호출하지 않습니다."
        ),
    }

    with policy_lock:
        if (
            policy_state is None
            or policy_state.get("requestSha256") != request_hash
            or policy_state.get("approvalSha256") != approval_hash
        ):
            raise RpcSimulationError("policy state changed during MetaMask preflight")
        policy_state["stage"] = "wallet-authorized" if decision["accepted"] else "rejected"
        policy_state["candidateEvaluation"] = evaluation
        policy_state["transaction"] = transaction
        policy_state["logs"].append("MetaMask RPC 잔고·nonce·eth_call·gas estimate 사전 증거 검증")
        policy_state["logs"].append(
            "결정론적 판정 승인 — MetaMask 사용자 확인 대기"
            if decision["accepted"]
            else "결정론적 판정 거절 — MetaMask 호출 0회"
        )
        return copy.deepcopy(policy_state)


def record_metamask_submission(plan_hash, transaction_hash):
    global policy_state
    if not isinstance(plan_hash, str) or not re.fullmatch(r"0x[0-9a-f]{64}", plan_hash):
        raise RpcSimulationError("planSha256 must be a canonical hash")
    if not isinstance(transaction_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash):
        raise RpcSimulationError("transactionHash must be a 32-byte hash")
    with policy_lock:
        transaction = (policy_state or {}).get("transaction") or {}
        if transaction.get("mode") != "metamask" or transaction.get("status") != "wallet-authorized":
            raise RpcSimulationError("no authorized MetaMask execution is pending")
        if transaction.get("planSha256") != plan_hash:
            raise RpcSimulationError("submitted transaction does not match the authorized plan")
        transaction["status"] = "submitted"
        transaction["eligibleForBroadcast"] = False
        transaction["transactionHash"] = transaction_hash.lower()
        transaction["reason"] = "MetaMask가 테스트넷 거래 해시를 반환했습니다. 체인 영수증 검증은 아직 필요합니다."
        policy_state["stage"] = "wallet-submitted"
        policy_state["logs"].append("MetaMask eth_sendTransaction이 거래 해시를 반환함")
        policy_state["logs"].append("테스트넷 영수증 검증 대기")
        return copy.deepcopy(policy_state)


def _receipt_address_topic(address):
    return "0x" + "0" * 24 + address.lower()[2:]


def _matching_transfer_log(receipt, *, token, sender, recipient, amount):
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise RpcSimulationError("receipt logs must be an array")
    expected_topics = (
        ERC20_TRANSFER_TOPIC,
        _receipt_address_topic(sender),
        _receipt_address_topic(recipient),
    )
    matches = []
    for entry in logs:
        if not isinstance(entry, dict) or str(entry.get("address", "")).lower() != token.lower():
            continue
        topics = entry.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if tuple(str(value).lower() for value in topics[:3]) != expected_topics:
            continue
        data = entry.get("data")
        if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", data):
            continue
        if int(data, 16) == amount:
            matches.append(entry)
    if len(matches) != 1:
        raise RpcSimulationError("receipt must contain exactly one matching ERC-20 Transfer event")
    return matches[0]


def record_metamask_receipt(plan_hash, transaction_hash, receipt, asset_balance_after):
    """Bind a successful MetaMask receipt and post-state to the authorized plan."""
    global policy_state
    if not isinstance(plan_hash, str) or not re.fullmatch(r"0x[0-9a-f]{64}", plan_hash):
        raise RpcSimulationError("planSha256 must be a canonical hash")
    if not isinstance(transaction_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash):
        raise RpcSimulationError("transactionHash must be a 32-byte hash")
    if not isinstance(receipt, dict):
        raise RpcSimulationError("receipt must be an object")
    after_balance = _decimal_uint(asset_balance_after, "assetBalanceAfter")

    with policy_lock:
        transaction = (policy_state or {}).get("transaction") or {}
        if transaction.get("mode") != "metamask" or transaction.get("status") != "submitted":
            raise RpcSimulationError("no submitted MetaMask execution is awaiting a receipt")
        if transaction.get("planSha256") != plan_hash:
            raise RpcSimulationError("receipt does not match the authorized plan")
        expected_hash = transaction.get("transactionHash")
        if not isinstance(expected_hash, str) or expected_hash != transaction_hash.lower():
            raise RpcSimulationError("receipt transaction hash does not match the submitted transaction")
        wallet_request = copy.deepcopy(transaction.get("walletRequest") or {})
        recipient = transaction.get("recipientAddress")
        amount = _decimal_uint(transaction.get("amountBaseUnits"), "authorized amount", positive=True)
        proposal = copy.deepcopy((policy_state or {}).get("proposal") or {})

    receipt_hash = receipt.get("transactionHash")
    if not isinstance(receipt_hash, str) or receipt_hash.lower() != transaction_hash.lower():
        raise RpcSimulationError("receipt transaction hash does not match the submitted transaction")
    if str(receipt.get("status", "")).lower() != "0x1":
        raise RpcSimulationError("MetaMask transaction receipt did not succeed")
    block_hash = receipt.get("blockHash")
    block_number = receipt.get("blockNumber")
    if not isinstance(block_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash):
        raise RpcSimulationError("receipt blockHash must be a 32-byte hash")
    if not isinstance(block_number, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", block_number):
        raise RpcSimulationError("receipt blockNumber must be an RPC quantity")

    sender = wallet_request.get("from")
    token = wallet_request.get("to")
    if not isinstance(sender, str) or str(receipt.get("from", "")).lower() != sender.lower():
        raise RpcSimulationError("receipt sender does not match the authorized wallet")
    if not isinstance(token, str) or str(receipt.get("to", "")).lower() != token.lower():
        raise RpcSimulationError("receipt target does not match the authorized token")
    if not isinstance(recipient, str):
        raise RpcSimulationError("authorized recipient is missing")
    _matching_transfer_log(receipt, token=token, sender=sender, recipient=recipient, amount=amount)

    policy = proposal.get("policy")
    if not isinstance(policy, dict):
        raise RpcSimulationError("approved policy is missing")
    floor = _decimal_uint(policy.get("assetBalanceFloor"), "assetBalanceFloor")
    if after_balance < floor:
        raise RpcSimulationError("confirmed post-state violates the approved asset balance floor")

    receipt_evidence = {
        "transactionHash": transaction_hash.lower(),
        "blockHash": block_hash.lower(),
        "blockNumber": str(int(block_number, 16)),
        "status": "success",
        "from": sender.lower(),
        "to": token.lower(),
        "recipientAddress": recipient.lower(),
        "amountBaseUnits": str(amount),
        "assetBalanceAfter": str(after_balance),
        "receiptSha256": canonical_sha256(receipt),
    }
    with policy_lock:
        transaction = (policy_state or {}).get("transaction") or {}
        if (
            transaction.get("status") != "submitted"
            or transaction.get("planSha256") != plan_hash
            or transaction.get("transactionHash") != transaction_hash.lower()
        ):
            raise RpcSimulationError("policy state changed during receipt verification")
        transaction["status"] = "confirmed"
        transaction["receipt"] = receipt_evidence
        transaction["reason"] = "테스트넷 영수증과 ERC-20 Transfer 이벤트, 정책 적용 후 잔고를 검증했습니다."
        policy_state["stage"] = "wallet-confirmed"
        policy_state["logs"].append("MetaMask 테스트넷 영수증 성공 확인")
        policy_state["logs"].append("ERC-20 Transfer 이벤트·정책 적용 후 잔고 검증 완료")
        return copy.deepcopy(policy_state)


def build_argv(name):
    if name == "validate":
        return [
            "uv", "run", "--cache-dir", "tmp/uv-cache", "--project", "verifier",
            "python", "verifier/validate_trace.py", "traces/cumulative-loss.json",
        ]
    if name == "evaluate":
        return [
            "uv", "run", "--cache-dir", "tmp/uv-cache", "--project", "verifier",
            "python", "verifier/evaluate_invariants.py",
            "specs/phase1-demo-invariants.json", "traces/cumulative-loss.json",
            "--expect", "reject",
        ]
    if name == "run g3":
        if os.name == "nt":
            program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            bash_exe = program_files / "Git" / "bin" / "bash.exe"
            if not bash_exe.exists():
                return None
            script = 'export PATH="$HOME/.foundry/bin:$PATH"; bash chain/scripts/reproduce-g3.sh'
            return [str(bash_exe), "--noprofile", "--norc", "-lc", script]
        return ["bash", "chain/scripts/reproduce-g3.sh"]
    return None


def child_env():
    env = dict(os.environ)
    env.setdefault("G3_PORT", "18550")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Handler(BaseHTTPRequestHandler):
    server_version = "TraceUI/1.0"

    def log_message(self, format, *args):
        pass

    def _has_local_host(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _has_local_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        hostname = urllib.parse.urlparse(origin).hostname
        return (hostname or "").lower() in {"127.0.0.1", "localhost"}

    def _send_json(self, status, payload, no_store=False):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if not self._has_local_host():
            self._send_json(403, {"error": "invalid host"})
            return
        if self.path == "/api/trace":
            self._handle_trace()
            return
        if self.path == "/api/policy":
            try:
                self._send_json(200, current_policy_flow(), no_store=True)
            except Exception:
                self._send_json(500, {"error": "policy state unavailable"}, no_store=True)
            return
        if self.path == "/api/wallet/config":
            try:
                self._send_json(200, metamask_wallet_config(), no_store=True)
            except (CompilerUnavailableError, RpcSimulationError) as exc:
                self._send_json(503, {"error": str(exc)}, no_store=True)
            return
        entry = STATIC_FILES.get(self.path)
        if entry is None:
            self._send_json(404, {"error": "not found"})
            return
        filename, mime = entry
        file_path = UI_DIR / filename
        try:
            data = file_path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _handle_trace(self):
        try:
            raw = TRACE_PATH.read_text(encoding="utf-8")
        except OSError:
            self._send_json(404, {"error": "trace not found"}, no_store=True)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(422, {"error": "invalid trace json"}, no_store=True)
            return
        self._send_json(200, data, no_store=True)

    def _read_json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "unsupported content-type"})
            return None
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._send_json(400, {"error": "missing content-length"})
            return None
        try:
            length = int(length_header)
        except ValueError:
            self._send_json(400, {"error": "invalid content-length"})
            return None
        if length < 0:
            self._send_json(400, {"error": "invalid content-length"})
            return None
        if length > MAX_BODY_BYTES:
            try:
                self.rfile.read(MAX_BODY_BYTES)
            except Exception:
                pass
            self._send_json(413, {"error": "body too large"})
            return None
        try:
            raw_body = self.rfile.read(length)
        except Exception:
            self._send_json(400, {"error": "read error"})
            return None
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid json"})
            return None

    def do_POST(self):
        if not self._has_local_host():
            self._send_json(403, {"error": "invalid host"})
            return
        if not self._has_local_origin():
            self._send_json(403, {"error": "invalid origin"})
            return

        if self.path in {
            "/api/policy/intent",
            "/api/policy/approve",
            "/api/policy/conditions",
            "/api/policy/execute",
            "/api/policy/wallet/authorize",
            "/api/policy/wallet/submitted",
            "/api/policy/wallet/confirmed",
        }:
            if self.headers.get("X-Policy-Console") != "1":
                self._send_json(403, {"error": "missing policy header"})
                return
            body = self._read_json_body()
            if not isinstance(body, dict):
                if body is not None:
                    self._send_json(400, {"error": "invalid body"})
                return
            try:
                if self.path == "/api/policy/intent":
                    intent = body.get("intent")
                    if not isinstance(intent, str) or not intent.strip() or len(intent.strip()) > 4000:
                        self._send_json(400, {"error": "invalid intent"})
                        return
                    state = replace_policy_flow(intent.strip(), wallet_binding=body.get("walletBinding"))
                elif self.path == "/api/policy/conditions":
                    intent = body.get("intent")
                    invariants = body.get("invariants")
                    if not isinstance(intent, str) or not intent.strip() or len(intent.strip()) > 4000:
                        self._send_json(400, {"error": "invalid intent"})
                        return
                    if not isinstance(invariants, list):
                        self._send_json(400, {"error": "invalid invariants"})
                        return
                    state = replace_structured_policy_flow(intent.strip(), invariants)
                elif self.path == "/api/policy/approve":
                    confirmation = body.get("confirmation")
                    if not isinstance(confirmation, str):
                        self._send_json(400, {"error": "invalid confirmation"})
                        return
                    state = approve_current_policy(confirmation)
                elif self.path == "/api/policy/wallet/authorize":
                    state = authorize_metamask_policy(
                        body.get("walletAddress"),
                        body.get("chainId"),
                        body.get("recipientAddress"),
                        body.get("amountBaseUnits"),
                        body.get("gasLimit"),
                        body.get("assetBalance"),
                        body.get("senderNonce"),
                        body.get("transferCallResult"),
                    )
                elif self.path == "/api/policy/wallet/submitted":
                    state = record_metamask_submission(body.get("planSha256"), body.get("transactionHash"))
                elif self.path == "/api/policy/wallet/confirmed":
                    state = record_metamask_receipt(
                        body.get("planSha256"),
                        body.get("transactionHash"),
                        body.get("receipt"),
                        body.get("assetBalanceAfter"),
                    )
                else:
                    recipient = body.get("recipientAddress")
                    amount = body.get("amountBaseUnits")
                    gas_limit = body.get("gasLimit")
                    state = execute_current_policy(recipient, amount, gas_limit)
            except (SynthesisInputError, PolicyApprovalError) as exc:
                self._send_json(422, {"error": str(exc)}, no_store=True)
                return
            except CompilerUnavailableError as exc:
                self._send_json(503, {"error": str(exc)}, no_store=True)
                return
            except ProviderResponseError as exc:
                self._send_json(502, {"error": str(exc)}, no_store=True)
                return
            except RpcSimulationError as exc:
                self._send_json(422, {"error": str(exc)}, no_store=True)
                return
            except Exception:
                self._send_json(500, {"error": "policy workflow failed"}, no_store=True)
                return
            self._send_json(200, state, no_store=True)
            return

        if self.path != "/api/run":
            self._send_json(400, {"error": "unknown endpoint"})
            return
        if self.headers.get("X-G3-Console") != "1":
            self._send_json(403, {"error": "missing console header"})
            return
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict) or not isinstance(body.get("command"), str):
            self._send_json(400, {"error": "invalid body"})
            return
        command = body["command"]
        if command not in ALLOWED_COMMANDS:
            self._send_json(400, {"error": "unknown command"})
            return

        acquired = run_lock.acquire(blocking=False)
        if not acquired:
            self._send_json(409, {"error": "busy"})
            return
        try:
            self._stream_command(command)
        finally:
            run_lock.release()

    def _write_ndjson(self, record):
        line = json.dumps(record).encode("utf-8") + b"\n"
        try:
            self.wfile.write(line)
            self.wfile.flush()
        except Exception:
            raise ConnectionError("client disconnected")

    def _stream_command(self, command):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            self._write_ndjson({"type": "start", "command": command})
        except ConnectionError:
            return

        if command == "status":
            try:
                state = "trace ready" if TRACE_PATH.is_file() else "trace missing"
                self._write_ndjson({"type": "line", "text": "status: " + state})
                self._write_ndjson(
                    {"type": "done", "exitCode": 0, "refreshTrace": True}
                )
            except ConnectionError:
                pass
            return

        argv = build_argv(command)
        if argv is None:
            try:
                self._write_ndjson({"type": "line", "text": "executable not found"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass
            return

        proc = None
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(REPO_ROOT),
                env=child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            client_connected = True
            for text_line in proc.stdout:
                if client_connected:
                    try:
                        self._write_ndjson({"type": "line", "text": text_line.rstrip("\n")})
                    except ConnectionError:
                        client_connected = False
            exit_code = proc.wait()
            if client_connected:
                self._write_ndjson(
                    {"type": "done", "exitCode": exit_code, "refreshTrace": True}
                )
        except FileNotFoundError:
            try:
                self._write_ndjson({"type": "line", "text": "executable not found"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass
        except ConnectionError:
            if proc is not None and proc.poll() is None:
                proc.wait()
        except Exception:
            if proc is not None and proc.poll() is None:
                proc.kill()
            try:
                self._write_ndjson({"type": "line", "text": "internal error"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("Serving at http://127.0.0.1:%d" % args.port)
    print("Allowed commands: %s" % ", ".join(ALLOWED_COMMANDS))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
