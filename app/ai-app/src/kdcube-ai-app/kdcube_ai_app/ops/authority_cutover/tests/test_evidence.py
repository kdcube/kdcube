from __future__ import annotations

import pytest

from kdcube_ai_app.ops.authority_cutover.evidence import (
    require_reviewed_reset,
    reviewed_reset_prerequisites,
)


def test_reviewed_reset_prerequisites_are_explicit_and_secret_safe() -> None:
    prerequisites = reviewed_reset_prerequisites()

    require_reviewed_reset(prerequisites)
    assert prerequisites == {
        "activation": {
            "kind": "reset",
            "reconstructable_authority": "discarded",
            "resident_agent_credentials": "preserved",
        },
        "resident_card_bindings": {
            "verification": "durable-card-identity-revision-expiry",
        },
    }


def test_reviewed_reset_requires_card_binding_verification() -> None:
    prerequisites = reviewed_reset_prerequisites()
    prerequisites.pop("resident_card_bindings")
    with pytest.raises(ValueError, match="resident Card verification"):
        require_reviewed_reset(prerequisites)
