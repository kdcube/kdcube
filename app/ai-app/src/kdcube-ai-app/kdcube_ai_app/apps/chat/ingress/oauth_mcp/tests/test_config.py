# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.clients import dcr_redirect_allowed, get_client
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.config import oauth_mcp_config
from kdcube_ai_app.apps.chat.sdk import config as sdk_config


def test_oauth_mcp_routes_are_descriptor_gated():
    app = FastAPI()
    mount_oauth_mcp(app)

    response = TestClient(app).get("/.well-known/oauth-authorization-server")

    assert response.status_code == 404


def test_oauth_mcp_reads_assembly_descriptor(tmp_path, monkeypatch):
    for key in ("TENANT_ID", "PROJECT_ID", "AUTH_TOKEN_COOKIE_NAME"):
        monkeypatch.delenv(key, raising=False)
    assembly = tmp_path / "assembly.yaml"
    assembly.write_text(
        """
context:
  tenant: "tenant-a"
  project: "project-b"
auth:
  auth_token_cookie_name: "__Secure-KDCUBE"
  oauth_mcp:
    enabled: true
    issuer: "https://mcp.example.test"
    public_clients:
      - client_id: "local-client"
        redirect_uris:
          - "http://localhost/callback"
    dynamic_client_registration:
      allowed_redirect_uris:
        - "https://allowed.example.test/callback"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly))
    sdk_config.get_settings.cache_clear()
    try:
        app = FastAPI()
        mount_oauth_mcp(app)

        cfg = oauth_mcp_config(app)
        assert cfg.enabled is True
        assert cfg.issuer == "https://mcp.example.test"
        assert cfg.tenant == "tenant-a"
        assert cfg.project == "project-b"
        assert cfg.auth_cookie_name == "__Secure-KDCUBE"
        assert cfg.brand == "KDCube"  # default when auth.oauth_mcp.brand is unset

        assert get_client("local-client", app) is not None
        assert get_client("claude", app) is None
        assert dcr_redirect_allowed("https://allowed.example.test/callback", app)
        assert not dcr_redirect_allowed("https://claude.ai/api/mcp/auth_callback", app)

        response = TestClient(app).get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        assert response.json()["issuer"] == "https://mcp.example.test"
    finally:
        sdk_config.get_settings.cache_clear()


def test_oauth_mcp_brand_defaults_to_kdcube():
    app = FastAPI()
    app.state.oauth_mcp_config = {"enabled": True}
    assert oauth_mcp_config(app).brand == "KDCube"


def test_oauth_mcp_brand_from_descriptor_is_reflected():
    app = FastAPI()
    app.state.oauth_mcp_config = {"enabled": True, "brand": "Acme AI"}
    assert oauth_mcp_config(app).brand == "Acme AI"
