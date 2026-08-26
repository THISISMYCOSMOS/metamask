"""The one shared contract for compiling, approving and executing a floor policy.

Backend imports these models too.  There is exactly one ``PolicyProposal`` shape
and exactly one canonical hash over it, so the text a user reviews, the hash the
user approves, and the policy the evaluator enforces are the same object.

Scope note: the only expressible invariant is ``assetBalanceFloor`` -- keep at
least N base units of one ERC-20 token in one wallet on one chain.  Anything a
user asks for beyond that is recorded as unsupported and blocks approval.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .canonical import canonical_sha256


UINT256_MAX = (1 << 256) - 1
Address = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
Bytes32 = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-f]{64}$")]
Uint = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
IntentText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=400)]
Sentences = Annotated[list[Sentence], Field(max_length=16)]
TokenSymbol = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,16}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _uint(value: str, field: str, *, positive: bool = False) -> int:
    parsed = int(value)
    if parsed > UINT256_MAX:
        raise ValueError(f"{field} exceeds uint256")
    if positive and parsed == 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _non_blank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _no_blank_items(values: list[str], field: str) -> list[str]:
    for item in values:
        _non_blank(item, field)
    return values


class CompileRequest(StrictModel):
    """Authoritative caller input for one compilation.

    Every fact a language model must never choose lives here: chain, wallet,
    token identity and the stable identifiers.  The model only ever sees
    ``intentText`` plus the token metadata needed to reason about units.
    """

    schemaVersion: Literal[1]
    kind: Literal["policy-compile-request"]
    requestId: Identifier
    proposalId: Identifier
    policyId: Identifier
    intentText: IntentText
    chainId: int = Field(ge=1)
    walletAddress: Address
    tokenAddress: Address
    tokenSymbol: TokenSymbol
    tokenDecimals: int = Field(ge=0, le=36)

    @model_validator(mode="after")
    def _check(self) -> "CompileRequest":
        _non_blank(self.intentText, "intentText")
        return self


class CompilerIdentity(StrictModel):
    """Set by backend code from its own configuration; never by model output."""

    provider: Literal["google-gemini", "anthropic"]
    model: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _check(self) -> "CompilerIdentity":
        _non_blank(self.model, "compiler.model")
        return self


class AssetBalanceFloorPolicy(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["assetBalanceFloor"]
    policyId: Identifier
    chainId: int = Field(ge=1)
    walletAddress: Address
    tokenAddress: Address
    assetBalanceFloor: Uint

    @model_validator(mode="after")
    def _check_floor(self) -> "AssetBalanceFloorPolicy":
        _uint(self.assetBalanceFloor, "assetBalanceFloor")
        return self


class PolicyProposal(StrictModel):
    """The single artifact the user reviews and approves by hash.

    ``unsupportedItems`` is required to be empty: a proposal that admits it
    under-enforces the stated intent is not approvable at all, so an approvable
    proposal is by construction one whose whole intent is expressed by its one
    policy.  Non-expressible intent is reported by ``CompilationResult``
    instead, which carries no proposal and therefore cannot be approved.
    """

    schemaVersion: Literal[1]
    kind: Literal["policy-proposal"]
    proposalId: Identifier
    requestSha256: Bytes32
    intentText: IntentText
    compiler: CompilerIdentity
    policy: AssetBalanceFloorPolicy
    policySha256: Bytes32
    rationales: Sentences
    assumptions: Sentences
    unsupportedItems: Annotated[list[Sentence], Field(max_length=0)]

    @model_validator(mode="after")
    def _bind(self) -> "PolicyProposal":
        if self.policySha256 != canonical_sha256(self.policy):
            raise ValueError("policySha256 must bind the exact embedded policy")
        _non_blank(self.intentText, "intentText")
        _no_blank_items(self.rationales, "rationales")
        _no_blank_items(self.assumptions, "assumptions")
        if not self.rationales:
            raise ValueError("a proposal must state at least one rationale for its policy")
        if self.unsupportedItems:
            raise ValueError("an approvable proposal cannot carry unsupported items")
        return self


class CompilationResult(StrictModel):
    """Compilation outcome, approvable only when ``supported`` is true.

    A non-approvable result carries ``proposal=None``.  There is no other
    representation of failure, so no caller can approve an intent that the one
    supported invariant does not fully express.
    """

    schemaVersion: Literal[1]
    kind: Literal["policy-compilation-result"]
    requestId: Identifier
    requestSha256: Bytes32
    supported: bool
    proposal: PolicyProposal | None
    proposalSha256: Bytes32 | None
    rationales: Sentences
    assumptions: Sentences
    unsupportedItems: Sentences
    reasonCodes: Annotated[list[ReasonCode], Field(max_length=16)]

    @model_validator(mode="after")
    def _bind(self) -> "CompilationResult":
        _no_blank_items(self.rationales, "rationales")
        _no_blank_items(self.assumptions, "assumptions")
        _no_blank_items(self.unsupportedItems, "unsupportedItems")
        if self.supported:
            if self.proposal is None:
                raise ValueError("a supported result must carry the proposal it claims")
            if self.unsupportedItems or self.reasonCodes:
                raise ValueError("a supported result must have no unsupported items and no reason codes")
            if self.proposal.requestSha256 != self.requestSha256:
                raise ValueError("the proposal must bind the same request hash as its result")
            if self.proposalSha256 != canonical_sha256(self.proposal):
                raise ValueError("proposalSha256 must bind the exact embedded proposal")
        else:
            if self.proposal is not None or self.proposalSha256 is not None:
                raise ValueError("a non-approvable result must not carry a proposal")
            if not self.unsupportedItems and not self.reasonCodes:
                raise ValueError("a non-approvable result must say why it is not approvable")
        return self

    @property
    def approvable_proposal(self) -> PolicyProposal:
        if not self.supported or self.proposal is None:
            raise ValueError(
                "this compilation is not approvable: " + "; ".join([*self.reasonCodes, *self.unsupportedItems])
            )
        return self.proposal


class ApprovedPolicyEnvelope(StrictModel):
    """Explicit user approval of one exact proposal.

    This is an approval *record*.  It is not cryptographic authentication of the
    approver's identity; ``approvedBy`` is a caller-supplied label.
    """

    schemaVersion: Literal[1]
    kind: Literal["approved-policy-envelope"]
    approvalId: Identifier
    approvalScope: Literal["user"]
    approvedBy: Identifier
    proposal: PolicyProposal
    proposalSha256: Bytes32
    policySha256: Bytes32
    confirmation: Annotated[str, StringConstraints(min_length=1, max_length=80)]

    @model_validator(mode="after")
    def _bind_approval(self) -> "ApprovedPolicyEnvelope":
        proposal_hash = canonical_sha256(self.proposal)
        if self.proposalSha256 != proposal_hash:
            raise ValueError("proposalSha256 must bind the exact embedded proposal")
        if self.policySha256 != self.proposal.policySha256:
            raise ValueError("policySha256 must bind the approved proposal policy")
        if self.confirmation != f"APPROVE {proposal_hash}":
            raise ValueError("confirmation must approve the exact proposal hash")
        return self


class ChainContext(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["chain-context"]
    chainId: int = Field(ge=1)
    blockNumber: Uint
    blockHash: Bytes32
    walletAddress: Address
    senderNonce: Uint
    tokenAddress: Address
    assetBalance: Uint

    @model_validator(mode="after")
    def _check_uints(self) -> "ChainContext":
        for name in ("blockNumber", "senderNonce", "assetBalance"):
            _uint(getattr(self, name), name)
        return self


class HistoryEntry(StrictModel):
    blockNumber: Uint
    blockHash: Bytes32
    transactionHash: Bytes32
    transactionIndex: Uint
    senderNonceAfter: Uint
    assetBalanceAfter: Uint

    @model_validator(mode="after")
    def _check_uints(self) -> "HistoryEntry":
        for name in ("blockNumber", "transactionIndex", "senderNonceAfter", "assetBalanceAfter"):
            _uint(getattr(self, name), name)
        return self


class HistoryLedger(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["history-ledger"]
    context: ChainContext
    entries: Annotated[list[HistoryEntry], Field(max_length=256)]

    @model_validator(mode="after")
    def _ordered_entries(self) -> "HistoryLedger":
        previous: tuple[int, int] | None = None
        hashes: set[str] = set()
        for entry in self.entries:
            point = (int(entry.blockNumber), int(entry.transactionIndex))
            if previous is not None and point <= previous:
                raise ValueError("history entries must be in strict block/transaction order")
            if point[0] > int(self.context.blockNumber):
                raise ValueError("history cannot extend past the captured context")
            if entry.transactionHash in hashes:
                raise ValueError("history transaction hashes must be unique")
            previous = point
            hashes.add(entry.transactionHash)
        return self


class Erc20TransferTransaction(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["evm-transaction"]
    chainId: int = Field(ge=1)
    fromAddress: Address
    toAddress: Address
    nonce: Uint
    gasLimit: Uint
    value: Literal["0"]
    data: Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]*$")]

    @model_validator(mode="after")
    def _check_uints(self) -> "Erc20TransferTransaction":
        _uint(self.nonce, "nonce")
        _uint(self.gasLimit, "gasLimit", positive=True)
        return self


class Simulation(StrictModel):
    """Everything the snapshot simulation actually observed.

    The transfer identity fields exist so the evaluator can compare the exact
    calldata it is about to broadcast against what was simulated, rather than
    trusting a predicted balance that any caller could have computed.
    """

    schemaVersion: Literal[1]
    kind: Literal["erc20-transfer-simulation"]
    status: Literal["success", "reverted", "unknown"]
    chainId: int = Field(ge=1)
    tokenAddress: Address
    senderAddress: Address
    recipientAddress: Address
    transferAmount: Uint
    senderNonce: Uint
    gasLimit: Uint
    gasUsed: Uint
    beforeAssetBalance: Uint
    afterAssetBalance: Uint
    beforeRecipientBalance: Uint
    afterRecipientBalance: Uint

    @model_validator(mode="after")
    def _check_uints(self) -> "Simulation":
        for name in (
            "transferAmount",
            "senderNonce",
            "beforeAssetBalance",
            "afterAssetBalance",
            "beforeRecipientBalance",
            "afterRecipientBalance",
        ):
            _uint(getattr(self, name), name)
        gas_limit = _uint(self.gasLimit, "gasLimit", positive=True)
        if _uint(self.gasUsed, "gasUsed", positive=True) > gas_limit:
            raise ValueError("gasUsed cannot exceed the gasLimit the simulation sent")
        return self


class ExecutionCandidate(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["erc20-transfer-candidate"]
    candidateId: Identifier
    approvalSha256: Bytes32
    policySha256: Bytes32
    context: ChainContext
    history: HistoryLedger
    historySha256: Bytes32
    transaction: Erc20TransferTransaction
    simulation: Simulation

    @model_validator(mode="after")
    def _bind_captured_inputs(self) -> "ExecutionCandidate":
        if self.historySha256 != canonical_sha256(self.history):
            raise ValueError("historySha256 must bind the exact embedded ledger")
        if self.history.context != self.context:
            raise ValueError("ledger context must exactly match candidate context")
        if self.transaction.chainId != self.context.chainId:
            raise ValueError("transaction chainId must match context")
        if self.transaction.fromAddress.lower() != self.context.walletAddress.lower():
            raise ValueError("transaction sender must match context wallet")
        if self.transaction.nonce != self.context.senderNonce:
            raise ValueError("transaction nonce must match context sender nonce")
        return self


class FinalDecision(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["final-decision"]
    candidateId: Identifier
    approvalSha256: Bytes32
    policySha256: Bytes32
    candidateSha256: Bytes32
    executionSha256: Bytes32
    historySha256: Bytes32
    accepted: bool
    reasonCodes: tuple[str, ...]
