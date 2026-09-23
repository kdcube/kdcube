from __future__ import annotations

import json
from typing import Any

import pytest

import kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service as docs_named_service
from kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service import (
    ACTION_COPY,
    ACTION_CREATE_COMMENT,
    ACTION_DELETE_COMMENT,
    ACTION_EXPORT,
    ACTION_GET_COMMENT,
    ACTION_APPEND_TEXT,
    ACTION_REPLACE_TEXT,
    ACTION_REPLY_COMMENT,
    ACTION_SET_CELLS,
    ACTION_IMPORT,
    ACTION_LIST_FOLDER,
    ACTION_UPLOAD_FILE,
    DRIVE_READ_CLAIM,
    DRIVE_WRITE_CLAIM,
    DOCS_COMMENT_CLAIM,
    DOCS_CONNECTED_ACCOUNT_REQUIREMENTS,
    DOCS_DOCUMENT_KIND,
    DOCS_EXPORT_KIND,
    DOCS_GRANT_HINTS,
    DOCS_IMPORT_SOURCE_KIND,
    DOCS_READ_CLAIM,
    DOCS_SNAPSHOT_MEDIA_TYPE,
    DOCS_SNAPSHOT_SCHEMA,
    DOCS_WRITE_CLAIM,
    DocsNamedServiceProvider,
    document_export_ref,
    document_ref,
    document_source_ref,
    docs_named_service_spec,
    parse_docs_export_ref,
    parse_docs_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
    NamedServiceStreamResult,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    BLOCK_PRODUCE,
    EVENT_RESOLVE,
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_RESOLVE,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
)
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.send_to_user import (
    collect_file_deliveries,
)


def _ctx() -> NamedServiceContext:
    return NamedServiceContext(tenant="demo", project="project", user_id="user-1")


def test_spec_publishes_discoverable_ref_and_object_metadata() -> None:
    spec = docs_named_service_spec()
    metadata = spec.metadata

    assert metadata["canonical_refs"]["document"].startswith("docs:<provider>")
    assert metadata["canonical_refs"]["document"].endswith(":document:<document_id>")
    assert metadata["object_kinds"][DOCS_DOCUMENT_KIND]
    assert metadata["actions"][ACTION_APPEND_TEXT]
    assert OBJECT_RESOLVE in spec.operations


class _FakeDocs:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: dict[str, Any] | None = None
        self.tabs: list[dict[str, Any]] = [
            {
                "tab_id": "tab-main",
                "title": "Main",
                "index": 0,
                "parent_tab_id": "",
                "nesting_level": 0,
                "end_index": 41,
            }
        ]
        self.comments: list[dict[str, Any]] = [
            {
                "comment_id": "c-1",
                "content": "Please expand.",
                "resolved": False,
                "author": "Reviewer",
                "author_is_me": False,
            }
        ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            return dict(self.error)
        operation = kwargs["operation"]
        account_id = kwargs.get("account_id") or "account-1"
        if operation == "search":
            query = kwargs["payload"].get("query")
            if query == "26_006":
                return {
                    "ok": True,
                    "ret": {
                        "account_id": account_id,
                        "items": [
                            {
                                "document_id": "source-docx",
                                "title": "26_006.docx",
                                "logical_title": "26_006",
                                "mime_type": (
                                    "application/vnd.openxmlformats-officedocument."
                                    "wordprocessingml.document"
                                ),
                                "source_format": "docx",
                                "native_document": False,
                                "conversion_required": True,
                                "copyable": True,
                                "exact_title_match": True,
                            }
                        ],
                        "exact_match_count": 1,
                        "incomplete_search": False,
                        "match_mode": "exact_then_title_prefix",
                    },
                }
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "items": [
                        {
                            "document_id": "doc-1",
                            "title": "Launch plan",
                            "web_url": "https://docs.google.com/document/d/doc-1/edit",
                        }
                    ],
                    "next_cursor": "next-1",
                    "exact_match_count": 1,
                    "incomplete_search": False,
                    "match_mode": "exact_then_title_prefix",
                },
            }
        if operation == "get_source":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "source-docx",
                    "title": "26_006.docx",
                    "logical_title": "26_006",
                    "mime_type": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                    "source_format": "docx",
                    "native_document": False,
                    "conversion_required": True,
                    "copyable": True,
                    "web_url": "https://drive.google.com/file/d/source-docx/view",
                    "next_action": "Copy this import source.",
                },
            }
        if operation == "get":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "doc-1",
                    "title": "Launch plan",
                    "revision_id": "rev-9",
                    "web_url": "https://docs.google.com/document/d/doc-1/edit",
                    "text": "Intro paragraph.\nSecond paragraph body.",
                    "tab_count": len(self.tabs),
                    "tabs": list(self.tabs),
                    "end_index": 41,
                },
            }
        if operation == "list_comments":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "doc-1",
                    "comments": list(self.comments),
                    "count": len(self.comments),
                    "next_cursor": "",
                },
            }
        if operation == "create":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "created-1",
                    "title": kwargs["payload"]["title"],
                    "web_url": "https://docs.google.com/document/d/created-1/edit",
                    "revision_id": "rev-1",
                },
            }
        if operation == "copy":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "source_document_id": kwargs["payload"]["document_ref"],
                    "document_id": "copied-1",
                    "title": kwargs["payload"]["title"],
                    "web_url": ("https://docs.google.com/document/d/copied-1/edit"),
                    "copied": True,
                },
            }
        if operation == "export":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "doc-1",
                    "format": kwargs["payload"].get("format"),
                    "content_base64": "AAA=",
                },
            }
        if operation == "create_comment":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "document_id": "doc-1",
                    "comment": {"comment_id": "c-9", "content": "New note."},
                },
            }
        if operation == "delete_comment":
            return {
                "ok": True,
                "ret": {
                    "document_id": "doc-1",
                    "deleted_comment_id": kwargs["payload"].get("comment_id"),
                },
            }
        return {
            "ok": True,
            "ret": {
                "account_id": account_id,
                "document_id": "doc-1",
                "operation": operation,
            },
        }


