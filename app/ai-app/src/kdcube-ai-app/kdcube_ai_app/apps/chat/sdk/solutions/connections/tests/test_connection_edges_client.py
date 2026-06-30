# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationCall,
    bind_bundle_operation_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections import ConnectionEdgesClient, request_origin


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
async def test_connection_edges_client_calls_configured_connection_hub_public_operation():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "challenge": {"challenge_id": "ch_1"}}

    entrypoint = _Entrypoint({"connections": {"connection_hub": {"bundle_id": "connection-hub@test"}}})
    with bind_bundle_operation_caller(_caller):
        result = await ConnectionEdgesClient(entrypoint).telegram_edge_start(
            telegram_init_data="tg-init",
            public_origin="https://example.test",
        )

    assert result["ok"] is True
    assert calls == [
        BundleOperationCall(
            bundle_id="connection-hub@test",
            operation="telegram_connection_edge_start",
            data={"telegram_init_data": "tg-init", "request_origin": "https://example.test"},
            route="public",
        )
    ]


@pytest.mark.asyncio
async def test_connection_edges_client_complete_sends_challenge_id():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "edge": {"from": {"subject": "42"}}}

    with bind_bundle_operation_caller(_caller):
        result = await ConnectionEdgesClient(_Entrypoint()).telegram_edge_complete(
            challenge_id="abc",
            telegram_init_data="tg-init",
        )

    assert result["edge"]["from"]["subject"] == "42"
    assert calls[0].bundle_id == "connection-hub@1-0"
    assert calls[0].operation == "telegram_connection_edge_complete"
    assert calls[0].data["challenge_id"] == "abc"


@pytest.mark.asyncio
async def test_connection_edges_client_unwraps_operation_alias_result():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {
            "telegram_connection_edge_status": {
                "ok": True,
                "linked": True,
                "edge": {"to": {"user_id": "platform-user"}},
            }
        }

    with bind_bundle_operation_caller(_caller):
        result = await ConnectionEdgesClient(_Entrypoint()).telegram_edge_status(
            telegram_init_data="tg-init",
        )

    assert result["linked"] is True
    assert result["edge"]["to"]["user_id"] == "platform-user"
    assert calls[0].http_method == "GET"


def test_request_origin_prefers_forwarded_headers():
    class _Request:
        headers = {"x-forwarded-host": "public.example", "x-forwarded-proto": "https"}

    assert request_origin(_Request()) == "https://public.example"
