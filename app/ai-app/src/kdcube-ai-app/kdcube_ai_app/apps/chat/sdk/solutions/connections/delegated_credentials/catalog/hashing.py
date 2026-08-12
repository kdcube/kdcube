# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Canonical content hash for the delegated-service catalog body."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def connections_content_hash(connections: Mapping[str, Any]) -> str:
    """Full lowercase SHA-256 of the canonical JSON encoding of ``connections``.

    The input must already be JSON-compatible parsed effective props. An
    unsupported value raises instead of being coerced to a string, so a
    publication error cannot silently become a different hash.

    Map-key order and source formatting do not affect the result; list order
    and every actual value do.
    """
    canonical = json.dumps(
        connections,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["connections_content_hash"]
