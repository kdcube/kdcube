# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""
Resolution tests for resolve_effective_cron().

Verifies all precedence and disable rules from the spec.
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import resolve_effective_cron


def test_inline_only():
    assert resolve_effective_cron("*/5 * * * *", None, {}) == "*/5 * * * *"


def test_expr_config_found_in_props():
    props = {"routines": {"job": {"cron": "0 * * * *"}}}
    result = resolve_effective_cron(None, "routines.job.cron", props)
    assert result == "0 * * * *"


def test_expr_config_wins_over_cron_expression():
    props = {"routines": {"job": {"cron": "0 * * * *"}}}
    result = resolve_effective_cron("*/5 * * * *", "routines.job.cron", props)
    assert result == "0 * * * *"


def test_expr_config_missing_from_props_returns_none():
    result = resolve_effective_cron("*/5 * * * *", "routines.job.cron", {})
    assert result is None


def test_expr_config_disable_returns_none():
    props = {"routines": {"job": {"cron": "disable"}}}
    result = resolve_effective_cron(None, "routines.job.cron", props)
    assert result is None


def test_expr_config_disable_case_insensitive():
    for value in ("DISABLE", "Disable", "DISABLE  ", "  disable  "):
        props = {"routines": {"job": {"cron": value}}}
        result = resolve_effective_cron(None, "routines.job.cron", props)
        assert result is None, f"expected None for {value!r}"


def test_expr_config_blank_returns_none():
    for blank in ("", "   "):
        props = {"routines": {"job": {"cron": blank}}}
        result = resolve_effective_cron(None, "routines.job.cron", props)
        assert result is None, f"expected None for {blank!r}"


def test_expr_config_non_string_returns_none():
    props = {"routines": {"job": {"cron": 42}}}
    result = resolve_effective_cron(None, "routines.job.cron", props)
    assert result is None


def test_neither_provided_returns_none():
    assert resolve_effective_cron(None, None, {}) is None


def test_expr_config_deep_path():
    props = {"a": {"b": {"c": {"d": "*/10 * * * *"}}}}
    assert resolve_effective_cron(None, "a.b.c.d", props) == "*/10 * * * *"


def test_expr_config_path_partially_missing_returns_none():
    props = {"a": {"b": {}}}
    assert resolve_effective_cron(None, "a.b.c.d", props) is None


def test_expr_config_no_fallback_to_cron_expression_when_disabled():
    """If expr_config resolves to 'disable', cron_expression must NOT be used."""
    props = {"routines": {"cron": "disable"}}
    result = resolve_effective_cron("*/5 * * * *", "routines.cron", props)
    assert result is None


def test_expr_config_no_fallback_to_cron_expression_when_missing():
    """If expr_config path is missing in props, cron_expression must NOT be used."""
    result = resolve_effective_cron("*/5 * * * *", "routines.cron", {})
    assert result is None
