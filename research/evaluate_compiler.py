#!/usr/bin/env python3
"""Score compiler predictions against the fixed 50-100 case RQ2 benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from research.models import BenchmarkDataset, PredictionRecord


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(dataset: BenchmarkDataset, predictions: list[PredictionRecord]) -> dict[str, Any]:
    prediction_by_id = {item.caseId: item for item in predictions}
    if len(prediction_by_id) != len(predictions):
        raise ValueError("prediction case ids must be unique")
    expected_ids = {case.caseId for case in dataset.cases}
    if set(prediction_by_id) != expected_ids:
        missing = sorted(expected_ids - set(prediction_by_id))
        extra = sorted(set(prediction_by_id) - expected_ids)
        raise ValueError(f"predictions must cover the dataset exactly; missing={missing}, extra={extra}")

    tp = tn = fp = fn = exact = 0
    per_kind: dict[str, dict[str, int]] = {}
    mismatches: list[dict[str, str]] = []
    for case in dataset.cases:
        expected = case.expected
        predicted = prediction_by_id[case.caseId].output
        if expected.supported and predicted.supported:
            tp += 1
        elif not expected.supported and not predicted.supported:
            tn += 1
        elif not expected.supported and predicted.supported:
            fp += 1
        else:
            fn += 1

        expected_invariant = expected.invariant.model_dump(mode="json") if expected.invariant else None
        predicted_invariant = predicted.invariant.model_dump(mode="json") if predicted.invariant else None
        is_exact = expected.supported == predicted.supported and expected_invariant == predicted_invariant
        exact += int(is_exact)
        kind = expected.invariant.kind if expected.invariant else "unsupported"
        bucket = per_kind.setdefault(kind, {"total": 0, "exact": 0})
        bucket["total"] += 1
        bucket["exact"] += int(is_exact)
        if not is_exact:
            mismatches.append({"caseId": case.caseId, "expectedKind": kind, "predictedKind": predicted.invariant.kind if predicted.invariant else "unsupported"})

    total = len(dataset.cases)
    return {
        "schemaVersion": 1,
        "kind": "compiler-benchmark-report",
        "caseCount": total,
        "confusionMatrix": {"truePositive": tp, "trueNegative": tn, "falsePositive": fp, "falseNegative": fn},
        "supportClassification": {
            "accuracy": _ratio(tp + tn, total),
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "falsePositiveRate": _ratio(fp, fp + tn),
        },
        "exactInvariantAccuracy": _ratio(exact, total),
        "perExpectedKind": {kind: {**counts, "accuracy": _ratio(counts["exact"], counts["total"])} for kind, counts in sorted(per_kind.items())},
        "mismatches": mismatches,
    }


def load_predictions(path: Path) -> list[PredictionRecord]:
    records: list[PredictionRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(PredictionRecord.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid prediction at line {line_number}") from exc
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("predictions", type=Path)
    args = parser.parse_args()
    try:
        dataset = BenchmarkDataset.model_validate(json.loads(args.dataset.read_text(encoding="utf-8")))
        report = evaluate(dataset, load_predictions(args.predictions))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"[evaluate_compiler] invalid input: {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
