# SPDX-License-Identifier: MIT

"""Attachment byte caps are read from ``platform.attachments`` in assembly.yaml.

Without the descriptor fields the caps are the historical 5 MB / 10 MB / 25 MB.
A descriptor value replaces a cap on the next read. A malformed value keeps the
default instead of failing the upload path.
"""

from __future__ import annotations

import base64

import pytest

import kdcube_ai_app.apps.chat.sdk.config as sdk_config
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import ingest_user_attachments
from kdcube_ai_app.infra.service_hub import multimodality

MB = 1024 * 1024


def _descriptor(monkeypatch, values: dict[str, object]) -> list[str]:
    reads: list[str] = []

    def _get_plain(key, default=None):
        reads.append(key)
        prefix = "a:platform.attachments."
        if key.startswith(prefix) and key[len(prefix):] in values:
            return values[key[len(prefix):]]
        return default

    monkeypatch.setattr(sdk_config, "get_plain", _get_plain)
    return reads


def test_defaults_are_the_historical_caps(monkeypatch):
    reads = _descriptor(monkeypatch, {})

    assert multimodality.max_image_bytes() == 5 * MB
    assert multimodality.max_doc_bytes() == 10 * MB
    assert multimodality.max_message_bytes() == 25 * MB
    assert reads == [
        "a:platform.attachments.max_image_bytes",
        "a:platform.attachments.max_doc_bytes",
        "a:platform.attachments.max_message_bytes",
    ]


def test_descriptor_values_replace_the_caps(monkeypatch):
    _descriptor(monkeypatch, {
        "max_image_bytes": 10 * MB,
        "max_doc_bytes": str(20 * MB),
        "max_message_bytes": 50 * MB,
    })

    assert multimodality.max_image_bytes() == 10 * MB
    assert multimodality.max_doc_bytes() == 20 * MB
    assert multimodality.max_message_bytes() == 50 * MB


@pytest.mark.parametrize("bad", ["ten megabytes", 0, -1, True, [1]])
def test_a_malformed_value_keeps_the_default(monkeypatch, bad):
    _descriptor(monkeypatch, {"max_image_bytes": bad})

    assert multimodality.max_image_bytes() == 5 * MB


def test_a_process_without_settings_uses_the_defaults(monkeypatch):
    def _no_settings(*_a, **_k):
        raise RuntimeError("no platform settings in this process")

    monkeypatch.setattr(sdk_config, "get_plain", _no_settings)

    assert multimodality.max_message_bytes() == 25 * MB


@pytest.mark.asyncio
async def test_ingest_enforces_the_descriptor_image_cap(monkeypatch):
    """A consumer of the caps sees the descriptor value, not the module default."""
    _descriptor(monkeypatch, {"max_image_bytes": 16})
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode("ascii")

    result = await ingest_user_attachments(
        attachments=[{"filename": "a.png", "mime": "image/png", "base64": png}],
        store=None,
    )

    assert result[0]["error"] == "size_limit: 72 > 16"
