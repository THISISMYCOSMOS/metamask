"""Strict contracts for a transaction candidate evaluated before broadcast."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from candidate_models import CandidateTrace
from invariant_models import Bytes32, PolicyIdentifier, StrictModel, UintStr, UINT256_MAX


Address = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
HexData = Annotated[str, StringConstraints(pattern=r"^0x(?:[0-9a-fA-F]{2})*$")]


def _require_uint256(value: str, field_name: str) -> None:
    if int(value) > UINT256_MAX:
        raise ValueError(f"{field_name} exceeds uint256")


class ExecutionRequest(StrictModel):
    chainId: int = Field(ge=1)
    fromAddress: Address
    toAddress: Address
    value: UintStr
    data: HexData
    gas: UintStr
    nonce: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "ExecutionRequest":
        _require_uint256(self.value, "value")
        _require_uint256(self.gas, "gas")
        _require_uint256(self.nonce, "nonce")
        return self


class ExecutionContext(StrictModel):
    chainId: int = Field(ge=1)
    currentBlockNumber: UintStr
    currentBlockHash: Bytes32
    senderNonce: UintStr

    @model_validator(mode="after")
    def _check_uints(self) -> "ExecutionContext":
        _require_uint256(self.currentBlockNumber, "currentBlockNumber")
        _require_uint256(self.senderNonce, "senderNonce")
        return self


class PreExecutionCandidate(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["pre-execution-candidate"]
    candidateId: PolicyIdentifier
    historySha256: Bytes32
    context: ExecutionContext
    execution: ExecutionRequest
    portfolioCandidate: CandidateTrace

    @model_validator(mode="after")
    def _check_links(self) -> "PreExecutionCandidate":
        if self.context.chainId != self.execution.chainId:
            raise ValueError("execution chainId must match captured context")
        if self.execution.chainId != self.portfolioCandidate.fork.chainId:
            raise ValueError("execution chainId must match candidate fork")
        if self.execution.nonce != self.context.senderNonce:
            raise ValueError("execution nonce must match captured sender nonce")
        if int(self.context.currentBlockNumber) < int(self.portfolioCandidate.fork.blockNumber):
            raise ValueError("current block cannot precede the candidate fork")
        if self.historySha256.lower() != self.portfolioCandidate.sourceTraceHashedContentSha256.lower():
            raise ValueError("history hash must match the embedded portfolio candidate")
        return self