def _provider(
    fake: _FakeDocs,
    *,
    file_url_factory: Any = None,
) -> DocsNamedServiceProvider:
    return DocsNamedServiceProvider(
        execute_operation=fake.execute,
        bundle_id="kdcube-services@1-0",
        file_url_factory=file_url_factory,
    )


def test_docs_refs_are_provider_neutral_and_stable() -> None:
    ref = document_ref("account-1", "doc-1")
    assert ref == "docs:google:account-1:document:doc-1"
    assert parse_docs_ref(ref) == {
        "ref": ref,
        "provider": "google",
        "account_id": "account-1",
        "document_id": "doc-1",
        "object_kind": DOCS_DOCUMENT_KIND,
    }

    export_ref = document_export_ref("account-1", "doc-1", "docx")
    assert export_ref == "docs:google:account-1:export:docx:doc-1"
    assert parse_docs_export_ref(export_ref) == {
        "ref": export_ref,
        "provider": "google",
        "account_id": "account-1",
        "document_id": "doc-1",
        "format": "docx",
        "mime_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "extension": "docx",
        "object_kind": DOCS_EXPORT_KIND,
    }

    source_ref = document_source_ref("account-1", "source-docx")
    assert source_ref == "docs:google:account-1:source:source-docx"
    assert parse_docs_ref(source_ref) == {
        "ref": source_ref,
        "provider": "google",
        "account_id": "account-1",
        "document_id": "source-docx",
        "object_kind": DOCS_IMPORT_SOURCE_KIND,
    }


@pytest.mark.parametrize(
    "bad_ref",
    [
        "",
        "docs:google:account-1:document",
        "docs:google:account-1:spreadsheet:doc-1",
        "docs:google::document:doc-1",
        "mail:google:account-1:document:doc-1",
        "docs:google:account-1:document:doc-1:tab:7",
    ],
)
def test_parse_docs_ref_rejects_malformed_input(bad_ref: str) -> None:
    with pytest.raises(ValueError):
        parse_docs_ref(bad_ref)


def test_write_grant_and_connected_account_claims_are_separate_boundaries() -> None:
    assert DOCS_GRANT_HINTS["object.upsert"] == [DOCS_WRITE_CLAIM]
    assert DOCS_GRANT_HINTS["object.action.export"] == [DOCS_READ_CLAIM]
    assert DOCS_GRANT_HINTS["object.action.create_comment"] == [DOCS_COMMENT_CLAIM]
    claims_by_operation = DOCS_CONNECTED_ACCOUNT_REQUIREMENTS[0]["claims_by_operation"]
    assert claims_by_operation["object.upsert"] == [
        DOCS_READ_CLAIM,
        DOCS_WRITE_CLAIM,
    ]
    assert claims_by_operation["object.action.create_comment"] == [
        DOCS_READ_CLAIM,
        DOCS_COMMENT_CLAIM,
    ]
    assert claims_by_operation["object.action.export"] == [DOCS_READ_CLAIM]
    assert claims_by_operation["object.action.copy"] == [
        DOCS_READ_CLAIM,
        DOCS_WRITE_CLAIM,
    ]


@pytest.mark.anyio
async def test_about_and_schema_return_expected_shape() -> None:
    fake = _FakeDocs()
    provider = _provider(fake)

    about = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(operation=PROVIDER_ABOUT, namespace="docs"),
    )
    assert about.ok is True
    assert about.extra["schema"]["namespace"] == "docs"
    assert about.extra["schema"]["schema_projection"]["view"] == "catalog"
    assert "google" in about.extra["providers"]

    catalog = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SCHEMA, namespace="docs"),
    )
    assert catalog.ok is True
    assert catalog.extra["schema"]["schema_projection"]["view"] == "catalog"
    assert DOCS_DOCUMENT_KIND in catalog.extra["schema"]["object_kinds"]
    assert DOCS_IMPORT_SOURCE_KIND in catalog.extra["schema"]["object_kinds"]
    assert "actions" not in catalog.extra["schema"]

    kind = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SCHEMA,
            namespace="docs",
            object_kind=DOCS_DOCUMENT_KIND,
        ),
    )
    assert kind.ok is True
    assert kind.extra["schema"]["schema_projection"]["view"] == "kind"
    assert kind.extra["schema"]["refs"]["document"].startswith("docs:<provider>")
    assert "object.action:copy" in kind.extra["schema"]["operations"]
    assert "actions" not in kind.extra["schema"]

    operation = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SCHEMA,
            namespace="docs",
            object_kind=DOCS_DOCUMENT_KIND,
            schema_operation="object.action:copy",
        ),
    )
    assert operation.ok is True
    assert operation.extra["schema"]["schema_projection"]["view"] == "operation"
    assert operation.extra["schema"]["actions"][ACTION_COPY]["payload"] == [
        "title",
        "parent_id",
    ]

    full = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SCHEMA,
            namespace="docs",
            schema_view="full",
        ),
    )
    assert full.ok is True
    assert "hierarchy" in full.extra["schema"]["selectors"]["tab_selector"]["fields"]
    assert full.extra["schema"]["delete"]["payload"] == [
        "comment_id",
        "comment_selector",
    ]
    assert fake.calls == []


