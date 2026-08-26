"""Application service for the compile-then-approve path.

Approval is Core's :func:`core.policy_binding.approve` over the same shared
proposal the compiler produced.  There is no second proposal and no second
approval artifact anywhere in this path.
"""
from __future__ import annotations

from typing import Protocol

from core.models import ApprovedPolicyEnvelope, CompilationResult, CompileRequest, PolicyProposal
from core.policy_binding import approval_confirmation, approve

from .anthropic_compiler import AnthropicConfig, AnthropicPolicyCompiler
from .compiler_contract import Transport
from .gemini_compiler import GeminiConfig, GeminiPolicyCompiler


class PolicyCompiler(Protocol):
    def compile(self, request: CompileRequest) -> CompilationResult: ...


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
    def confirmation_sentence(proposal: PolicyProposal) -> str:
        """The exact sentence to show the user after they read the proposal."""
        return approval_confirmation(proposal)

    @staticmethod
    def approve(
        proposal: PolicyProposal,
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
