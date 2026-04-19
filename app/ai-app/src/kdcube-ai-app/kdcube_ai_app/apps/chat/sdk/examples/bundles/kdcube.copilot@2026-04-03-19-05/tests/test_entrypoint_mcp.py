from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_package(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py",
        submodule_search_locations=[str(path)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_entrypoint_module():
    root = _bundle_root()
    package_name = "copilot_bundle_entrypoint_testpkg"
    _ensure_package(package_name, root)
    module_name = f"{package_name}.entrypoint"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "entrypoint.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _request(*, header_name: str, header_value: str | None) -> Request:
    headers = []
    if header_value is not None:
        headers.append((header_name.lower().encode("utf-8"), header_value.encode("utf-8")))

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations/bundles/demo/demo/kdcube.copilot/mcp/doc_reader",
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        },
        receive=_receive,
    )


def test_doc_reader_mcp_auth_uses_bundle_prop_and_bundle_secret(monkeypatch):
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow.bundle_prop = lambda key, default=None: (
        "X-Test-MCP-Token" if key == "mcp.doc_reader.auth.header_name" else default
    )
    monkeypatch.setattr(mod, "get_secret", lambda key: "shared-token" if key == "b:mcp.doc_reader.auth.shared_token" else None)

    workflow._require_doc_reader_mcp_auth(
        _request(header_name="X-Test-MCP-Token", header_value="shared-token")
    )


def test_doc_reader_mcp_auth_rejects_invalid_header(monkeypatch):
    mod = _load_entrypoint_module()
    workflow = object.__new__(mod.ReactWorkflow)
    workflow.bundle_prop = lambda key, default=None: (
        "X-Test-MCP-Token" if key == "mcp.doc_reader.auth.header_name" else default
    )
    monkeypatch.setattr(mod, "get_secret", lambda key: "shared-token" if key == "b:mcp.doc_reader.auth.shared_token" else None)

    with pytest.raises(HTTPException) as exc:
        workflow._require_doc_reader_mcp_auth(
            _request(header_name="X-Test-MCP-Token", header_value="wrong-token")
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid X-Test-MCP-Token"