@pytest.mark.anyio
async def test_search_returns_named_service_objects() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="docs",
            query="launch",
            limit=5,
            filters={"account_id": "account-1"},
        ),
    )
    assert response.ok is True
    assert response.next_cursor == "next-1"
    assert response.items[0]["ref"] == "docs:google:account-1:document:doc-1"
    assert response.items[0]["object_kind"] == DOCS_DOCUMENT_KIND
    assert response.extra["exact_match_count"] == 1
    assert response.extra["incomplete_search"] is False
    assert response.extra["match_mode"] == "exact_then_title_prefix"
    assert fake.calls == [
        {
            "operation": "search",
            "claim": DOCS_READ_CLAIM,
            "tool_name": "named_services.docs.object.search",
            "payload": {"query": "launch", "limit": 5, "cursor": ""},
            "account_id": "account-1",
        }
    ]


@pytest.mark.anyio
async def test_object_resolve_declares_open_without_reading_provider() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_RESOLVE,
            namespace="docs",
            object_ref=ref,
            action="capabilities",
        ),
    )

    assert response.ok is True
    assert response.capabilities["preview"] is False
    assert response.capabilities["open"] is True
    assert response.extra["default_open_effect_action"] == "open"
    assert fake.calls == []


@pytest.mark.anyio
async def test_open_rechecks_read_access_and_returns_explicit_browser_url() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref=ref,
            action="open",
        ),
    )

    assert response.ok is True
    assert response.capabilities["preview"] is False
    assert response.ui_event == {
        "type": "kdcube.ui.object.open.requested",
        "action": "open",
        "object_ref": ref,
        "external_url": "https://docs.google.com/document/d/doc-1/edit",
        "title": "Launch plan",
    }
    assert response.extra["external_url"] == response.ui_event["external_url"]
    assert fake.calls[-1]["operation"] == "get"
    assert fake.calls[-1]["claim"] == DOCS_READ_CLAIM


@pytest.mark.anyio
async def test_open_import_source_uses_drive_metadata_url() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:source:source-docx"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref=ref,
            action="open",
        ),
    )

    assert response.ok is True
    assert response.ui_event["external_url"] == (
        "https://drive.google.com/file/d/source-docx/view"
    )
    assert fake.calls[-1]["operation"] == "get_source"


@pytest.mark.anyio
async def test_search_returns_docx_as_import_source_with_copy_guidance() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="docs",
            query="26_006",
            limit=5,
            filters={"account_id": "account-1"},
        ),
    )

    assert response.ok is True
    item = response.items[0]
    assert item["ref"] == "docs:google:account-1:source:source-docx"
    assert item["object_kind"] == DOCS_IMPORT_SOURCE_KIND
    assert item["logical_title"] == "26_006"
    assert item["conversion_required"] is True


@pytest.mark.anyio
async def test_get_import_source_returns_metadata_instead_of_reading_docs_body() -> (
    None
):
    fake = _FakeDocs()
    source_ref = "docs:google:account-1:source:source-docx"
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref=source_ref,
        ),
    )

    assert response.ok is True
    assert response.object_ref == source_ref
    assert response.object["object_kind"] == DOCS_IMPORT_SOURCE_KIND
    assert response.object["conversion_required"] is True
    assert fake.calls[-1]["operation"] == "get_source"


@pytest.mark.anyio
async def test_copy_returns_the_new_document_ref_and_uses_read_write_claims() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_COPY,
            payload={"title": "26_007", "parent_id": "folder-1"},
            idempotency_key="invoice-26-007",
        ),
    )

    assert response.ok is True
    assert response.object_ref == "docs:google:account-1:document:copied-1"
    assert response.object["source_document_id"] == "doc-1"
    assert response.object["title"] == "26_007"
    assert fake.calls[-1] == {
        "operation": ACTION_COPY,
        # parent_id widens the credential to drive:write: docs:write's
        # drive.file scope cannot place into a pre-existing folder, and the
        # old docs-only claim is exactly why placement failed live.
        "claim": (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM, "drive:write"),
        "tool_name": "named_services.docs.object.action.copy",
        "payload": {
            "title": "26_007",
            "parent_id": "folder-1",
            "document_ref": "doc-1",
            "idempotency_key": "invoice-26-007",
        },
        "account_id": "account-1",
    }


@pytest.mark.anyio
async def test_copy_without_placement_keeps_the_docs_claims() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_COPY,
            payload={"title": "26_008"},
        ),
    )
    assert response.ok is True
    assert fake.calls[-1]["claim"] == (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM)


@pytest.mark.anyio
async def test_import_with_parent_widens_to_drive_write() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            action=ACTION_IMPORT,
            payload={
                "title": "26_009",
                "source_format": "docx",
                "content_base64": "UEs=",
                "parent_id": "folder-1",
            },
        ),
    )
    assert response.ok is True
    call = fake.calls[-1]
    assert call["operation"] == ACTION_IMPORT
    assert call["claim"] == (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM, DRIVE_WRITE_CLAIM)
    assert call["payload"]["parent_id"] == "folder-1"


@pytest.mark.anyio
async def test_upload_file_routes_to_drive_upload_with_drive_write() -> None:
    """The parity case behind the steuer exercise: a file must reach Drive AS
    ITSELF, in a folder, without ever wearing the docs claims."""
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            action=ACTION_UPLOAD_FILE,
            payload={
                "name": "26_009.docx",
                "content_base64": "UEs=",
                "mime_type": "application/zip",
                "parent_id": "folder-1",
            },
        ),
    )
    assert response.ok is True
    call = fake.calls[-1]
    assert call["operation"] == "drive_upload"
    assert call["claim"] == DRIVE_WRITE_CLAIM
    assert call["payload"]["parent_id"] == "folder-1"
    assert response.ret["extra"]["action"] == ACTION_UPLOAD_FILE


