#!/usr/bin/env python3
"""Run and record the fixed RQ2 benchmark through Gemini without fixture fallback."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from core.canonical import canonical_sha256
from research.evaluate_compiler import evaluate
from research.gemini_invariant_compiler import (
    CompilerUnavailableError,
    GeminiConfig,
    GeminiInvariantCompiler,
    ProviderResponseError,
    SYSTEM_PROMPT,
    _transport,
)
from research.models import BenchmarkDataset, PredictionRecord


ProviderTransport = Callable[[str, Mapping[str, str], Mapping[str, Any]], Mapping[str, Any]]


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_sha256(path: Path) -> str:
    return "0x" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _load_existing(path: Path) -> list[PredictionRecord]:
    if not path.exists():
        return []
    records: list[PredictionRecord] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(PredictionRecord.model_validate(json.loads(line)))
        except Exception as exc:  # noqa: BLE001 - report exact invalid line boundary
            raise ValueError(f"invalid existing prediction at line {number}") from exc
    if len({record.caseId for record in records}) != len(records):
        raise ValueError("existing prediction case ids must be unique")
    return records


def _safe_response_metadata(response: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    model_version = response.get("modelVersion")
    if isinstance(model_version, str):
        metadata["modelVersion"] = model_version
    usage = response.get("usageMetadata")
    if isinstance(usage, Mapping):
        metadata["usageMetadata"] = {
            key: value
            for key, value in usage.items()
            if isinstance(key, str) and isinstance(value, (int, float, str, bool))
        }
    return metadata


def run_benchmark(
    dataset: BenchmarkDataset,
    *,
    dataset_path: Path,
    output_path: Path,
    manifest_path: Path,
    config: GeminiConfig,
    transport: ProviderTransport = _transport,
    overwrite: bool = False,
    now: Callable[[], str] = _utc_now,
) -> tuple[dict[str, Any], bool]:
    dataset_sha256 = _file_sha256(dataset_path)
    system_prompt_sha256 = canonical_sha256(SYSTEM_PROMPT)
    prior_manifest: dict[str, Any] = {}
    if manifest_path.exists() and not overwrite:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("existing manifest must be a JSON object")
        prior_manifest = value
        expected_run = (dataset_sha256, config.model, system_prompt_sha256)
        actual_run = (
            prior_manifest.get("datasetSha256"),
            prior_manifest.get("requestedModel"),
            prior_manifest.get("systemPromptSha256"),
        )
        if actual_run != expected_run:
            raise ValueError("existing benchmark run metadata differs; use --overwrite for a new run")
    if overwrite:
        existing: list[PredictionRecord] = []
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8", newline="\n")
    else:
        existing = _load_existing(output_path)
    expected_ids = {case.caseId for case in dataset.cases}
    extra = sorted({record.caseId for record in existing} - expected_ids)
    if extra:
        raise ValueError(f"existing predictions contain unknown case ids: {extra}")
    existing_by_id = {record.caseId: record for record in existing}

    started_at = str(prior_manifest.get("startedAtUtc") or now())
    prior_case_results = prior_manifest.get("caseResults")
    case_results: dict[str, dict[str, Any]] = dict(prior_case_results) if isinstance(prior_case_results, dict) else {}
    for record in existing:
        case_results.setdefault(record.caseId, {"status": "completed", "resumed": True, "attempts": 1})
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "gemini-compiler-benchmark-run",
        "status": "running",
        "startedAtUtc": started_at,
        "updatedAtUtc": started_at,
        "datasetPath": dataset_path.as_posix(),
        "datasetSha256": dataset_sha256,
        "caseCount": len(dataset.cases),
        "requestedModel": config.model,
        "systemPromptSha256": system_prompt_sha256,
        "predictionPath": output_path.as_posix(),
        "caseResults": case_results,
        "completedCount": len(existing),
        "failedCount": 0,
        "metrics": None,
        "operationalMetrics": None,
        "failedCasePolicy": "Provider or strict-contract failures produce no proposal and are scored as non-approvable only in operationalMetrics.",
    }
    _write_json_atomic(manifest_path, manifest)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8", newline="\n") as stream:
        for case in dataset.cases:
            if case.caseId in existing_by_id:
                continue
            captured: dict[str, Any] = {}

            def recording_transport(url, headers, body):
                response = transport(url, headers, body)
                captured.update(_safe_response_metadata(response))
                return response

            compiler = GeminiInvariantCompiler(config, transport=recording_transport)
            request = compiler.build_request(case)
            result: dict[str, Any] = {
                "status": "running",
                "resumed": False,
                "attempts": int(case_results.get(case.caseId, {}).get("attempts", 0)) + 1,
                "promptSha256": canonical_sha256(request),
                "generationConfigSha256": canonical_sha256(request["generationConfig"]),
            }
            case_results[case.caseId] = result
            try:
                output = compiler.compile(case)
                record = PredictionRecord(caseId=case.caseId, output=output)
                stream.write(
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                stream.flush()
                existing_by_id[case.caseId] = record
                result.update({"status": "completed", **captured})
            except (CompilerUnavailableError, ProviderResponseError) as exc:
                result.update(
                    {
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "error": str(exc),
                        **captured,
                    }
                )
            manifest["updatedAtUtc"] = now()
            manifest["completedCount"] = len(existing_by_id)
            manifest["failedCount"] = sum(
                item.get("status") == "failed" for item in case_results.values()
            )
            _write_json_atomic(manifest_path, manifest)

    ordered_predictions = [existing_by_id[case.caseId] for case in dataset.cases if case.caseId in existing_by_id]
    complete = len(ordered_predictions) == len(dataset.cases)
    manifest["status"] = "completed" if complete else "incomplete"
    manifest["updatedAtUtc"] = now()
    fail_closed = {
        "supported": False,
        "invariant": None,
        "rationales": [],
        "assumptions": [],
        "unsupportedItems": ["Provider response failed strict local validation; no proposal was created."],
    }
    operational_predictions = [
        existing_by_id.get(case.caseId)
        or PredictionRecord(caseId=case.caseId, output=fail_closed)
        for case in dataset.cases
    ]
    manifest["operationalMetrics"] = evaluate(dataset, operational_predictions)
    if complete:
        manifest["metrics"] = evaluate(dataset, ordered_predictions)
    _write_json_atomic(manifest_path, manifest)
    return manifest, complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest or args.output.with_name(args.output.name + ".manifest.json")
    try:
        dataset = BenchmarkDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
        manifest, complete = run_benchmark(
            dataset,
            dataset_path=args.dataset,
            output_path=args.output,
            manifest_path=manifest_path,
            config=GeminiConfig.from_env(),
            overwrite=args.overwrite,
        )
    except Exception as exc:  # noqa: BLE001 - one fail-closed CLI boundary
        print(f"[gemini-benchmark] failed: {exc}")
        return 1
    print(json.dumps({key: manifest[key] for key in ("status", "caseCount", "completedCount", "failedCount")}, indent=2))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
