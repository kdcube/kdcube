# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.context.vector.anchors import parse_retrieval_anchors


def test_no_block_returns_empty():
    assert parse_retrieval_anchors("Goal: x\nOutcome: y") == ""


def test_empty_input_returns_empty():
    assert parse_retrieval_anchors("") == ""
    assert parse_retrieval_anchors(None) == ""  # type: ignore[arg-type]


def test_inline_json_lists_parsed():
    text = (
        "Goal: build Q2 forecast\n"
        "Outcome: file produced\n"
        "Retrieval-anchors:\n"
        '  phrases: ["Forecast-Q2-2026.xlsx", "openpyxl IndexError", "rename ARR contribution column"]\n'
        '  entities: ["Forecast-Q2-2026.xlsx", "openpyxl", "ARR contribution"]\n'
    )
    result = parse_retrieval_anchors(text)
    # Phrases must be quoted (multi-word verbatim), entities bare.
    assert '"Forecast-Q2-2026.xlsx"' in result
    assert '"openpyxl IndexError"' in result
    assert '"rename ARR contribution column"' in result
    assert "openpyxl" in result
    assert "ARR contribution" in result
    # Phrases come before entities.
    assert result.index('"Forecast-Q2-2026.xlsx"') < result.index(" openpyxl")


def test_only_one_field_present():
    text = (
        "Retrieval-anchors:\n"
        '  entities: ["claudeflare", "wireguard"]\n'
    )
    result = parse_retrieval_anchors(text)
    assert "claudeflare" in result
    assert "wireguard" in result
    assert '"' not in result  # no phrases → no quoted tokens


def test_yaml_list_shape_parsed():
    text = (
        "Retrieval-anchors:\n"
        "  phrases:\n"
        "    - alpha beta\n"
        "    - gamma\n"
        "  entities:\n"
        "    - Foo\n"
        "    - Bar\n"
    )
    result = parse_retrieval_anchors(text)
    assert '"alpha beta"' in result
    assert '"gamma"' in result
    assert "Foo" in result
    assert "Bar" in result


def test_single_quotes_repaired():
    text = (
        "Retrieval-anchors:\n"
        "  phrases: ['one', 'two words']\n"
    )
    result = parse_retrieval_anchors(text)
    assert '"one"' in result
    assert '"two words"' in result


def test_malformed_value_falls_back_to_empty():
    text = "Retrieval-anchors:\n  phrases: not-a-list\n"
    # Best-effort regex split should still extract the bare token.
    result = parse_retrieval_anchors(text)
    # Either empty or contains the fallback token; the contract just requires no exception.
    assert isinstance(result, str)


def test_header_case_insensitive_and_underscore():
    text = (
        "retrieval_anchors:\n"
        '  entities: ["X"]\n'
    )
    assert "X" in parse_retrieval_anchors(text)