@pytest.mark.anyio
async def test_list_folder_routes_to_drive_list_with_drive_read() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            action=ACTION_LIST_FOLDER,
            payload={"folder_id": "folder-1", "limit": 10},
        ),
    )
    assert response.ok is True
    call = fake.calls[-1]
    assert call["operation"] == "drive_list"
    assert call["claim"] == DRIVE_READ_CLAIM
    assert call["payload"] == {"folder_id": "folder-1", "limit": 10}


@pytest.mark.anyio
async def test_copy_import_source_returns_native_document_ref() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:source:source-docx",
            action=ACTION_COPY,
            payload={"title": "26_007"},
        ),
    )

    assert response.ok is True
    assert response.object_ref == "docs:google:account-1:document:copied-1"
    assert fake.calls[-1]["operation"] == ACTION_COPY
    assert fake.calls[-1]["claim"] == (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM)


@pytest.mark.anyio
async def test_import_source_must_be_copied_before_upsert() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="docs",
            object_ref="docs:google:account-1:source:source-docx",
            object={"replacements": [{"find": "old", "replace": "new"}]},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_import_source_requires_copy"
    assert fake.calls == []


@pytest.mark.anyio
async def test_get_reads_document_metadata_and_text() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace="docs", object_ref=ref),
    )
    assert response.ok is True
    assert response.object["ref"] == ref
    assert response.object["object_kind"] == DOCS_DOCUMENT_KIND
    assert response.object["text"].startswith("Intro paragraph")
    assert fake.calls[0]["operation"] == "get"
    assert fake.calls[0]["claim"] == DOCS_READ_CLAIM
    assert fake.calls[0]["payload"]["document_ref"] == "doc-1"


@pytest.mark.anyio
async def test_turnless_metadata_get_offers_complete_snapshot_download() -> None:
    fake = _FakeDocs()
    minted: list[dict[str, Any]] = []

    async def _file_url(ctx: NamedServiceContext, info: Any) -> dict[str, str]:
        minted.append({"ctx": ctx, "info": dict(info or {})})
        return {
            "url": "https://runtime.test/signed-doc",
            "expires_at": "2026-07-27T12:00:00Z",
        }

    ref = "docs:google:account-1:document:doc-1"
    response = await _provider(fake, file_url_factory=_file_url).dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace="docs", object_ref=ref),
    )

    assert response.ok is True
    assert next(iter(response.object)) == "snapshot"
    assert response.object["snapshot"] == {
        "schema": DOCS_SNAPSHOT_SCHEMA,
        "media_type": DOCS_SNAPSHOT_MEDIA_TYPE,
        "filename": "doc-1.docs.json",
        "download": {
            "encoding": "url",
            "url": "https://runtime.test/signed-doc",
            "expires_at": "2026-07-27T12:00:00Z",
        },
    }
    assert minted[0]["info"] == {"ref": ref}


@pytest.mark.anyio
async def test_turn_scoped_get_does_not_mint_out_of_band_snapshot_url() -> None:
    fake = _FakeDocs()
    calls = 0

    async def _file_url(ctx: NamedServiceContext, info: Any) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"url": "https://runtime.test/unused"}

    response = await _provider(fake, file_url_factory=_file_url).dispatch(
        NamedServiceContext(
            tenant="demo",
            project="project",
            user_id="user-1",
            turn_id="turn-1",
        ),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
        ),
    )

    assert response.ok is True
    assert "snapshot" not in response.object
    assert calls == 0


@pytest.mark.anyio
async def test_get_stream_materializes_complete_doc_snapshot_for_react_pull() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"

    result = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref=ref,
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    assert result.response.ok is True
    assert result.response.object_ref == ref
    assert result.media_type == DOCS_SNAPSHOT_MEDIA_TYPE
    assert result.filename == "doc-1.docs.json"
    snapshot = json.loads(b"".join([chunk async for chunk in result.chunks]))
    assert snapshot["schema"] == DOCS_SNAPSHOT_SCHEMA
    assert snapshot["object_ref"] == ref
    assert snapshot["object"]["text"].startswith("Intro paragraph")
    assert snapshot["comments"][0]["comment_id"] == "c-1"
    assert snapshot["materialization"]["complete_text"] is True
    assert [call["operation"] for call in fake.calls] == ["get", "list_comments"]
    assert fake.calls[0]["claim"] == DOCS_READ_CLAIM
    assert fake.calls[1]["claim"] == DOCS_READ_CLAIM


@pytest.mark.anyio
async def test_get_stream_materializes_import_source_metadata_for_react_pull() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:source:source-docx"

    result = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref=ref,
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    snapshot = json.loads(b"".join([chunk async for chunk in result.chunks]))
    assert snapshot["object_ref"] == ref
    assert snapshot["object_kind"] == DOCS_IMPORT_SOURCE_KIND
    assert snapshot["object"]["logical_title"] == "26_006"
    assert snapshot["object"]["conversion_required"] is True
    assert snapshot["materialization"]["complete_text"] is False
    assert [call["operation"] for call in fake.calls] == ["get_source"]


@pytest.mark.anyio
async def test_event_resolve_maps_doc_refs_without_calling_google() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(operation=EVENT_RESOLVE, namespace="docs", object_ref=ref),
    )

    assert response.ok is True
    assert response.object_ref == ref
    assert response.extra["event_source_id"] == "named_services.docs"
    assert response.extra["target_surface"] == "sdk.docs.snapshot"
    assert fake.calls == []


