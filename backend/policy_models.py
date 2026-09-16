"""The model-output contract and the code that assembles the shared proposal.

Division of authority, which is the whole point of this module:

* The **caller** owns :class:`core.models.CompileRequest` -- chain, wallet,
  token, and every identifier.  A language model never chooses those.
* The **model** may only say whether the one supported ``assetBalanceFloor`` is
  expressible, what the floor is in base units, and what it could not express.
* **Backend code** owns the compiler identity and assembles the final
  :class:`core.models.PolicyProposal` from those two inputs.

There is exactly one proposal artifact and one hash over it, shared with Core.
Nothing here converts, re-proposes, or re-hashes anything downstream.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from core.canonical import canonical_sha256
from core.models import (
    UINT256_MAX,
    AssetBalanceFloorPolicy,
    CompilationResult,
    CompileRequest,
    CompilerIdentity,
    PolicyProposal,
    Sentence,
)


class ContractError(ValueError):
    """A value violates the compilation contract."""


class PolicyFloorOutput(BaseModel):
    """Exactly what the model is allowed to decide, and nothing else.

    ``minimumBalanceBaseUnits`` is a string so a large uint256 survives JSON
    without float rounding.  It is deliberately *not* pattern-constrained here:
    a malformed or missing floor is a non-approvable compilation, not a crash.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    supported: bool
    minimumBalanceBaseUnits: Annotated[str, StringConstraints(max_length=80)] | None
    rationales: Annotated[list[Sentence], Field(max_length=16)]
    assumptions: Annotated[list[Sentence], Field(max_length=16)]
    unsupportedItems: Annotated[list[Sentence], Field(max_length=16)]

    @classmethod
    def parse(cls, value: Any) -> "PolicyFloorOutput":
        try:
            return cls.model_validate(value)
        except ValidationError as exc:
            raise ContractError(f"model output violated the policy output schema: {exc.error_count()} error(s)") from exc


def _clean(items: list[str]) -> list[str]:
    return [item.strip() for item in items if item.strip()]


def _floor_reason(value: str | None) -> str | None:
    """Return the reason ``value`` is not a usable base-unit floor, or None."""
    if value is None:
        return "MINIMUM_BALANCE_MISSING"
    candidate = value.strip()
    if not candidate or not candidate.isdigit() or (len(candidate) > 1 and candidate.startswith("0")):
        return "MINIMUM_BALANCE_NOT_A_BASE_UNIT_INTEGER"
    if int(candidate) > UINT256_MAX:
        return "MINIMUM_BALANCE_EXCEEDS_UINT256"
    return None


def _not_approvable(
    request: CompileRequest,
    request_hash: str,
    output: PolicyFloorOutput | None,
    reason_codes: list[str],
    unsupported: list[str],
) -> CompilationResult:
    return CompilationResult(
        schemaVersion=1,
        kind="policy-compilation-result",
        requestId=request.requestId,
        requestSha256=request_hash,
        supported=False,
        proposal=None,
        proposalSha256=None,
        rationales=_clean(output.rationales) if output else [],
        assumptions=_clean(output.assumptions) if output else [],
        unsupportedItems=unsupported,
        reasonCodes=reason_codes,
    )


def assemble_compilation(
    request: CompileRequest,
    output: PolicyFloorOutput,
    *,
    compiler: CompilerIdentity,
) -> CompilationResult:
    """Build the final shared proposal, or a structured non-approvable result.

    Mixed intent fails closed: if the model reports anything it could not
    express, no proposal is produced at all.  Producing one anyway would put a
    hash in front of the user that under-enforces the intent that hash is
    presented as approving.  There is no flag to relax this.
    """
    if not isinstance(request, CompileRequest):
        raise ContractError("request must be the shared CompileRequest model")
    if not isinstance(compiler, CompilerIdentity):
        raise ContractError("compiler identity must be set by backend code, never by model output")
    request_hash = canonical_sha256(request)

    unsupported = _clean(output.unsupportedItems)
    reasons: list[str] = []
    if not output.supported:
        reasons.append("MODEL_REPORTED_UNSUPPORTED")
    if unsupported:
        reasons.append("UNSUPPORTED_ITEMS_PRESENT")

    floor_reason = _floor_reason(output.minimumBalanceBaseUnits)
    if floor_reason is not None:
        reasons.append(floor_reason)

    rationales = _clean(output.rationales)
    if not rationales:
        reasons.append("NO_RATIONALE_FOR_POLICY")

    if reasons:
        return _not_approvable(request, request_hash, output, reasons, unsupported)

    assert output.minimumBalanceBaseUnits is not None
    policy = AssetBalanceFloorPolicy(
        schemaVersion=1,
        kind="assetBalanceFloor",
        policyId=request.policyId,
        chainId=request.chainId,
        walletAddress=request.walletAddress.lower(),
        tokenAddress=request.tokenAddress.lower(),
        assetBalanceFloor=str(int(output.minimumBalanceBaseUnits.strip())),
    )
    proposal = PolicyProposal(
        schemaVersion=1,
        kind="policy-proposal",
        proposalId=request.proposalId,
        requestSha256=request_hash,
        intentText=request.intentText,
        compiler=compiler,
        policy=policy,
        policySha256=canonical_sha256(policy),
        rationales=rationales,
        assumptions=_clean(output.assumptions),
        unsupportedItems=[],
    )
    return CompilationResult(
        schemaVersion=1,
        kind="policy-compilation-result",
        requestId=request.requestId,
        requestSha256=request_hash,
        supported=True,
        proposal=proposal,
        proposalSha256=canonical_sha256(proposal),
        rationales=proposal.rationales,
        assumptions=proposal.assumptions,
        unsupportedItems=[],
        reasonCodes=[],
    )


def refusal_result(request: CompileRequest, reason_code: str, detail: str) -> CompilationResult:
    """A non-approvable result for a failure that never produced model output."""
    return CompilationResult(
        schemaVersion=1,
        kind="policy-compilation-result",
        requestId=request.requestId,
        requestSha256=canonical_sha256(request),
        supported=False,
        proposal=None,
        proposalSha256=None,
        rationales=[],
        assumptions=[],
        unsupportedItems=[detail] if detail.strip() else [],
        reasonCodes=[reason_code],
    )
