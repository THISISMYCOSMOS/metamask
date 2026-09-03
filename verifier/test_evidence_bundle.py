from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from core.canonical import canonical_sha256
from verifier.evidence_bundle_models import (
    BroadcastOutcome,
    EvidenceBundle,
    EvidenceSource,
    Reproducibility,
)
from verifier.export_evidence_bundle import REDACTION_RULESET_SHA256, build_bundle


ROOT = Path(__file__).resolve().parents[1]


def reproducibility() -> Reproducibility:
    return Reproducibility(
        repositoryCommit="1" * 40,
        repositoryBranch="feat",
        repositoryDirty=True,
        delegationFrameworkCommit="2" * 40,
        delegationFrameworkDirty=False,
        fileSha256={"lock.json": "0x" + "3" * 64},
    )


def source(case: str) -> EvidenceSource:
    accepted = case.endswith("accept")
    broadcast = case == "live-floor-accept"
    return EvidenceSource.model_validate(
        {
            "schemaVersion": 1,
            "kind": "research-evidence-source",
            "evidenceId": case,
            "case": case,
            "claimScope": (
                "application-level-testnet"
                if case.startswith("live-")
                else "offline-counterfactual-control" if accepted else "offline-counterexample"
            ),
            "network": {
                "chainId": 11155111 if case.startswith("live-") else 1,
                "networkName": "ethereum-sepolia" if case.startswith("live-") else "ethereum-mainnet-fork",
                "environment": "public-testnet" if case.startswith("live-") else "pinned-mainnet-fork",
                "rpcClass": "public-testnet" if case.startswith("live-") else "pinned-fork",
                "blockNumber": "1",
                "blockHash": "0x" + "4" * 64,
            },
            "artifacts": [
                {
                    "name": "proposal",
                    "evidenceClass": "configured",
                    "value": {"intentText": "USDC를 남겨줘", "apiKey": "AIza" + "A" * 35},
                    "source": "synthetic test proposal",
                },
                {
                    "name": "decision",
                    "evidenceClass": "generated",
                    "value": {"accepted": accepted, "copiedValue": "https://user:secret@example.test/v1/key"},
                    "source": "synthetic deterministic decision",
                },
            ],
            "outcome": {
                "accepted": accepted,
                "broadcastAttempted": broadcast,
                "txHash": "0x" + "5" * 64 if broadcast else None,
                "receiptStatus": "success" if broadcast else "not-broadcast" if case == "live-floor-preflight-reject" else "not-applicable",
                "walletNativeEnforcement": False,
            },
            "claims": [
                {
                    "path": "/artifacts/decision/value/accepted",
                    "evidenceClass": "generated",
                    "source": "test assertion",
                }
            ],
            "limitations": ["Synthetic unit-test source only."],
        }
    )


class EvidenceBundleTests(unittest.TestCase):
    def test_all_four_case_shapes_validate(self) -> None:
        for case in (
            "offline-g3-reject",
            "offline-benign-accept",
            "live-floor-accept",
            "live-floor-preflight-reject",
        ):
            with self.subTest(case=case):
                bundle = build_bundle(
                    source(case),
                    generated_at="2026-09-04T00:00:00Z",
                    reproducibility=reproducibility(),
                )
                self.assertEqual(bundle.payloadSha256, canonical_sha256(bundle.payload))

    def test_redaction_happens_before_artifact_and_payload_hashing(self) -> None:
        bundle = build_bundle(
            source("offline-g3-reject"),
            generated_at="2026-09-04T00:00:00Z",
            reproducibility=reproducibility(),
        )
        serialized = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn("AIza", serialized)
        self.assertNotIn("user:secret", serialized)
        self.assertNotIn("apiKey", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertEqual(REDACTION_RULESET_SHA256, bundle.envelope.redactionRulesetSha256)
        for artifact in bundle.payload.artifacts:
            self.assertEqual(artifact.sha256, canonical_sha256(artifact.value))

    def test_wall_clock_is_outside_the_deterministic_payload(self) -> None:
        first = build_bundle(source("offline-benign-accept"), generated_at="2026-09-04T00:00:00Z", reproducibility=reproducibility())
        second = build_bundle(source("offline-benign-accept"), generated_at="2026-09-04T00:00:01Z", reproducibility=reproducibility())
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(first.payloadSha256, second.payloadSha256)
        self.assertNotEqual(first.envelope.generatedAtUtc, second.envelope.generatedAtUtc)

    def test_broadcast_hash_semantics_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            BroadcastOutcome(
                accepted=False,
                broadcastAttempted=False,
                txHash="0x" + "5" * 64,
                receiptStatus="not-broadcast",
                walletNativeEnforcement=False,
            )

    def test_committed_bundles_validate_and_match_case_semantics(self) -> None:
        expected = {
            "offline-g3-reject": (False, False),
            "offline-benign-accept": (True, False),
            "live-floor-accept": (True, True),
            "live-floor-preflight-reject": (False, False),
        }
        for name, outcome in expected.items():
            with self.subTest(name=name):
                path = ROOT / "research" / "evidence" / "bundles" / f"{name}.bundle.json"
                bundle = EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
                self.assertEqual(outcome, (bundle.payload.outcome.accepted, bundle.payload.outcome.broadcastAttempted))
                self.assertEqual(bundle.payloadSha256, canonical_sha256(bundle.payload))


if __name__ == "__main__":
    unittest.main()
