# SPDX-License-Identifier: MIT

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from starlette.requests import Request

from kdcube_ai_app.apps.chat.ingress.control_plane import config as frontend_config


class _Settings:
    TENANT = "tenant-one"
    PROJECT = "project-one"
    AUTH = SimpleNamespace(
        COGNITO_REGION="",
        COGNITO_USER_POOL_ID="",
        COGNITO_APP_CLIENT_ID="",
        AUTH_TOKEN_COOKIE_NAME="__Secure-LATC",
        ID_TOKEN_COOKIE_NAME="__Secure-LITC",
    )

    def plain(self, path: str, default=None):
        values = {
            "proxy.route_prefix": "/platform",
            "company": "KDCube",
            "auth.turnstile_development_token": "",
        }
        return values.get(path, default)

    def connection_hub_platform_auth_config(self):
        return {
            "auth_provider": "bundle",
            "provider": {
                "id": "workspace_google_session",
                "provider_id": "workspace_google_session",
                "type": "bundle",
                "entrypoints": {
                    "login": {
                        "bundle_id": "workspace@1-0",
                        "route": "public",
                        "operation": "platform_login",
                    },
                },
            },
        }


class _CognitoSettings(_Settings):
    AUTH = SimpleNamespace(
        COGNITO_REGION="eu-west-1",
        COGNITO_USER_POOL_ID="eu-west-1_DEMO",
        COGNITO_APP_CLIENT_ID="demo-client",
        AUTH_TOKEN_COOKIE_NAME="__Secure-LATC",
        ID_TOKEN_COOKIE_NAME="__Secure-LITC",
    )

    def connection_hub_platform_auth_config(self):
        return {
            "auth_provider": "cognito",
            "region": "eu-west-1",
            "user_pool_id": "eu-west-1_DEMO",
            "app_client_id": "demo-client",
            "hosted_ui_domain": "https://auth.demo.example.test",
        }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/cp-frontend-config",
            "raw_path": b"/api/cp-frontend-config",
            "query_string": b"",
            "headers": [
                (b"host", b"ingress.internal:8010"),
                (b"x-forwarded-host", b"runtime.example.test"),
                (b"x-forwarded-proto", b"https"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("ingress.internal", 8010),
        }
    )


def test_frontend_config_is_public_descriptor_resolution_without_runtime_services(monkeypatch):
    assembly = {
        "company": "KDCube",
        "context": {"tenant": "tenant-one", "project": "project-one"},
        "auth": {
            "type": "bundle",
            "connection_hub": {
                "bundle_id": "connection-hub@1-0",
                "authority_id": "kdcube.platform",
                "provider_id": "workspace_google_session",
                "entrypoint": "login",
            },
        },
        "proxy": {"route_prefix": "/platform"},
    }
    monkeypatch.setattr(frontend_config, "get_settings", lambda: _Settings())
    monkeypatch.setattr(frontend_config, "_load_assembly_descriptor", lambda: assembly)

    # A sync route is dispatched by FastAPI's thread pool. A cold descriptor
    # read therefore cannot block the ingress event loop.
    assert inspect.iscoroutinefunction(frontend_config.cp_frontend_config) is False

    response = frontend_config.cp_frontend_config(_request())
    payload = json.loads(response.body)

    assert payload["tenant"] == "tenant-one"
    assert payload["project"] == "project-one"
    assert payload["auth"]["authType"] == "bundle"
    assert payload["auth"]["loginUrl"] == (
        "https://runtime.example.test/api/integrations/bundles/"
        "tenant-one/project-one/workspace%401-0/public/platform_login"
    )
    assert response.headers["cache-control"] == "no-store, no-cache"


def test_frontend_config_keeps_cognito_login_and_logout_on_selected_provider(monkeypatch):
    assembly = {
        "context": {"tenant": "tenant-one", "project": "project-one"},
        "auth": {
            "type": "bundle",
            "connection_hub": {
                "bundle_id": "connection-hub@1-0",
                "authority_id": "kdcube.platform",
                "provider_id": "cognito_demo",
            },
        },
        "proxy": {"route_prefix": "/platform"},
    }
    monkeypatch.setattr(frontend_config, "get_settings", lambda: _CognitoSettings())
    monkeypatch.setattr(frontend_config, "_load_assembly_descriptor", lambda: assembly)

    response = frontend_config.cp_frontend_config(_request())
    payload = json.loads(response.body)

    assert payload["auth"]["authType"] == "cognito"
    assert payload["auth"]["oidcConfig"] == {
        "authority": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_DEMO",
        "client_id": "demo-client",
        "end_session_endpoint": "https://auth.demo.example.test/logout",
    }
