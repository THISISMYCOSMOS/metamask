"""Deterministic construction and validation for the MVP synthesis artifacts."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from invariant_models import ForkBinding, Invariant, InvariantPolicy, canonical_policy_sha256
from synthesis_models import (
    CompilerIdentity,
    IntentCompilerRequest,
    InvariantRationale,
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

PERCENTAGE_INVARIANT_KINDS = frozenset({"portfolioDrawdownCapBps", "cumulativeLossCapBps"})

STRUCTURED_EDITOR_REQUEST_ID = "mvp-structured-intent-001"

LOCAL_STRUCTURED_COMPILER = CompilerIdentity(provider="local", model="structured-editor-v1")

STRUCTURED_EDITOR_ASSUMPTION = (
    "Structured condition fields supplied through the local editor are authoritative; the "
    "original intent text is preserved for review only and is not used to infer conditions."
)

PERCENTAGE_COMPILER_RULES = [
    "Return JSON only and do not add fields outside the response schema.",
    "Generate only invariant kinds listed in allowedInvariants.",
    "Represent every USD threshold as an unsigned decimal string scaled by 1e18.",
    'Represent percentage thresholds as basis points: 20 percent is the integer string "2000".',
    "Represent all BPS values as integer strings from 0 through 10000, inclusive.",
    "portfolioDrawdownCapBps requires an explicit referenceValue1e18.",
    "Never infer a missing referenceValue1e18 or any unrequested condition.",
    "LLM output is an unapproved policy proposal only; never infer user approval.",
    "If the intent is ambiguous, record the ambiguity in assumptions instead of inventing hidden policy.",
]


def artifact_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def write_artifact(path: Path, model: BaseModel) -> None:
    path.write_text(artifact_json(model), encoding="utf-8", newline="\n")


def create_intent_request(*, request_id: str, intent_text: str, policy_template: InvariantPolicy) -> IntentCompilerRequest:
    if policy_template.traceKind != "portfolio-candidate":
        raise SynthesisInputError("MVP synthesis requires a portfolio-candidate policy template")
    allowed_invariants = [invariant.kind for invariant in policy_template.invariants]
    compiler_rules = list(MVP_COMPILER_RULES)
    if any(kind in PERCENTAGE_INVARIANT_KINDS for kind in allowed_invariants):
        compiler_rules = list(PERCENTAGE_COMPILER_RULES)
    try:
        return IntentCompilerRequest(
            schemaVersion=1,
            kind="intent-compiler-request",
            requestId=request_id,
            intentText=intent_text,
            targetTraceKind="portfolio-candidate",
            fork=policy_template.fork,
            allowedInvariants=allowed_invariants,
            outputUnit="usd-1e18",
            compilerRules=compiler_rules,
        )
    except ValueError as exc:
        raise SynthesisInputError(str(exc)) from exc


def build_structured_policy(
    *,
    policy_id: str,
    fork: ForkBinding,
    invariants: list[Invariant],
) -> InvariantPolicy:
    try:
        return InvariantPolicy(
            schemaVersion=1,
            policyId=policy_id,
            traceKind="portfolio-candidate",
            fork=fork,
            invariants=invariants,
        )
    except ValueError as exc:
        raise SynthesisInputError(str(exc)) from exc


def compile_local_structured_proposal(
    request: IntentCompilerRequest,
    invariants: list[Invariant],
) -> PolicyProposal:
    """Deterministically compile a policy proposal from structured editor fields.

    This never calls a provider; it is the local structured-editor compiler path
    used when a user edits canonical invariant conditions directly instead of
    relying on a bound offline LLM response.
    """
    try:
        policy = InvariantPolicy(
            schemaVersion=1,
            policyId=request.requestId,
            traceKind=request.targetTraceKind,
            fork=request.fork,
            invariants=invariants,
        )
        rationales = [
            InvariantRationale(
                invariantId=invariant.id,
                summary=(
                    f"Value supplied directly through the structured condition editor for {invariant.kind}."
                ),
            )
            for invariant in policy.invariants
        ]
        return PolicyProposal(
            schemaVersion=1,
            kind="policy-proposal",
            requestSha256=canonical_model_sha256(request),
            request=request,
            compiler=LOCAL_STRUCTURED_COMPILER,
            policy=policy,
            rationales=rationales,
            assumptions=[STRUCTURED_EDITOR_ASSUMPTION],
        )
    except ValueError as exc:
        raise SynthesisInputError(str(exc)) from exc


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
        raise SynthesisInputError("LLM policy invariant kinds must exactly match allowedInvariants")
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
