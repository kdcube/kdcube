# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationCall,
    bind_bundle_operation_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections import IdentityLinksClient, request_origin


class _Entrypoint:
    def __init__(self, props: dict | None = None) -> None:
        self.bundle_props = props or {}

    def bundle_prop(self, path: str, default=None):
        current = self.bundle_props
        for part in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(part)
        return default if current is None else current


@pytest.mark.asyncio
async def test_identity_links_client_calls_configured_connection_hub_public_operation():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "challenge": {"challenge_id": "ch_1"}}

    entrypoint = _Entrypoint({"integrations": {"connection_hub": {"bundle_id": "connection-hub@test"}}})
    with bind_bundle_operation_caller(_caller):
        result = await IdentityLinksClient(entrypoint).telegram_link_start(
            telegram_init_data="tg-init",
            public_origin="https://example.test",
        )

    assert result["ok"] is True
    assert calls == [
        BundleOperationCall(
            bundle_id="connection-hub@test",
            operation="telegram_identity_link_start",
            data={"telegram_init_data": "tg-init", "request_origin": "https://example.test"},
            route="public",
        )
    ]


@pytest.mark.asyncio
async def test_identity_links_client_complete_sends_challenge_id():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "link": {"provider_subject": "42"}}

    with bind_bundle_operation_caller(_caller):
        result = await IdentityLinksClient(_Entrypoint()).telegram_link_complete(
            challenge_id="abc",
            telegram_init_data="tg-init",
        )

    assert result["link"]["provider_subject"] == "42"
    assert calls[0].bundle_id == "connection-hub@1-0"
    assert calls[0].operation == "telegram_identity_link_complete"
    assert calls[0].data["challenge_id"] == "abc"


def test_request_origin_prefers_forwarded_headers():
    class _Request:
        headers = {"x-forwarded-host": "public.example", "x-forwarded-proto": "https"}

    assert request_origin(_Request()) == "https://public.example"
