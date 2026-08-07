# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The productivity surface is the reference PURE-MCP door: every tool
declares its connected-account requirements (ToolClaimPolicy shape) and the
surface builds with exactly the declared tool roster."""

from __future__ import annotations

from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    load_dynamic_module_for_path,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

SHEETS_READ_TOOLS = {
    "productivity_sheets_search",
    "productivity_sheets_describe",
    "productivity_sheets_read",
}
SHEETS_WRITE_TOOLS = {
    "productivity_sheets_update_values",
    "productivity_sheets_append_rows",
    "productivity_sheets_clear_values",
    "productivity_sheets_create_spreadsheet",
    "productivity_sheets_add_tab",
    "productivity_sheets_update_tab",
    "productivity_sheets_delete_tab",
    "productivity_sheets_format_range",
}
DOCS_READ_TOOLS = {
    "productivity_docs_search",
    "productivity_docs_get",
    "productivity_docs_export",
    "productivity_docs_list_comments",
    "productivity_docs_get_comment",
    # flexible / tab-aware read (evaluation surface, side by side with typed)
    "productivity_docs_get_structure",
    "productivity_docs_list_tabs",
}
DOCS_WRITE_TOOLS = {
    "productivity_docs_create",
    "productivity_docs_copy",
    "productivity_docs_insert_text",
    "productivity_docs_append_text",
    "productivity_docs_replace_text",
    "productivity_docs_apply_text_style",
    "productivity_docs_insert_page_break",
    "productivity_docs_embed_image",
    "productivity_docs_import",
    # flexible native batch edit (evaluation surface, side by side with typed)
    "productivity_docs_batch_edit",
}
DOCS_COMMENT_TOOLS = {
    "productivity_docs_create_comment",
    "productivity_docs_reply_comment",
    "productivity_docs_resolve_comment",
    "productivity_docs_delete_comment",
}
LINKEDIN_READ_TOOLS = {
    "productivity_linkedin_profile",
}
# Reads KDCube's own connection records, calls no LinkedIn API, and takes no
# account_id — so it declares no connected-account claim.
LINKEDIN_DISCOVERY_TOOLS = {
    "productivity_linkedin_accounts",
}
LINKEDIN_WRITE_TOOLS = {
    "productivity_linkedin_post",
    "productivity_linkedin_comment",
    "productivity_linkedin_post_image",
}
ALL_TOOLS = {
    "productivity_slack_search",
    "productivity_mail_search",
    "productivity_mail_get",
    *SHEETS_READ_TOOLS,
    *SHEETS_WRITE_TOOLS,
    *DOCS_READ_TOOLS,
    *DOCS_WRITE_TOOLS,
    *DOCS_COMMENT_TOOLS,
    *LINKEDIN_READ_TOOLS,
    *LINKEDIN_DISCOVERY_TOOLS,
    *LINKEDIN_WRITE_TOOLS,
}


def _surface_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "surfaces" / "mcp" / "productivity.py"
    )
    return module


def _docs_service_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "productivity" / "google_docs.py"
    )
    return module


def test_every_tool_declares_provider_claims():
    module = _surface_module()
    declared = {name: config for name, config in module.PRODUCTIVITY_TOOLS.items()}
    assert set(declared) == ALL_TOOLS
    expectations = {
        "productivity_slack_search": ("slack", ["slack:search"]),
        "productivity_mail_search": ("google", ["gmail:read"]),
        "productivity_mail_get": ("google", ["gmail:read"]),
        **{name: ("google", ["sheets:read"]) for name in SHEETS_READ_TOOLS},
        **{
            name: ("google", ["sheets:read", "sheets:write"])
            for name in SHEETS_WRITE_TOOLS
        },
        **{name: ("google", ["docs:read"]) for name in DOCS_READ_TOOLS},
        **{
            name: ("google", ["docs:read", "docs:write"])
            for name in DOCS_WRITE_TOOLS
        },
        **{
            name: ("google", ["docs:read", "docs:comment"])
            for name in DOCS_COMMENT_TOOLS
        },
        **{name: ("linkedin", ["linkedin:profile"]) for name in LINKEDIN_READ_TOOLS},
        # LinkedIn gates posts and comments on the same w_member_social scope.
        **{name: ("linkedin", ["linkedin:post"]) for name in LINKEDIN_WRITE_TOOLS},
    }
    for name, (provider_id, claims) in expectations.items():
        requirements = module.tool_requirements(name)
        assert requirements, f"{name} declares no requirements"
        assert requirements[0]["provider_id"] == provider_id
        assert requirements[0]["claims"] == claims
    # Account discovery must stay claim-free: resolving a connected-account
    # claim here returns account_required once two accounts are connected, and
    # the tool takes no account_id, so the denial could never be satisfied.
    for name in LINKEDIN_DISCOVERY_TOOLS:
        assert module.tool_requirements(name) == []


@pytest.mark.asyncio
async def test_signed_docs_export_download_reauthorizes_and_returns_file_bytes(
    monkeypatch,
):
    module = _docs_service_module()
    token_calls = []

    async def resolve_token(_entrypoint, **kwargs):
        token_calls.append(kwargs)
        return "provider-token", None

    class FakeDocsService:
        def __init__(self):
            self.calls = []

        async def _execute_with_access_token(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["operation"] == "get":
                return {
                    "ok": True,
                    "ret": {"document_id": "doc-1", "title": "Invoice 26_007"},
                }
            return {
                "ok": True,
                "ret": {
                    "document_id": "doc-1",
                    "format": "docx",
                    "content_base64": "ZG9jeC1ieXRlcw==",
                },
            }

    service = FakeDocsService()
    monkeypatch.setattr(module, "resolve_connected_account_access_token", resolve_token)
    monkeypatch.setattr(module, "GoogleDocsService", lambda: service)

    result = await module.fetch_google_docs_export(
        object(),
        user_id="user-1",
        tenant="demo",
        project="project",
        object_ref="docs:google:account-1:export:docx:doc-1",
    )

    assert result == {
        "ok": True,
        "data": b"docx-bytes",
        "filename": "Invoice 26_007.docx",
        "mime_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "status": 200,
    }
    assert token_calls[0]["account_id"] == "account-1"
    assert token_calls[0]["claim"] == "docs:read"
    assert [call["operation"] for call in service.calls] == ["get", "export"]
    assert all(call["access_token"] == "provider-token" for call in service.calls)


@pytest.mark.asyncio
async def test_surface_builds_with_declared_tool_roster():
    module = _surface_module()
    app = module.build_productivity_mcp_app(
        name="KDCube productivity",
        config_factory=lambda: {
            "connector_apps": {"slack": "slack-demo", "google": "gmail"}
        },
        tenant_factory=lambda: "t",
        project_factory=lambda: "p",
        request=None,
    )
    tools = {tool.name for tool in await app.list_tools()}
    assert tools == ALL_TOOLS

    schemas = {tool.name: tool.input_schema for tool in await app.list_tools()}
    assert schemas["productivity_sheets_search"]["properties"]["query"]["description"]
    assert schemas["productivity_sheets_read"]["properties"]["ranges"]["description"]
    assert schemas["productivity_sheets_update_values"]["properties"]["updates"][
        "description"
    ]
    assert schemas["productivity_sheets_format_range"]["properties"]["sheet_id"][
        "description"
    ]
    assert schemas["productivity_docs_get"]["properties"]["document_ref"]["description"]
    assert schemas["productivity_docs_copy"]["properties"]["title"]["description"]
    assert schemas["productivity_docs_replace_text"]["properties"]["replacements"][
        "description"
    ]
    assert schemas["productivity_docs_replace_text"]["properties"]["tab_ids"][
        "description"
    ]
    assert schemas["productivity_docs_replace_text"]["properties"]["all_tabs"][
        "description"
    ]
    assert schemas["productivity_docs_apply_text_style"]["properties"]["start_index"][
        "description"
    ]
    assert schemas["productivity_docs_apply_text_style"]["properties"]["tab_id"][
        "description"
    ]
    assert schemas["productivity_docs_batch_edit"]["properties"]["all_tabs"][
        "description"
    ]
    assert schemas["productivity_docs_reply_comment"]["properties"]["comment_id"][
        "description"
    ]
