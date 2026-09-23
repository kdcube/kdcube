# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Secret-safe prerequisite evidence for an authority reset."""

from __future__ import annotations

from typing import Any


def reviewed_reset_prerequisites() -> dict[str, Any]:
    return {
        "activation": {
            "kind": "reset",
            "reconstructable_authority": "discarded",
            "resident_agent_credentials": "preserved",
        },
        "resident_card_bindings": {
            "verification": "durable-card-identity-revision-expiry",
        },
    }


def require_reviewed_reset(prerequisites: dict[str, Any]) -> None:
    activation = dict(prerequisites.get("activation") or {})
    if activation != {
        "kind": "reset",
        "reconstructable_authority": "discarded",
        "resident_agent_credentials": "preserved",
    }:
        raise ValueError("authority preview is not a reviewed reset")
    binding = dict(prerequisites.get("resident_card_bindings") or {})
    if binding != {
        "verification": "durable-card-identity-revision-expiry",
    }:
        raise ValueError("authority preview lacks resident Card verification")


__all__ = ["require_reviewed_reset", "reviewed_reset_prerequisites"]