@pytest.mark.anyio
async def test_block_produce_projects_document_inventory_and_preview() -> None:
    fake = _FakeDocs()
    ref = "docs:google:account-1:document:doc-1"
    snapshot = {
        "schema": DOCS_SNAPSHOT_SCHEMA,
        "object_ref": ref,
        "object_kind": DOCS_DOCUMENT_KIND,
        "object": {
            "ref": ref,
            "object_kind": DOCS_DOCUMENT_KIND,
            "document_id": "doc-1",
            "title": "Launch plan",
            "web_url": "https://docs.google.com/document/d/doc-1/edit",
            "revision_id": "rev-9",
            "text": "First heading line.\nBody content here.",
            "tabs": [
                {"tab_id": "main", "title": "Main", "parent_tab_id": ""},
                {
                    "tab_id": "notes",
                    "title": "Notes",
                    "parent_tab_id": "main",
                },
            ],
            "end_index": 41,
        },
        "comments": [{"comment_id": "c-1", "content": "note"}],
        "materialization": {"text_materialized": True, "complete_text": True},
    }
    logical_path = "conv:fi:conv_1.turn_1.named_services/docs/doc-1.docs.json"
    physical_path = "turn_1/attachments/named_services/docs/doc-1.docs.json"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=BLOCK_PRODUCE,
            namespace="docs",
            object_ref=ref,
            payload={
                "target": {
                    "turn_id": "turn_1",
                    "tool_call_id": "tc_1",
                    "tool_id": "react.read",
                    "logical_path": logical_path,
                    "raw": {
                        "text": json.dumps(snapshot),
                        "mime": DOCS_SNAPSHOT_MEDIA_TYPE,
                    },
                    "meta": {"physical_path": physical_path},
                }
            },
        ),
    )

    assert response.ok is True
    assert len(response.extra["blocks"]) == 1
    block = response.extra["blocks"][0]
    assert block["path"] == ref
    assert "[DOCS SNAPSHOT]" in block["text"]
    assert "Launch plan" in block["text"]
    assert "First heading line." in block["text"]
    assert "comment_count: 1" in block["text"]
    assert "position=2; title=Notes; hierarchy=Main / Notes" in block["text"]
    assert "mutation_scope: use tab_selector" in block["text"]
    assert logical_path in block["text"]
    assert physical_path in block["text"]
    assert fake.calls == []


@pytest.mark.anyio
async def test_upsert_create_and_edit_route_to_google_operations() -> None:
    fake = _FakeDocs()
    provider = _provider(fake)

    created = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="docs",
            object={"title": "Created from named services"},
        ),
    )
    assert created.ok is True
    assert created.object_ref == "docs:google:account-1:document:created-1"
    assert fake.calls[0]["operation"] == "create"
    assert fake.calls[0]["claim"] == (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM)

    appended = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            object={"text": "More body.", "tab_id": "tab-main"},
        ),
    )
    assert appended.ok is True
    assert fake.calls[-1]["operation"] == "append_text"
    assert fake.calls[-1]["payload"]["text"] == "More body."
    assert fake.calls[-1]["payload"]["tab_id"] == "tab-main"

    replaced = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            object={
                "replacements": [{"find": "a", "replace": "b"}],
                "tab_ids": ["tab-main"],
                "all_tabs": False,
            },
        ),
    )
    assert replaced.ok is True
    assert fake.calls[-1]["operation"] == "replace_text"
    assert fake.calls[-1]["payload"]["tab_ids"] == ["tab-main"]
    assert fake.calls[-1]["payload"]["all_tabs"] is False


@pytest.mark.anyio
async def test_tab_selection_error_preserves_available_tabs() -> None:
    fake = _FakeDocs()
    fake.error = {
        "ok": False,
        "error": {
            "code": "docs_tab_selection_required",
            "message": "This document has multiple tabs.",
        },
        "ret": {
            "outcome_unknown": False,
            "tab_count": 2,
            "tabs": [
                {"tab_id": "tab-main", "title": "Main"},
                {"tab_id": "tab-notes", "title": "Notes"},
            ],
            "next_action": "Choose a tab_id and retry.",
        },
    }
    provider = _provider(fake)

    response = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_APPEND_TEXT,
            payload={"text": "More body."},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_tab_selection_required"
    assert response.error.details["ret"]["tab_count"] == 2
    assert response.error.details["ret"]["tabs"][1]["tab_id"] == "tab-notes"


@pytest.mark.anyio
async def test_tab_selector_resolves_title_fragment_before_single_tab_action() -> None:
    fake = _FakeDocs()
    fake.tabs = [
        {
            "tab_id": "tab-main",
            "title": "Overview",
            "parent_tab_id": "",
            "nesting_level": 0,
            "end_index": 20,
        },
        {
            "tab_id": "tab-invoices",
            "title": "July Invoices",
            "parent_tab_id": "",
            "nesting_level": 0,
            "end_index": 80,
        },
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_APPEND_TEXT,
            payload={
                "text": "Paid.",
                "tab_selector": {"title_contains": "invoices"},
            },
        ),
    )

    assert response.ok is True
    assert [call["operation"] for call in fake.calls] == ["get", ACTION_APPEND_TEXT]
    assert fake.calls[-1]["payload"]["tab_id"] == "tab-invoices"
    assert "tab_selector" not in fake.calls[-1]["payload"]
    resolution = response.extra["selector_resolution"]
    assert resolution["matches"][0]["title"] == "July Invoices"
    assert resolution["matches"][0]["position"] == 2


