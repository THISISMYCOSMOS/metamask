"""Canonical JSON and SHA-256 helpers shared by backend and Core.

Every hash in this system is SHA-256 over the UTF-8 bytes of this exact
serialization: recursively sorted keys, compact separators, no ASCII escaping,
no NaN/Infinity.  Backend and Core import this module rather than reimplementing
it, so a proposal hash means the same thing on both sides of the boundary.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize JSON values in the one representation used for all bindings."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return "0x" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
