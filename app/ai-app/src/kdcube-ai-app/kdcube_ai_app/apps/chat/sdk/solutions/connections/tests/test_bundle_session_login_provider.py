# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login import (
    resolve_bundle_session_login_provider,
)


class _EntryPoint:
    bundle_props = {
        "authority_registry": {
            "authorities": {
                "kdcube.platform": {
                    "platform": True,
                    "providers": {
                        "versatile_google_session": {
                            "type": "bundle_session_login",
                            "entrypoints": {
                                "login": {
                                    "bundle_id": "versatile@2026-03-31-13-36",
                                    "route": "public",
                                    "operation": "platform_login",
                                },
                                "session_issue": {
                                    "bundle_id": "versatile@2026-03-31-13-36",
                                    "route": "public",
                                    "operation": "auth_google_session",
                                },
                            },
                        }
                    },
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_bundle_session_login_provider_accepts_registered_entrypoint_without_host():
    result = await resolve_bundle_session_login_provider(
        _EntryPoint(),
        bundle_id="versatile@2026-03-31-13-36",
        operation="platform_login",
    )

    assert result["ok"] is True
    assert result["authority_id"] == "kdcube.platform"
    assert result["provider_id"] == "versatile_google_session"
    assert result["entrypoints"]["login"]["operation"] == "platform_login"
