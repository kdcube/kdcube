from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_adapter import MCPServerSpec, PythonSDKMCPAdapter


def test_auth_headers_supports_secret_key(monkeypatch):
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_secret",
        lambda key, default=None: "secret-token" if key == "bundles.react.mcp@2026-03-09.secrets.docs.token" else default,
    )
    adapter = PythonSDKMCPAdapter(
        MCPServerSpec(
            server_id="docs",
            display_name="docs",
            transport="http",
            endpoint="https://mcp.example.com",
            auth_profile={
                "type": "bearer",
                "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token",
            },
        )
    )
    assert adapter._auth_headers() == {"Authorization": "Bearer secret-token"}
