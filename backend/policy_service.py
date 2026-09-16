"""Application service for the compile-then-approve path.

Approval is Core's :func:`core.policy_binding.approve` over the exact shared
proposal the user reviewed.  A user may create a separately typed revision,
but it links the prior proposal hash, identifies the changed value as
user-authored, and requires a fresh approval over the revised hash.
"""
from __future__ import annotations

from typing import Protocol

from core.canonical import canonical_sha256
from core.models import (
    ApprovablePolicyProposal,
    ApprovedPolicyEnvelope,
    AssetBalanceFloorPolicy,
    CompilationResult,
    CompileRequest,
    PolicyProposal,
    RevisedPolicyProposal,
    UINT256_MAX,
    UserPolicyRevision,
)
from core.policy_binding import approval_confirmation, approve

from .anthropic_compiler import AnthropicConfig, AnthropicPolicyCompiler
from .compiler_contract import Transport
from .gemini_compiler import GeminiConfig, GeminiPolicyCompiler


class PolicyCompiler(Protocol):
    def compile(self, request: CompileRequest) -> CompilationResult: ...


class PolicyRevisionError(ValueError):
    """A requested user revision is not bound to the proposal being reviewed."""


def revise_asset_balance_floor(
    source: ApprovablePolicyProposal,
    *,
    source_proposal_sha256: str,
    asset_balance_floor: str,
    revised_by: str,
) -> RevisedPolicyProposal:
    """Create a fresh unapproved proposal with explicit user provenance."""
    if not isinstance(source, (PolicyProposal, RevisedPolicyProposal)):
        raise PolicyRevisionError("only a shared balance-floor proposal can be revised")
    expected_source_hash = canonical_sha256(source)
    if source_proposal_sha256 != expected_source_hash:
        raise PolicyRevisionError("source proposal hash does not match the proposal being reviewed")
    if not isinstance(asset_balance_floor, str):
        raise PolicyRevisionError("assetBalanceFloor must be a decimal uint256 string")
    candidate = asset_balance_floor.strip()
    if (
        not candidate
        or not candidate.isdigit()
        or (len(candidate) > 1 and candidate.startswith("0"))
        or int(candidate) > UINT256_MAX
    ):
        raise PolicyRevisionError("assetBalanceFloor must be a canonical decimal uint256 string")
    candidate = str(int(candidate))
    before = source.policy.assetBalanceFloor
    if candidate == before:
        raise PolicyRevisionError("the revised assetBalanceFloor must differ from the current value")

    policy = AssetBalanceFloorPolicy(
        schemaVersion=1,
        kind="assetBalanceFloor",
        policyId=source.policy.policyId,
        chainId=source.policy.chainId,
        walletAddress=source.policy.walletAddress,
        tokenAddress=source.policy.tokenAddress,
        assetBalanceFloor=candidate,
    )
    revision = UserPolicyRevision(
        schemaVersion=1,
        kind="user-policy-revision",
        sourceProposalSha256=expected_source_hash,
        revisedBy=revised_by,
        assetBalanceFloorBefore=before,
        assetBalanceFloorAfter=candidate,
    )
    return RevisedPolicyProposal(
        schemaVersion=1,
        kind="revised-policy-proposal",
        proposalId=source.proposalId,
        requestSha256=source.requestSha256,
        intentText=source.intentText,
        compiler=source.compiler,
        policy=policy,
        policySha256=canonical_sha256(policy),
        rationales=[
            f"사용자가 검토 후 assetBalanceFloor를 {before}에서 {candidate}(으)로 수정했습니다."
        ],
        assumptions=[],
        unsupportedItems=[],
        revision=revision,
    )


class PolicyProposalService:
    """Compile unapproved proposals and record explicit exact-hash approvals."""

    def __init__(self, compiler: PolicyCompiler) -> None:
        self._compiler = compiler

    @classmethod
    def from_env(cls, transport: Transport | None = None) -> "PolicyProposalService":
        """Build the default free-tier Gemini Developer API compiler."""
        return cls(GeminiPolicyCompiler(GeminiConfig.from_env(), transport=transport))

    @classmethod
    def from_anthropic_env(cls, transport: Transport | None = None) -> "PolicyProposalService":
        """Build the optional paid Anthropic compiler explicitly."""
        return cls(AnthropicPolicyCompiler(AnthropicConfig.from_env(), transport=transport))

    def compile(self, request: CompileRequest) -> CompilationResult:
        return self._compiler.compile(request)

    @staticmethod
    def confirmation_sentence(proposal: ApprovablePolicyProposal) -> str:
        """The exact sentence to show the user after they read the proposal."""
        return approval_confirmation(proposal)

    @staticmethod
    def approve(
        proposal: ApprovablePolicyProposal,
        *,
        approval_id: str,
        approved_by: str,
        confirmation: str,
        request: CompileRequest | None = None,
    ) -> ApprovedPolicyEnvelope:
        return approve(
            proposal,
            approval_id=approval_id,
            approved_by=approved_by,
            confirmation=confirmation,
            request=request,
        )
