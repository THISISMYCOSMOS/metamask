#!/usr/bin/env python3
"""Export one strict, deterministic and secret-safe research evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import canonical_sha256  # noqa: E402
from verifier.evidence_bundle_models import (  # noqa: E402
    EvidenceArtifact,
    EvidenceBundle,
    EvidenceEnvelope,
    EvidencePayload,
    EvidenceSource,
    Reproducibility,
)


EXPORTER_VERSION = "1.0.0"
SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "geminiapikey",
        "anthropicapikey",
        "privatekey",
        "mnemonic",
        "seedphrase",
        "password",
        "authorization",
        "cookie",
        "setcookie",
        "accesstoken",
        "refreshtoken",
        "rpcurl",
        "agentwalletrpcurl",
        "anvilrpcurl",
        "mmclipath",
        "secret",
    }
)
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-ant-[0-9A-Za-z_-]{10,}"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._~+/=-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
REDACTION_RULESET = {
    "version": 1,
    "sensitiveKeys": sorted(SENSITIVE_KEYS),
    "valuePatterns": [pattern.pattern for pattern in SECRET_PATTERNS],
    "urlPolicy": "redact-all-http-ws-urls",
}
REDACTION_RULESET_SHA256 = canonical_sha256(REDACTION_RULESET)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _looks_like_url(value: str) -> bool:
    try:
        return urlsplit(value).scheme.lower() in {"http", "https", "ws", "wss"}
    except ValueError:
        return True


def _sanitize(value: Any, path: str, redacted: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}/{key}"
            if _normalized_key(str(key)) in SENSITIVE_KEYS:
                redacted.append(f"{path}/[SENSITIVE_KEY]")
                continue
            sanitized[str(key)] = _sanitize(item, child_path, redacted)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, f"{path}/{index}", redacted) for index, item in enumerate(value)]
    if isinstance(value, str):
        if _looks_like_url(value) or any(pattern.search(value) for pattern in SECRET_PATTERNS):
            redacted.append(path)
            return "[REDACTED]"
    return value


def _git(*args: str, cwd: Path = ROOT) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    return "0x" + hashlib.sha256(path.read_bytes()).hexdigest()


def collect_reproducibility(root: Path = ROOT) -> Reproducibility:
    submodule = root / "chain" / "lib" / "delegation-framework"
    repository_status = _git("status", "--porcelain", cwd=root)
    submodule_status = _git("status", "--porcelain", cwd=submodule)
    tracked_files = (
        root / "verifier" / "uv.lock",
        root / "chain" / "package-lock.json",
        root / "research" / "data" / "compiler_cases.json",
        root / "verifier" / "evidence_bundle_models.py",
        root / "verifier" / "export_evidence_bundle.py",
    )
    return Reproducibility(
        repositoryCommit=_git("rev-parse", "HEAD", cwd=root).lower(),
        repositoryBranch=_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root),
        repositoryDirty=bool(repository_status),
        delegationFrameworkCommit=_git("rev-parse", "HEAD", cwd=submodule).lower(),
        delegationFrameworkDirty=bool(submodule_status),
        fileSha256={path.relative_to(root).as_posix(): _file_sha256(path) for path in tracked_files},
    )


def build_bundle(
    source: EvidenceSource,
    *,
    generated_at: str | None = None,
    reproducibility: Reproducibility | None = None,
) -> EvidenceBundle:
    redacted_paths: list[str] = []
    artifacts: list[EvidenceArtifact] = []
    for artifact in source.artifacts:
        value = _sanitize(
            artifact.value,
            f"/artifacts/{artifact.name}/value",
            redacted_paths,
        )
        artifacts.append(
            EvidenceArtifact(
                name=artifact.name,
                evidenceClass=artifact.evidenceClass,
                value=value,
                source=artifact.source,
                sha256=canonical_sha256(value),
            )
        )
    payload = EvidencePayload(
        schemaVersion=1,
        evidenceId=source.evidenceId,
        case=source.case,
        claimScope=source.claimScope,
        reproducibility=reproducibility or collect_reproducibility(),
        network=source.network,
        artifacts=artifacts,
        outcome=source.outcome,
        claims=source.claims,
        limitations=source.limitations,
        redactedPaths=sorted(set(redacted_paths)),
    )
    timestamp = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return EvidenceBundle(
        schemaVersion=1,
        kind="research-evidence-bundle",
        envelope=EvidenceEnvelope(
            generatedAtUtc=timestamp,
            exporterVersion=EXPORTER_VERSION,
            redactionRulesetSha256=REDACTION_RULESET_SHA256,
        ),
        payload=payload,
        payloadSha256=canonical_sha256(payload),
    )


def write_bundle(bundle: EvidenceBundle, path: Path, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing evidence bundle: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--generated-at")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        source = EvidenceSource.model_validate_json(args.source.read_text(encoding="utf-8"))
        bundle = build_bundle(source, generated_at=args.generated_at)
        write_bundle(bundle, args.output, overwrite=args.overwrite)
    except Exception as exc:  # noqa: BLE001 - fail-closed CLI boundary
        print(f"[export-evidence] failed: {exc}", file=sys.stderr)
        return 1
    print(bundle.payloadSha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
