"""Strict artifacts for the MVP LLM-compiler and explicit approval boundary."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, List, Literal

from pydantic import Field, StringConstraints, model_validator

from invariant_models import (
    CANONICAL_INVARIANT_KINDS,
    Bytes32,
    ForkBinding,
    InvariantPolicy,
    PolicyIdentifier,
    StrictModel,
    canonical_policy_sha256,
)


NonEmptyText = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
ProviderName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


def canonical_model_sha256(model: StrictModel) -> str:
    encoded = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


class IntentCompilerRequest(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["intent-compiler-request"]
    requestId: PolicyIdentifier
    intentText: NonEmptyText
    targetTraceKind: Literal["portfolio-candidate"]
    fork: ForkBinding
    allowedInvariants: Annotated[
        List[
            Literal[
                "portfolioValueFloor",
                "portfolioDrawdownCapBps",
                "cumulativeLossCap",
                "cumulativeLossCapBps",
            ]
        ],
        Field(min_length=1, max_length=4),
    ]
    outputUnit: Literal["usd-1e18"]
    compilerRules: Annotated[List[NonEmptyText], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def _check_allowed_invariants(self) -> "IntentCompilerRequest":
        if len(set(self.allowedInvariants)) != len(self.allowedInvariants):
            raise ValueError("allowedInvariants must not contain duplicates")
        expected = [kind for kind in CANONICAL_INVARIANT_KINDS if kind in self.allowedInvariants]
        if self.allowedInvariants != expected:
            raise ValueError("allowedInvariants must be a canonical-order subset of the four invariant kinds")
        return self


class CompilerIdentity(StrictModel):
    provider: ProviderName
    model: ProviderName


class InvariantRationale(StrictModel):
    invariantId: PolicyIdentifier
    summary: NonEmptyText


class LlmPolicyResponse(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["llm-policy-response"]
    requestSha256: Bytes32
    compiler: CompilerIdentity
    policy: InvariantPolicy
    rationales: Annotated[List[InvariantRationale], Field(min_length=1, max_length=16)]
    assumptions: Annotated[List[NonEmptyText], Field(max_length=16)]


class PolicyProposal(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["policy-proposal"]
    requestSha256: Bytes32
    request: IntentCompilerRequest
    compiler: CompilerIdentity
    policy: InvariantPolicy
    rationales: Annotated[List[InvariantRationale], Field(min_length=1, max_length=16)]
    assumptions: Annotated[List[NonEmptyText], Field(max_length=16)]

    @model_validator(mode="after")
    def _check_request_and_policy_links(self) -> "PolicyProposal":
        expected_request_hash = canonical_model_sha256(self.request)
        if self.requestSha256.lower() != expected_request_hash:
            raise ValueError("requestSha256 does not match embedded request")
        if self.policy.traceKind != self.request.targetTraceKind:
            raise ValueError("policy traceKind does not match embedded request")
        if (
            self.policy.fork.chainId != self.request.fork.chainId
            or self.policy.fork.blockNumber != self.request.fork.blockNumber
            or self.policy.fork.blockHash.lower() != self.request.fork.blockHash.lower()
        ):
            raise ValueError("policy fork does not match embedded request")
        if [invariant.kind for invariant in self.policy.invariants] != self.request.allowedInvariants:
            raise ValueError("policy invariants do not match embedded request")
        if [rationale.invariantId for rationale in self.rationales] != [
            invariant.id for invariant in self.policy.invariants
        ]:
            raise ValueError("rationales do not match policy invariants")
        return self


class PolicyApproval(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["policy-approval"]
    approvalScope: Literal["user", "test-fixture"]
    approvedBy: PolicyIdentifier
    proposalSha256: Bytes32
    policySha256: Bytes32
    confirmation: NonEmptyText
    proposal: PolicyProposal

    @model_validator(mode="after")
    def _check_hashes_and_confirmation(self) -> "PolicyApproval":
        expected_proposal_hash = canonical_model_sha256(self.proposal)
        if self.proposalSha256.lower() != expected_proposal_hash:
            raise ValueError("proposalSha256 does not match embedded proposal")
        expected_policy_hash = canonical_policy_sha256(self.proposal.policy)
        if self.policySha256.lower() != expected_policy_hash:
            raise ValueError("policySha256 does not match embedded policy")
        if self.confirmation != f"APPROVE {expected_proposal_hash}":
            raise ValueError("confirmation must approve the exact proposal hash")
        return self
