"""Deterministic construction and validation for the MVP synthesis artifacts."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from invariant_models import InvariantPolicy, canonical_policy_sha256
from synthesis_models import (
    IntentCompilerRequest,
    LlmPolicyResponse,
    PolicyApproval,
    PolicyProposal,
    canonical_model_sha256,
)


class SynthesisInputError(ValueError):
    """The compiler response or approval action violates the MVP contract."""


MVP_COMPILER_RULES = [
    "Return JSON only and do not add fields outside the response schema.",
    "Compile only portfolioValueFloor and cumulativeLossCap for portfolio-candidate traces.",
    "Represent every USD threshold as an unsigned decimal string scaled by 1e18.",
    "Do not infer user approval; this response is only a policy proposal.",
    "If the intent is ambiguous, record the ambiguity in assumptions instead of inventing hidden policy.",
]


def artifact_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, model: BaseModel) -> None:
    path.write_text(artifact_json(model), encoding="utf-8", newline="\n")


def create_intent_request(*, request_id: str, intent_text: str, policy_template: InvariantPolicy) -> IntentCompilerRequest:
    if policy_template.traceKind != "portfolio-candidate":
        raise SynthesisInputError("MVP synthesis requires a portfolio-candidate policy template")
    return IntentCompilerRequest(
        schemaVersion=1,
        kind="intent-compiler-request",
        requestId=request_id,
        intentText=intent_text,
        targetTraceKind="portfolio-candidate",
        fork=policy_template.fork,
        allowedInvariants=["portfolioValueFloor", "cumulativeLossCap"],
        outputUnit="usd-1e18",
        compilerRules=MVP_COMPILER_RULES,
    )


def compile_policy_proposal(
    request: IntentCompilerRequest,
    response: LlmPolicyResponse,
) -> PolicyProposal:
    request_hash = canonical_model_sha256(request)
    if response.requestSha256.lower() != request_hash:
        raise SynthesisInputError("LLM response is not bound to the supplied request")
    if response.policy.traceKind != request.targetTraceKind:
        raise SynthesisInputError("LLM policy traceKind does not match the request")
    if (
        response.policy.fork.chainId != request.fork.chainId
        or response.policy.fork.blockNumber != request.fork.blockNumber
        or response.policy.fork.blockHash.lower() != request.fork.blockHash.lower()
    ):
        raise SynthesisInputError("LLM policy fork does not match the request")

    kinds = [invariant.kind for invariant in response.policy.invariants]
    if kinds != request.allowedInvariants:
        raise SynthesisInputError("LLM policy must contain the two MVP invariants in canonical order")
    ids = [rationale.invariantId for rationale in response.rationales]
    expected_ids = [invariant.id for invariant in response.policy.invariants]
    if ids != expected_ids:
        raise SynthesisInputError("rationales must correspond to policy invariants in canonical order")

    return PolicyProposal(
        schemaVersion=1,
        kind="policy-proposal",
        requestSha256=request_hash,
        request=request,
        compiler=response.compiler,
        policy=response.policy,
        rationales=response.rationales,
        assumptions=response.assumptions,
    )


def approve_policy_proposal(
    proposal: PolicyProposal,
    *,
    confirmation: str,
    approved_by: str,
    approval_scope: str = "user",
) -> PolicyApproval:
    proposal_hash = canonical_model_sha256(proposal)
    expected_confirmation = f"APPROVE {proposal_hash}"
    if confirmation != expected_confirmation:
        raise SynthesisInputError("approval confirmation does not match the exact proposal hash")
    if approval_scope not in {"user", "test-fixture"}:
        raise SynthesisInputError("unsupported approval scope")
    return PolicyApproval(
        schemaVersion=1,
        kind="policy-approval",
        approvalScope=approval_scope,
        approvedBy=approved_by,
        proposalSha256=proposal_hash,
        policySha256=canonical_policy_sha256(proposal.policy),
        confirmation=confirmation,
        proposal=proposal,
    )