@pytest.mark.anyio
async def test_tab_selector_ambiguity_returns_hierarchical_candidates() -> None:
    fake = _FakeDocs()
    fake.tabs = [
        {
            "tab_id": "tab-current",
            "title": "Current",
            "parent_tab_id": "",
            "nesting_level": 0,
        },
        {
            "tab_id": "tab-current-notes",
            "title": "Notes",
            "parent_tab_id": "tab-current",
            "nesting_level": 1,
        },
        {
            "tab_id": "tab-archive",
            "title": "Archive",
            "parent_tab_id": "",
            "nesting_level": 0,
        },
        {
            "tab_id": "tab-archive-notes",
            "title": "Notes",
            "parent_tab_id": "tab-archive",
            "nesting_level": 1,
        },
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_APPEND_TEXT,
            payload={"text": "More.", "tab_selector": {"title": "Notes"}},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_tab_selector_ambiguous"
    assert [
        candidate["hierarchy"] for candidate in response.error.details["candidates"]
    ] == [["Current", "Notes"], ["Archive", "Notes"]]
    assert [call["operation"] for call in fake.calls] == ["get"]


@pytest.mark.anyio
async def test_plural_tab_selectors_resolve_before_replacement() -> None:
    fake = _FakeDocs()
    fake.tabs = [
        {"tab_id": "tab-a", "title": "January", "parent_tab_id": ""},
        {"tab_id": "tab-b", "title": "February", "parent_tab_id": ""},
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_REPLACE_TEXT,
            payload={
                "replacements": [{"find": "draft", "replace": "final"}],
                "tab_selectors": [
                    {"title": "January"},
                    {"position": 2},
                ],
            },
        ),
    )

    assert response.ok is True
    assert fake.calls[-1]["payload"]["tab_ids"] == ["tab-a", "tab-b"]
    assert "tab_selectors" not in fake.calls[-1]["payload"]


@pytest.mark.anyio
async def test_comment_selector_resolves_my_matching_comment_before_reply() -> None:
    fake = _FakeDocs()
    fake.comments = [
        {
            "comment_id": "c-reviewer",
            "content": "Please verify the invoice total.",
            "resolved": False,
            "author": "Reviewer",
            "author_is_me": False,
        },
        {
            "comment_id": "c-mine",
            "content": "Invoice total is ready for review.",
            "resolved": False,
            "author": "Elena Viter",
            "author_is_me": True,
        },
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_REPLY_COMMENT,
            payload={
                "comment_selector": {
                    "text_contains": "invoice total",
                    "author": "me",
                    "resolved": False,
                },
                "content": "Confirmed.",
            },
        ),
    )

    assert response.ok is True
    assert [call["operation"] for call in fake.calls] == [
        "list_comments",
        ACTION_REPLY_COMMENT,
    ]
    assert fake.calls[-1]["payload"]["comment_id"] == "c-mine"
    assert "comment_selector" not in fake.calls[-1]["payload"]
    assert response.extra["selector_resolution"]["match"]["author_is_me"] is True


@pytest.mark.anyio
async def test_comment_selector_follows_bounded_provider_pagination() -> None:
    class _PagedComments(_FakeDocs):
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["operation"] != "list_comments":
                return await super().execute(**kwargs)
            self.calls.append(dict(kwargs))
            cursor = kwargs["payload"].get("cursor")
            if not cursor:
                return {
                    "ok": True,
                    "ret": {
                        "comments": [
                            {
                                "comment_id": "c-first",
                                "content": "First page.",
                                "author": "Reviewer",
                                "resolved": False,
                            }
                        ],
                        "next_cursor": "page-2",
                    },
                }
            return {
                "ok": True,
                "ret": {
                    "comments": [
                        {
                            "comment_id": "c-target",
                            "content": "Payment terms need review.",
                            "author": "Reviewer",
                            "resolved": False,
                        }
                    ],
                    "next_cursor": "",
                },
            }

    fake = _PagedComments()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_GET_COMMENT,
            payload={"comment_selector": {"text_contains": "payment terms"}},
        ),
    )

    assert response.ok is True
    assert [call["operation"] for call in fake.calls] == [
        "list_comments",
        "list_comments",
        ACTION_GET_COMMENT,
    ]
    assert fake.calls[1]["payload"]["cursor"] == "page-2"
    assert fake.calls[-1]["payload"]["comment_id"] == "c-target"
    assert response.extra["selector_resolution"]["scanned_pages"] == 2


@pytest.mark.anyio
async def test_comment_selector_reports_incomplete_bounded_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MoreComments(_FakeDocs):
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["operation"] != "list_comments":
                return await super().execute(**kwargs)
            self.calls.append(dict(kwargs))
            return {
                "ok": True,
                "ret": {
                    "comments": [
                        {
                            "comment_id": "c-possible",
                            "content": "Payment terms need review.",
                            "author": "Reviewer",
                            "resolved": False,
                        }
                    ],
                    "next_cursor": "more-comments",
                },
            }

    monkeypatch.setattr(docs_named_service, "COMMENT_SELECTOR_MAX_PAGES", 1)
    fake = _MoreComments()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_GET_COMMENT,
            payload={"comment_selector": {"text_contains": "payment terms"}},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_comment_selector_incomplete"
    assert response.error.details["next_cursor"] == "more-comments"
    assert response.error.details["candidates"][0]["comment_id"] == "c-possible"
    assert [call["operation"] for call in fake.calls] == ["list_comments"]


@pytest.mark.anyio
async def test_comment_selector_ambiguity_does_not_run_the_requested_action() -> None:
    fake = _FakeDocs()
    fake.comments = [
        {
            "comment_id": "c-1",
            "content": "Please update the total.",
            "resolved": False,
            "author": "Reviewer A",
        },
        {
            "comment_id": "c-2",
            "content": "The total needs a currency.",
            "resolved": False,
            "author": "Reviewer B",
        },
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_GET_COMMENT,
            payload={"comment_selector": {"text_contains": "total"}},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_comment_selector_ambiguous"
    assert len(response.error.details["candidates"]) == 2
    assert [call["operation"] for call in fake.calls] == ["list_comments"]


@pytest.mark.anyio
async def test_tab_scoped_comment_request_names_the_capability_boundary() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_CREATE_COMMENT,
            payload={
                "content": "Review this tab.",
                "tab_selector": {"title": "Main"},
            },
        ),
    )

    assert response.ok is False
    assert response.error.code == "tab_anchored_comments_unavailable"
    assert response.error.details["supported_scope"] == "document"
    assert fake.calls == []


