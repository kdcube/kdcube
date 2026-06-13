# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, TypeVar


TOOL_TRAITS_ATTR = "__kdcube_tool_traits__"
STRATEGY_TRAIT = "strategy"
UNKNOWN_STRATEGY = "unknown"
VALID_STRATEGIES = frozenset({"exploration", "exploitation", "neutral", UNKNOWN_STRATEGY})
STRATEGY_COMPATIBILITY_MATRIX: dict[str, dict[str, bool]] = {
    "exploration": {
        "exploration": True,
        "exploitation": False,
        "neutral": True,
        UNKNOWN_STRATEGY: False,
    },
    "exploitation": {
        "exploration": False,
        "exploitation": True,
        "neutral": True,
        UNKNOWN_STRATEGY: False,
    },
    "neutral": {
        "exploration": True,
        "exploitation": True,
        "neutral": True,
        UNKNOWN_STRATEGY: False,
    },
    UNKNOWN_STRATEGY: {
        "exploration": False,
        "exploitation": False,
        "neutral": False,
        UNKNOWN_STRATEGY: False,
    },
}

F = TypeVar("F", bound=Callable[..., Any])


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _unique_strings(value: Any, *, lower: bool = False) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item or "").strip()
        if lower:
            text = text.lower()
        if text and text not in out:
            out.append(text)
    return out


def normalize_strategy(value: Any) -> list[str]:
    strategies = [item for item in _unique_strings(value, lower=True) if item in VALID_STRATEGIES]
    concrete = [item for item in strategies if item != UNKNOWN_STRATEGY]
    if concrete:
        return concrete
    return [UNKNOWN_STRATEGY]


def normalize_tool_traits(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, raw in value.items():
        trait_name = str(key or "").strip()
        if not trait_name:
            continue
        if trait_name == STRATEGY_TRAIT:
            out[trait_name] = normalize_strategy(raw)
        else:
            out[trait_name] = raw
    return out


def merge_tool_traits(*sources: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        traits = normalize_tool_traits(source)
        for key, value in traits.items():
            if key == STRATEGY_TRAIT and key in merged:
                existing = normalize_strategy(merged.get(key))
                incoming = normalize_strategy(value)
                concrete = [item for item in existing + incoming if item != UNKNOWN_STRATEGY]
                if concrete:
                    merged[key] = list(dict.fromkeys(concrete))
                else:
                    merged[key] = [UNKNOWN_STRATEGY]
            else:
                merged[key] = value
    return normalize_tool_traits(merged)


def configured_tool_traits(base: Any, override: Any) -> dict[str, Any]:
    merged = normalize_tool_traits(base)
    override_traits = normalize_tool_traits(override)
    for key, value in override_traits.items():
        merged[key] = value
    return normalize_tool_traits(merged)


def tool_trait(**traits: Any) -> Callable[[F], F]:
    normalized = normalize_tool_traits(traits)

    def _decorator(fn: F) -> F:
        existing = getattr(fn, TOOL_TRAITS_ATTR, {})
        setattr(fn, TOOL_TRAITS_ATTR, merge_tool_traits(existing, normalized))
        return fn

    return _decorator


def get_tool_traits(fn: Any) -> dict[str, Any]:
    return normalize_tool_traits(getattr(fn, TOOL_TRAITS_ATTR, {}))


def strategy_values(traits: Any) -> set[str]:
    normalized = normalize_tool_traits(traits)
    return set(normalize_strategy(normalized.get(STRATEGY_TRAIT)))


def strategies_compatible(first: Any, second: Any) -> bool:
    a = strategy_values(first)
    b = strategy_values(second)
    if not a or not b:
        return False
    return any(
        STRATEGY_COMPATIBILITY_MATRIX.get(first_strategy, {}).get(second_strategy, False)
        for first_strategy in a
        for second_strategy in b
    )


__all__ = [
    "STRATEGY_TRAIT",
    "STRATEGY_COMPATIBILITY_MATRIX",
    "TOOL_TRAITS_ATTR",
    "UNKNOWN_STRATEGY",
    "VALID_STRATEGIES",
    "configured_tool_traits",
    "get_tool_traits",
    "merge_tool_traits",
    "normalize_strategy",
    "normalize_tool_traits",
    "strategies_compatible",
    "strategy_values",
    "tool_trait",
]
