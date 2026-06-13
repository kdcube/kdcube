from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import (
    STRATEGY_COMPATIBILITY_MATRIX,
    strategies_compatible,
)


EXPECTED_STRATEGY_MATRIX = {
    "exploration": {
        "exploration": True,
        "exploitation": False,
        "neutral": True,
        "unknown": False,
    },
    "exploitation": {
        "exploration": False,
        "exploitation": True,
        "neutral": True,
        "unknown": False,
    },
    "neutral": {
        "exploration": True,
        "exploitation": True,
        "neutral": True,
        "unknown": False,
    },
    "unknown": {
        "exploration": False,
        "exploitation": False,
        "neutral": False,
        "unknown": False,
    },
}


def test_strategy_compatibility_matrix_matches_react_contract() -> None:
    assert STRATEGY_COMPATIBILITY_MATRIX == EXPECTED_STRATEGY_MATRIX


@pytest.mark.parametrize("first,second", [(a, b) for a in EXPECTED_STRATEGY_MATRIX for b in EXPECTED_STRATEGY_MATRIX[a]])
def test_strategies_compatible_uses_matrix(first: str, second: str) -> None:
    assert strategies_compatible(
        {"strategy": [first]},
        {"strategy": [second]},
    ) is EXPECTED_STRATEGY_MATRIX[first][second]
