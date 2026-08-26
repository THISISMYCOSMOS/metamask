"""Turn one reviewed :class:`PolicyProposal` into one user approval.

There is a single proposal artifact in this system and a single approval over
it.  Backend assembles that proposal from a trusted :class:`CompileRequest` plus
validated model output; the user reads *that* proposal and confirms the exact
sentence :func:`approval_confirmation` returns.  Nothing is translated,
re-hashed, or re-proposed in between, so "the text the user read" and "the
policy the evaluator enforces" cannot drift apart.

Approval is an explicit *record* of consent, not cryptographic authentication of
who gave it.
"""
from __future__ import annotations

from .canonical import canonical_sha256
from .models import ApprovedPolicyEnvelope, CompileRequest, PolicyProposal


class PolicyApprovalError(ValueError):
    """The proposal cannot be approved as presented."""


def verify_request_binding(proposal: PolicyProposal, request: CompileRequest) -> None:
    """Fail closed unless ``proposal`` is the compilation of exactly ``request``.

    Re-derives the request hash instead of trusting the field the proposal
    carries, and re-checks every fact the caller -- never the model -- supplied.
    """
    if not isinstance(proposal, PolicyProposal) or not isinstance(request, CompileRequest):
        raise PolicyApprovalError("request binding requires the shared CompileRequest and PolicyProposal models")
    if proposal.requestSha256 != canonical_sha256(request):
        raise PolicyApprovalError("proposal does not bind the exact compile request")
    if proposal.proposalId != request.proposalId:
        raise PolicyApprovalError("proposalId does not match the compile request")
    if proposal.intentText != request.intentText:
        raise PolicyApprovalError("proposal intentText does not match the compile request")
    policy = proposal.policy
    if policy.policyId != request.policyId:
        raise PolicyApprovalError("policyId does not match the compile request")
    if policy.chainId != request.chainId:
        raise PolicyApprovalError("policy chainId does not match the compile request")
    if policy.walletAddress.lower() != request.walletAddress.lower():
        raise PolicyApprovalError("policy walletAddress does not match the compile request")
    if policy.tokenAddress.lower() != request.tokenAddress.lower():
        raise PolicyApprovalError("policy tokenAddress does not match the compile request")


def approval_confirmation(proposal: PolicyProposal) -> str:
    """Return the exact sentence the user must type to approve ``proposal``."""
    if not isinstance(proposal, PolicyProposal):
        raise PolicyApprovalError("only the shared PolicyProposal model can be approved")
    return f"APPROVE {canonical_sha256(proposal)}"


def approve(
    proposal: PolicyProposal,
    *,
    approval_id: str,
    approved_by: str,
    confirmation: str,
    request: CompileRequest | None = None,
) -> ApprovedPolicyEnvelope:
    """Record an explicit user approval of this exact proposal.

    ``confirmation`` must equal :func:`approval_confirmation` byte for byte; any
    edit to the proposal after the user read it changes the hash and therefore
    invalidates the confirmation the user gave.  Supplying ``request``
    additionally re-proves the proposal is the compilation of that request.
    """
    expected = approval_confirmation(proposal)
    if request is not None:
        verify_request_binding(proposal, request)
    if confirmation != expected:
        raise PolicyApprovalError("confirmation must be exactly the APPROVE sentence for this proposal hash")
    return ApprovedPolicyEnvelope(
        schemaVersion=1,
        kind="approved-policy-envelope",
        approvalId=approval_id,
        approvalScope="user",
        approvedBy=approved_by,
        proposal=proposal,
        proposalSha256=canonical_sha256(proposal),
        policySha256=proposal.policySha256,
        confirmation=confirmation,
    )
