"""Strict contracts for deterministic, secret-safe research evidence bundles.

The ``payload`` is the only hashed surface.  Wall-clock generation metadata
lives in ``envelope`` so exporting the same validated source twice produces the
same ``payloadSha256``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from core.canonical import canonical_sha256


Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,95}$")]
Hash32 = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-f]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Uint = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
NonBlank = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
JsonObject = dict[str, Any]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


EvidenceCase = Literal[
    "offline-g3-reject",
    "offline-benign-accept",
    "live-floor-accept",
    "live-floor-preflight-reject",
]
EvidenceClass = Literal["configured", "observed", "inferred", "generated", "external-chain", "reported"]


class NetworkContext(StrictModel):
    chainId: int = Field(ge=1)
    networkName: Identifier
    environment: Literal["pinned-mainnet-fork", "public-testnet"]
    rpcClass: Literal["pinned-fork", "public-testnet", "browser-wallet-rpc", "committed-record"]
    blockNumber: Uint | None = None
    blockHash: Hash32 | None = None

    @model_validator(mode="after")
    def _block_pair(self) -> "NetworkContext":
        if (self.blockNumber is None) != (self.blockHash is None):
            raise ValueError("blockNumber and blockHash must be supplied together")
        return self


class EvidenceArtifactSource(StrictModel):
    name: Identifier
    evidenceClass: EvidenceClass
    value: JsonObject
    source: NonBlank


class EvidenceArtifact(EvidenceArtifactSource):
    sha256: Hash32

    @model_validator(mode="after")
    def _bind_value(self) -> "EvidenceArtifact":
        if self.sha256 != canonical_sha256(self.value):
            raise ValueError(f"artifact {self.name} sha256 does not bind its exact value")
        return self


class EvidenceClaim(StrictModel):
    path: Annotated[str, StringConstraints(pattern=r"^/[^\r\n]*$", max_length=500)]
    evidenceClass: EvidenceClass
    source: NonBlank


class BroadcastOutcome(StrictModel):
    accepted: bool
    broadcastAttempted: bool
    txHash: Hash32 | None
    receiptStatus: Literal["not-applicable", "not-broadcast", "submitted", "success", "reverted", "unknown"]
    walletNativeEnforcement: Literal[False] = False

    @model_validator(mode="after")
    def _bind_broadcast(self) -> "BroadcastOutcome":
        if self.broadcastAttempted != (self.txHash is not None):
            raise ValueError("txHash must be present if and only if broadcastAttempted is true")
        if self.broadcastAttempted and self.receiptStatus in {"not-applicable", "not-broadcast"}:
            raise ValueError("a broadcast outcome requires a submitted or receipt status")
        if not self.broadcastAttempted and self.receiptStatus not in {"not-applicable", "not-broadcast"}:
            raise ValueError("a no-broadcast outcome cannot carry a submitted or receipt status")
        return self


class EvidenceSource(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["research-evidence-source"]
    evidenceId: Identifier
    case: EvidenceCase
    claimScope: Literal["offline-counterexample", "offline-counterfactual-control", "application-level-testnet"]
    network: NetworkContext
    artifacts: Annotated[list[EvidenceArtifactSource], Field(min_length=2, max_length=32)]
    outcome: BroadcastOutcome
    claims: Annotated[list[EvidenceClaim], Field(min_length=1, max_length=64)]
    limitations: Annotated[list[NonBlank], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def _case_semantics(self) -> "EvidenceSource":
        names = [artifact.name for artifact in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError("artifact names must be unique")
        paths = [claim.path for claim in self.claims]
        if len(paths) != len(set(paths)):
            raise ValueError("claim paths must be unique")
        offline = self.case.startswith("offline-")
        if offline and self.outcome.broadcastAttempted:
            raise ValueError("offline evidence cannot claim a broadcast")
        if self.case.endswith("reject") and self.outcome.accepted:
            raise ValueError("reject evidence must carry accepted=false")
        if self.case.endswith("accept") and not self.outcome.accepted:
            raise ValueError("accept evidence must carry accepted=true")
        if self.case == "live-floor-accept":
            if not self.outcome.broadcastAttempted or self.outcome.receiptStatus != "success":
                raise ValueError("live-floor-accept requires a successful broadcast receipt")
        if self.case == "live-floor-preflight-reject" and self.outcome.receiptStatus != "not-broadcast":
            raise ValueError("live preflight rejection must explicitly record not-broadcast")
        return self


class Reproducibility(StrictModel):
    repositoryCommit: GitCommit
    repositoryBranch: Identifier
    repositoryDirty: bool
    delegationFrameworkCommit: GitCommit
    delegationFrameworkDirty: bool
    fileSha256: dict[str, Hash32]


class EvidencePayload(StrictModel):
    schemaVersion: Literal[1]
    evidenceId: Identifier
    case: EvidenceCase
    claimScope: Literal["offline-counterexample", "offline-counterfactual-control", "application-level-testnet"]
    reproducibility: Reproducibility
    network: NetworkContext
    artifacts: Annotated[list[EvidenceArtifact], Field(min_length=2, max_length=32)]
    outcome: BroadcastOutcome
    claims: Annotated[list[EvidenceClaim], Field(min_length=1, max_length=64)]
    limitations: Annotated[list[NonBlank], Field(min_length=1, max_length=32)]
    redactedPaths: list[str]


class EvidenceEnvelope(StrictModel):
    generatedAtUtc: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
    exporterVersion: Literal["1.0.0"]
    redactionRulesetSha256: Hash32


class EvidenceBundle(StrictModel):
    schemaVersion: Literal[1]
    kind: Literal["research-evidence-bundle"]
    envelope: EvidenceEnvelope
    payload: EvidencePayload
    payloadSha256: Hash32

    @model_validator(mode="after")
    def _bind_payload(self) -> "EvidenceBundle":
        if self.payloadSha256 != canonical_sha256(self.payload):
            raise ValueError("payloadSha256 does not bind the exact payload")
        return self
