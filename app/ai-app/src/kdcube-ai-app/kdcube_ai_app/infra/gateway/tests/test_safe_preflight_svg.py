"""An SVG is accepted as a picture and refused as a document.

Problem Board renders a diagram attached to a report, and until this existed no
diagram could ever reach that renderer: the preflight refused image/svg+xml as
an unsupported type, so the render path was unreachable and the refusal reached
the sender only as a 422 after five silent retries.

Allowing the type outright was not the fix. An SVG can run script, animate, and
fetch while it is being drawn, so a surface that renders an arbitrary one has
become an execution surface. Each case below is a construct that turns a
picture back into a page.
"""
from __future__ import annotations

import asyncio

from kdcube_ai_app.infra.gateway.safe_preflight import PreflightConfig, preflight_async

DRAWING = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20">'
    b'<rect width="40" height="20" fill="#fff"/>'
    b'<text x="4" y="14" font-size="9">ok</text>'
    b"</svg>"
)


def _check(payload: bytes, **config):
    return asyncio.run(
        preflight_async(payload, "diagram.svg", "image/svg+xml", PreflightConfig(av_scan=False, **config))
    )


def test_a_plain_drawing_is_allowed():
    result = _check(DRAWING)
    assert result.allowed, list(result.reasons)
    assert result.meta["mime"] == "image/svg+xml"


def test_script_is_refused():
    result = _check(DRAWING.replace(b"<rect", b"<script>fetch('http://x/')</script><rect", 1))
    assert not result.allowed
    assert any("<script>" in reason for reason in result.reasons)


def test_an_event_attribute_is_refused():
    result = _check(DRAWING.replace(b"<svg ", b'<svg onload="alert(1)" ', 1))
    assert not result.allowed
    assert any("onload" in reason for reason in result.reasons)


def test_foreign_object_is_refused():
    result = _check(DRAWING.replace(b"<rect", b"<foreignObject/><rect", 1))
    assert not result.allowed
    assert any("foreignobject" in reason.lower() for reason in result.reasons)


def test_an_external_reference_is_refused():
    """Rendering would become a request from the reader's browser.

    That leaks who opened the report and when, to whoever hosts the URL, which
    is a disclosure the reader never agreed to and cannot see.
    """

    result = _check(DRAWING.replace(b"<rect", b'<image href="https://tracker.example/p.png"/><rect', 1))
    assert not result.allowed
    assert any("external" in reason for reason in result.reasons)


def test_a_script_uri_is_refused():
    result = _check(DRAWING.replace(b"<rect", b'<a href="javascript:alert(1)"/><rect', 1))
    assert not result.allowed
    assert any("script URI" in reason for reason in result.reasons)


def test_a_declared_entity_is_refused():
    """The shape of an XXE read, refused before the parser is asked to decide."""

    result = _check(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + DRAWING)
    assert not result.allowed
    assert any("DOCTYPE" in reason or "entity" in reason for reason in result.reasons)


def test_malformed_xml_is_refused_rather_than_guessed_at():
    result = _check(b"<svg><rect")
    assert not result.allowed


def test_policy_can_turn_svg_off_entirely():
    result = _check(DRAWING, allow_svg=False)
    assert not result.allowed
    assert any("policy" in reason for reason in result.reasons)


def test_an_oversized_svg_is_refused():
    result = _check(DRAWING + b"<!-- " + b"x" * 4096 + b" -->", svg_max_bytes=512)
    assert not result.allowed
    assert any("too large" in reason for reason in result.reasons)