@pytest.mark.anyio
async def test_actions_gate_on_the_right_claim_per_verb() -> None:
    fake = _FakeDocs()
    provider = _provider(fake)
    ref = "docs:google:account-1:document:doc-1"

    exported = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref=ref,
            action=ACTION_EXPORT,
            payload={"format": "pdf"},
        ),
    )
    assert exported.ok is True
    assert exported.object_ref == "docs:google:account-1:export:pdf:doc-1"
    assert exported.object["filename"] == "Launch plan.pdf"
    assert exported.object["delivery"]["response_mode"] == "stream"
    assert fake.calls[-1]["operation"] == "get"
    assert fake.calls[-1]["claim"] == DOCS_READ_CLAIM
    assert fake.calls[-1]["payload"]["document_ref"] == "doc-1"

    commented = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref=ref,
            action=ACTION_CREATE_COMMENT,
            payload={"content": "New note."},
        ),
    )
    assert commented.ok is True
    assert fake.calls[-1]["operation"] == ACTION_CREATE_COMMENT
    assert fake.calls[-1]["claim"] == (DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM)


@pytest.mark.anyio
async def test_export_action_returns_downloadable_file_object_without_base64() -> None:
    fake = _FakeDocs()

    async def file_url_factory(ctx: NamedServiceContext, info: dict[str, Any]):
        assert ctx.user_id == "user-1"
        assert info["ref"] == "docs:google:account-1:export:docx:doc-1"
        return {
            "url": "https://example.test/download?token=signed",
            "expires_at": "2026-07-31T12:00:00Z",
        }

    response = await _provider(
        fake,
        file_url_factory=file_url_factory,
    ).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_EXPORT,
            payload={"format": "docx"},
        ),
    )

    assert response.ok is True
    assert response.object_ref == "docs:google:account-1:export:docx:doc-1"
    assert response.object["object_kind"] == DOCS_EXPORT_KIND
    assert response.object["filename"] == "Launch plan.docx"
    assert response.object["download"] == {
        "encoding": "url",
        "url": "https://example.test/download?token=signed",
        "expires_at": "2026-07-31T12:00:00Z",
    }
    model_payload, files = collect_file_deliveries(response.to_dict())
    assert files == [
        {
            "object_ref": "docs:google:account-1:export:docx:doc-1",
            "ref": "docs:google:account-1:export:docx:doc-1",
            "filename": "Launch plan.docx",
            "mime": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            "description": DOCS_EXPORT_KIND,
        }
    ]
    assert model_payload["ret"]["object"]["download"]["encoding"] == "chat"
    assert "content_base64" not in json.dumps(response.to_dict())
    assert [call["operation"] for call in fake.calls] == ["get"]


@pytest.mark.anyio
async def test_export_ref_streams_complete_file_bytes_for_react_pull() -> None:
    fake = _FakeDocs()
    export_ref = "docs:google:account-1:export:pdf:doc-1"

    result = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref=export_ref,
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    assert result.response.ok is True
    assert result.response.object_ref == export_ref
    assert result.filename == "Launch plan.pdf"
    assert result.media_type == "application/pdf"
    assert b"".join([chunk async for chunk in result.chunks]) == b"\x00\x00"
    assert [call["operation"] for call in fake.calls] == ["get", "export"]
    assert all(call["claim"] == DOCS_READ_CLAIM for call in fake.calls)


@pytest.mark.anyio
async def test_export_action_rejects_unknown_format_before_provider_call() -> None:
    fake = _FakeDocs()

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_EXPORT,
            payload={"format": "pages"},
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_export_format_invalid"
    assert fake.calls == []


@pytest.mark.anyio
async def test_unknown_action_is_rejected_before_google_call() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action="drop_database",
            payload={},
        ),
    )
    assert response.ok is False
    assert response.error.code == "docs_action_not_supported"
    assert fake.calls == []


@pytest.mark.anyio
async def test_delete_removes_a_comment_and_refuses_document_deletion() -> None:
    fake = _FakeDocs()
    provider = _provider(fake)
    ref = "docs:google:account-1:document:doc-1"

    deleted = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_DELETE,
            namespace="docs",
            object_ref=ref,
            payload={"comment_id": "c-1"},
        ),
    )
    assert deleted.ok is True
    assert fake.calls[-1]["operation"] == ACTION_DELETE_COMMENT
    assert fake.calls[-1]["claim"] == (DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM)
    assert fake.calls[-1]["payload"]["comment_id"] == "c-1"

    refused = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_DELETE, namespace="docs", object_ref=ref),
    )
    assert refused.ok is False
    assert refused.error.code == "docs_document_delete_not_supported"


@pytest.mark.anyio
async def test_delete_accepts_a_natural_comment_selector() -> None:
    fake = _FakeDocs()
    fake.comments = [
        {
            "comment_id": "c-own",
            "content": "Remove this obsolete note.",
            "resolved": True,
            "author": "Elena Viter",
            "author_is_me": True,
        }
    ]

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_DELETE,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            payload={
                "comment_selector": {
                    "text_contains": "obsolete note",
                    "author": "me",
                }
            },
        ),
    )

    assert response.ok is True
    assert [call["operation"] for call in fake.calls] == [
        "list_comments",
        ACTION_DELETE_COMMENT,
    ]
    assert fake.calls[-1]["payload"]["comment_id"] == "c-own"


@pytest.mark.anyio
async def test_connected_account_consent_error_is_preserved() -> None:
    fake = _FakeDocs()
    fake.error = {
        "ok": False,
        "error": {
            "code": "needs_connected_account_consent",
            "message": "Approve document access.",
        },
        "consent": {
            "reason": "claim_upgrade_required",
            "provider_id": "google",
            "claims": [DOCS_READ_CLAIM],
            "url": "https://runtime.test/connections",
            "retry_hint": True,
        },
    }
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace="docs", query="plan"),
    )
    assert response.ok is False
    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    assert response.error.details["reason"] == "claim_upgrade_required"
    assert response.error.details["claims"] == [DOCS_READ_CLAIM]
    assert response.error.details["connection_hub_url"] == (
        "https://runtime.test/connections"
    )


@pytest.mark.anyio
async def test_canonical_fields_and_ref_boundaries_cannot_be_overridden() -> None:
    fake = _FakeDocs()

    async def _malicious_search(**kwargs: Any) -> dict[str, Any]:
        fake.calls.append(dict(kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": "account-1",
                "items": [
                    {
                        "document_id": "doc-1",
                        "ref": "mail:message:not-a-doc",
                        "object_kind": "wrong.kind",
                        "account_id": "wrong-account",
                        "provider": "excel",
                    }
                ],
            },
        }

    provider = DocsNamedServiceProvider(execute_operation=_malicious_search)
    response = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="docs",
            query="",
            filters={"account_id": "account-1"},
        ),
    )
    assert response.ok is True
    assert response.items[0]["ref"] == "docs:google:account-1:document:doc-1"
    assert response.items[0]["object_kind"] == DOCS_DOCUMENT_KIND
    assert response.items[0]["account_id"] == "account-1"
    assert response.items[0]["provider"] == "google"


@pytest.mark.anyio
async def test_unknown_provider_fails_before_google_call() -> None:
    fake = _FakeDocs()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:excel:account-1:document:doc-1",
            action=ACTION_EXPORT,
            payload={"format": "pdf"},
        ),
    )
    assert response.ok is False
    assert response.error.code == "docs_provider_not_implemented"
    assert fake.calls == []


@pytest.mark.anyio
async def test_get_passes_table_reads_to_the_provider() -> None:
    fake = _FakeDocs()
    selector = {"table": {"after_heading": "Tasks"}, "rows": "1-10"}

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            filters={"tables": selector},
        ),
    )

    assert response.ok is True
    assert fake.calls[-1]["operation"] == "get"
    assert fake.calls[-1]["claim"] == DOCS_READ_CLAIM
    assert fake.calls[-1]["payload"]["tables"] == selector


@pytest.mark.anyio
async def test_include_tables_reads_every_table() -> None:
    fake = _FakeDocs()

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            include=["tables"],
        ),
    )

    assert response.ok is True
    assert fake.calls[-1]["payload"]["tables"] == "all"


@pytest.mark.anyio
async def test_named_tables_win_over_include() -> None:
    fake = _FakeDocs()
    selector = {"table": {"position": 2}}

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            include=["cells"],
            filters={"tables": selector},
        ),
    )

    assert response.ok is True
    assert fake.calls[-1]["payload"]["tables"] == selector


@pytest.mark.anyio
async def test_an_unknown_include_is_refused_instead_of_dropped() -> None:
    fake = _FakeDocs()

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            include=["comments"],
        ),
    )

    assert response.ok is False
    assert response.error.code == "docs_include_unsupported"
    assert "filters.tables" in response.error.message
    # Nothing was read: a silently dropped hint is what made an agent report
    # that a cell held no chip.
    assert fake.calls == []


@pytest.mark.anyio
async def test_set_cells_resolves_the_tab_and_needs_read_and_write() -> None:
    fake = _FakeDocs()
    fake.tabs = [
        {"tab_id": "tab-main", "title": "Main", "parent_tab_id": "", "nesting_level": 0},
        {"tab_id": "tab-budget", "title": "Budget", "parent_tab_id": "", "nesting_level": 0},
    ]
    payload = {
        "tab_selector": {"title": "budget"},
        "table": 1,
        "row": {"where": {"column": "Item", "equals": "Office"}},
        "cells": {"Amount": "130"},
        "mode": "replace",
        "revision_id": "rev-1",
    }

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            action=ACTION_SET_CELLS,
            payload=payload,
        ),
    )

    assert response.ok is True
    call = fake.calls[-1]
    assert call["operation"] == ACTION_SET_CELLS
    assert call["claim"] == (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM)
    assert call["payload"]["tab_id"] == "tab-budget"
    for key in ("table", "row", "cells", "mode", "revision_id"):
        assert call["payload"][key] == payload[key]


@pytest.mark.anyio
async def test_snapshot_asks_the_provider_for_every_table_cell() -> None:
    fake = _FakeDocs()

    await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="docs",
            object_ref="docs:google:account-1:document:doc-1",
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert fake.calls[0]["operation"] == "get"
    assert fake.calls[0]["payload"]["include_table_cells"] is True


def test_schema_declares_set_cells_and_table_selectors() -> None:
    schema = docs_named_service.DOCS_SCHEMA
    action = schema["actions"][ACTION_SET_CELLS]
    assert action["claim"] == "docs:write"
    assert action["modes"] == ["replace", "append", "prepend"]
    assert {"table", "row", "cells", "remove_objects"} <= set(action["payload"])
    assert {"table", "row"} <= set(schema["selectors"])
    assert "tables" in schema["get"]["filters"]
    assert ACTION_SET_CELLS in docs_named_service.DOCS_WRITE_ACTIONS
    assert docs_named_service.DOCS_GRANT_HINTS[f"object.action.{ACTION_SET_CELLS}"] == [
        DOCS_WRITE_CLAIM
    ]
