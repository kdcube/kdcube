from __future__ import annotations

import json
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.sheets.named_service import (
    ACTION_APPEND_ROWS,
    ACTION_DELETE_TAB,
    SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS,
    SHEETS_GRANT_HINTS,
    SHEETS_READ_CLAIM,
    SHEETS_SNAPSHOT_MEDIA_TYPE,
    SHEETS_SNAPSHOT_SCHEMA,
    SHEETS_SPREADSHEET_KIND,
    SHEETS_WRITE_CLAIM,
    SheetsNamedServiceProvider,
    parse_sheets_ref,
    sheets_named_service_spec,
    spreadsheet_ref,
    tab_ref,
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
    OBJECT_SEARCH,
    OBJECT_UPSERT,
)


def _ctx() -> NamedServiceContext:
    return NamedServiceContext(tenant="demo", project="project", user_id="user-1")


def test_spec_publishes_discoverable_ref_and_object_metadata() -> None:
    spec = sheets_named_service_spec()
    metadata = spec.metadata

    assert metadata["canonical_refs"]["spreadsheet"].startswith(
        "sheets:<provider>"
    )
    assert metadata["canonical_refs"]["tab"].endswith(":tab:<sheet_id>")
    assert metadata["object_kinds"][SHEETS_SPREADSHEET_KIND]
    assert metadata["actions"][ACTION_APPEND_ROWS]
    assert OBJECT_RESOLVE in spec.operations


class _FakeSheets:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: dict[str, Any] | None = None

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            return dict(self.error)
        operation = kwargs["operation"]
        account_id = kwargs.get("account_id") or "account-1"
        if operation == "search":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "items": [
                        {
                            "spreadsheet_id": "sheet-1",
                            "title": "Quarterly plan",
                            "web_url": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
                        }
                    ],
                    "next_cursor": "next-1",
                },
            }
        if operation == "describe":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "spreadsheet_id": "sheet-1",
                    "title": "Quarterly plan",
                    "web_url": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
                    "tabs": [
                        {
                            "sheet_id": 7,
                            "title": "Plan",
                            "index": 0,
                            "row_count": 100,
                            "column_count": 12,
                        }
                    ],
                },
            }
        if operation == "read":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "spreadsheet_id": "sheet-1",
                    "ranges": [
                        {"range": "Plan!A1:B2", "values": [["A", "B"], [1, 2]]}
                    ],
                    "cell_count": 4,
                },
            }
        if operation == "create_spreadsheet":
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "spreadsheet_id": "created-1",
                    "title": kwargs["payload"]["title"],
                    "first_tab": {"sheet_id": 0, "title": "Sheet1", "index": 0},
                },
            }
        if operation in {"update_tab", "add_tab"}:
            return {
                "ok": True,
                "ret": {
                    "account_id": account_id,
                    "spreadsheet_id": "sheet-1",
                    "tab": {"sheet_id": 7, "title": "Updated", "index": 0},
                },
            }
        return {
            "ok": True,
            "ret": {
                "account_id": account_id,
                "spreadsheet_id": "sheet-1",
                "operation": operation,
            },
        }


def _provider(
    fake: _FakeSheets,
    *,
    file_url_factory: Any = None,
) -> SheetsNamedServiceProvider:
    return SheetsNamedServiceProvider(
        execute_operation=fake.execute,
        bundle_id="kdcube-services@1-0",
        file_url_factory=file_url_factory,
    )


def test_sheets_refs_are_provider_neutral_and_stable() -> None:
    spreadsheet = spreadsheet_ref(
        provider="google",
        account_id="account-1",
        spreadsheet_id="sheet-1",
    )
    tab = tab_ref(
        provider="google",
        account_id="account-1",
        spreadsheet_id="sheet-1",
        sheet_id=7,
    )
    assert spreadsheet == "sheets:google:account-1:spreadsheet:sheet-1"
    assert tab == "sheets:google:account-1:spreadsheet:sheet-1:tab:7"
    assert parse_sheets_ref(spreadsheet)["kind"] == "spreadsheet"
    assert parse_sheets_ref(tab) == {
        "ref": tab,
        "provider": "google",
        "account_id": "account-1",
        "spreadsheet_id": "sheet-1",
        "sheet_id": 7,
        "kind": "tab",
    }

    large_sheet_id = 1_932_156_744
    assert tab_ref(
        provider="google",
        account_id="account-1",
        spreadsheet_id="sheet-1",
        sheet_id=large_sheet_id,
    ).endswith(f":tab:{large_sheet_id}")


def test_write_grant_and_connected_account_claims_are_separate_boundaries() -> None:
    assert SHEETS_GRANT_HINTS["object.upsert"] == [SHEETS_WRITE_CLAIM]
    claims_by_operation = SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS[0][
        "claims_by_operation"
    ]
    assert claims_by_operation["object.upsert"] == [
        SHEETS_READ_CLAIM,
        SHEETS_WRITE_CLAIM,
    ]


@pytest.mark.anyio
async def test_search_returns_named_service_objects() -> None:
    fake = _FakeSheets()
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="sheets",
            query="quarterly",
            limit=5,
            filters={"account_id": "account-1"},
        ),
    )
    assert response.ok is True
    assert response.next_cursor == "next-1"
    assert response.items[0]["ref"] == (
        "sheets:google:account-1:spreadsheet:sheet-1"
    )
    assert response.items[0]["object_kind"] == "sheets.spreadsheet"
    assert fake.calls == [
        {
            "operation": "search",
            "claim": SHEETS_READ_CLAIM,
            "tool_name": "named_services.sheets.object.search",
            "payload": {"query": "quarterly", "limit": 5, "cursor": ""},
            "account_id": "account-1",
        }
    ]


@pytest.mark.anyio
async def test_object_resolve_declares_spreadsheet_open_without_provider_call() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_RESOLVE,
            namespace="sheets",
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
async def test_open_spreadsheet_rechecks_read_access_and_returns_browser_url() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="sheets",
            object_ref=ref,
            action="open",
        ),
    )

    assert response.ok is True
    assert response.capabilities["preview"] is False
    assert response.ui_event["external_url"] == (
        "https://docs.google.com/spreadsheets/d/sheet-1/edit"
    )
    assert fake.calls[-1]["operation"] == "describe"
    assert fake.calls[-1]["claim"] == SHEETS_READ_CLAIM


@pytest.mark.anyio
async def test_open_tab_targets_its_google_sheet_gid() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1:tab:7"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="sheets",
            object_ref=ref,
            action="open",
        ),
    )

    assert response.ok is True
    assert response.object_ref == ref
    assert response.ui_event["external_url"].endswith("#gid=7")
    assert response.object["title"] == "Plan"


@pytest.mark.anyio
async def test_get_reads_metadata_or_explicit_ranges() -> None:
    fake = _FakeSheets()
    provider = _provider(fake)
    ref = "sheets:google:account-1:spreadsheet:sheet-1"

    metadata = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace="sheets", object_ref=ref),
    )
    assert metadata.ok is True
    assert metadata.object["tabs"][0]["ref"].endswith(":tab:7")

    values = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref=ref,
            filters={"ranges": ["Plan!A1:B2"]},
        ),
    )
    assert values.ok is True
    assert values.object["ranges"][0]["values"] == [["A", "B"], [1, 2]]
    assert [call["operation"] for call in fake.calls] == ["describe", "read"]


@pytest.mark.anyio
async def test_turnless_metadata_get_offers_complete_snapshot_download() -> None:
    fake = _FakeSheets()
    minted: list[dict[str, Any]] = []

    async def _file_url(ctx: NamedServiceContext, info: Any) -> dict[str, str]:
        minted.append({"ctx": ctx, "info": dict(info or {})})
        return {
            "url": "https://runtime.test/signed-sheet",
            "expires_at": "2026-07-27T12:00:00Z",
        }

    ref = "sheets:google:account-1:spreadsheet:sheet-1"
    response = await _provider(fake, file_url_factory=_file_url).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref=ref,
        ),
    )

    assert response.ok is True
    assert response.object["snapshot"] == {
        "schema": SHEETS_SNAPSHOT_SCHEMA,
        "media_type": SHEETS_SNAPSHOT_MEDIA_TYPE,
        "filename": "sheet-1.sheets.json",
        "download": {
            "encoding": "url",
            "url": "https://runtime.test/signed-sheet",
            "expires_at": "2026-07-27T12:00:00Z",
        },
    }
    assert minted[0]["info"] == {"ref": ref}


@pytest.mark.anyio
async def test_turn_scoped_get_does_not_mint_out_of_band_snapshot_url() -> None:
    fake = _FakeSheets()
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
            namespace="sheets",
            object_ref="sheets:google:account-1:spreadsheet:sheet-1",
        ),
    )

    assert response.ok is True
    assert "snapshot" not in response.object
    assert calls == 0


@pytest.mark.anyio
async def test_turnless_ranged_get_keeps_complete_snapshot_download_fallback() -> None:
    async def _file_url(ctx: NamedServiceContext, info: Any) -> dict[str, str]:
        return {"url": "https://runtime.test/signed-sheet"}

    response = await _provider(
        _FakeSheets(),
        file_url_factory=_file_url,
    ).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref="sheets:google:account-1:spreadsheet:sheet-1",
            filters={"ranges": ["Plan!A1:B2"]},
        ),
    )

    assert response.ok is True
    assert next(iter(response.object)) == "snapshot"
    assert response.object["snapshot"]["download"]["url"] == (
        "https://runtime.test/signed-sheet"
    )
    assert response.object["ranges"][0]["values"] == [["A", "B"], [1, 2]]


@pytest.mark.anyio
async def test_get_stream_materializes_complete_sheet_snapshot_for_react_pull() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1"

    result = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref=ref,
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    assert result.response.ok is True
    assert result.response.object_ref == ref
    assert result.media_type == SHEETS_SNAPSHOT_MEDIA_TYPE
    assert result.filename == "sheet-1.sheets.json"
    body = b"".join([chunk async for chunk in result.chunks])
    snapshot = json.loads(body)
    assert snapshot["schema"] == SHEETS_SNAPSHOT_SCHEMA
    assert snapshot["object_ref"] == ref
    assert snapshot["object"]["tabs"][0]["ref"].endswith(":tab:7")
    assert snapshot["values"]["ranges"][0]["values"] == [["A", "B"], [1, 2]]
    assert snapshot["materialization"]["complete_values"] is True
    assert [call["operation"] for call in fake.calls] == ["describe", "read"]
    assert fake.calls[1]["payload"]["ranges"] == ["'Plan'"]
    assert fake.calls[0]["claim"] == SHEETS_READ_CLAIM
    assert fake.calls[1]["claim"] == SHEETS_READ_CLAIM


@pytest.mark.anyio
async def test_large_snapshot_streams_in_multiple_lossless_chunks() -> None:
    class _LargeSheets(_FakeSheets):
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["operation"] != "read":
                return await super().execute(**kwargs)
            self.calls.append(dict(kwargs))
            large_cell = "sheet-value-" * 10_000
            return {
                "ok": True,
                "ret": {
                    "account_id": "account-1",
                    "spreadsheet_id": "sheet-1",
                    "ranges": [
                        {"range": "Plan!A1", "values": [[large_cell]]}
                    ],
                    "range_count": 1,
                    "row_count": 1,
                    "cell_count": 1,
                },
            }

    result = await _provider(_LargeSheets()).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref="sheets:google:account-1:spreadsheet:sheet-1",
            response_mode="stream",
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    chunks = [chunk async for chunk in result.chunks]
    assert len(chunks) > 1
    snapshot = json.loads(b"".join(chunks))
    assert snapshot["values"]["ranges"][0]["values"][0][0] == (
        "sheet-value-" * 10_000
    )


@pytest.mark.anyio
async def test_get_stream_materializes_only_the_requested_tab() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1:tab:7"

    result = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref=ref,
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert isinstance(result, NamedServiceStreamResult)
    assert result.response.object_ref == ref
    assert result.filename == "sheet-1-tab-7.sheets.json"
    snapshot = json.loads(b"".join([chunk async for chunk in result.chunks]))
    assert snapshot["object_ref"] == ref
    assert snapshot["object_kind"] == "sheets.tab"
    assert snapshot["object"]["ref"] == ref
    assert snapshot["materialization"]["selected_tab_count"] == 1
    assert fake.calls[1]["payload"]["ranges"] == ["'Plan'"]


@pytest.mark.anyio
async def test_event_resolve_maps_sheet_refs_without_calling_google() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1:tab:7"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=EVENT_RESOLVE,
            namespace="sheets",
            object_ref=ref,
        ),
    )

    assert response.ok is True
    assert response.object_ref == ref
    assert response.extra["event_source_id"] == "named_services.sheets"
    assert response.extra["target_surface"] == "sdk.sheets.snapshot"
    assert fake.calls == []


@pytest.mark.anyio
async def test_block_produce_projects_inventory_without_cell_values() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1"
    snapshot = {
        "schema": SHEETS_SNAPSHOT_SCHEMA,
        "object_ref": ref,
        "object_kind": "sheets.spreadsheet",
        "object": {
            "ref": ref,
            "object_kind": "sheets.spreadsheet",
            "spreadsheet_id": "sheet-1",
            "title": "Quarterly plan",
            "tabs": [
                {
                    "ref": f"{ref}:tab:7",
                    "sheet_id": 7,
                    "title": "Plan",
                    "row_count": 100,
                    "column_count": 12,
                }
            ],
        },
        "spreadsheet": {
            "spreadsheet_id": "sheet-1",
            "title": "Quarterly plan",
            "web_url": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
            "tabs": [
                {
                    "ref": f"{ref}:tab:7",
                    "sheet_id": 7,
                    "title": "Plan",
                    "row_count": 100,
                    "column_count": 12,
                }
            ],
        },
        "values": {
            "ranges": [
                {
                    "range": "Plan!A1:B2",
                    "values": [["Header", "PRIVATE-CELL-VALUE"], [1, 2]],
                }
            ],
            "range_count": 1,
            "row_count": 2,
            "cell_count": 4,
        },
        "materialization": {
            "values_materialized": True,
            "complete_values": True,
        },
    }
    logical_path = "conv:fi:conv_1.turn_1.named_services/sheets/sheet-1.sheets.json"
    physical_path = "turn_1/attachments/named_services/sheets/sheet-1.sheets.json"

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=BLOCK_PRODUCE,
            namespace="sheets",
            object_ref=ref,
            payload={
                "target": {
                    "turn_id": "turn_1",
                    "tool_call_id": "tc_1",
                    "tool_id": "react.read",
                    "logical_path": logical_path,
                    "raw": {
                        "text": json.dumps(snapshot),
                        "mime": SHEETS_SNAPSHOT_MEDIA_TYPE,
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
    assert "[SHEETS SNAPSHOT]" in block["text"]
    assert "Quarterly plan" in block["text"]
    assert "Plan!A1:B2 (2 rows, 4 cells)" in block["text"]
    assert "values.ranges[].values" in block["text"]
    assert logical_path in block["text"]
    assert physical_path in block["text"]
    assert "PRIVATE-CELL-VALUE" not in block["text"]
    assert "react.rg" not in block["text"]
    assert "react.read" not in block["text"]
    assert "next:" not in block["text"]
    assert fake.calls == []


@pytest.mark.anyio
async def test_block_produce_is_client_agnostic_for_ranged_targets() -> None:
    fake = _FakeSheets()
    ref = "sheets:google:account-1:spreadsheet:sheet-1"
    snapshot = {
        "schema": SHEETS_SNAPSHOT_SCHEMA,
        "object_ref": ref,
        "object_kind": SHEETS_SPREADSHEET_KIND,
        "object": {
            "ref": ref,
            "object_kind": SHEETS_SPREADSHEET_KIND,
            "spreadsheet_id": "sheet-1",
            "title": "Quarterly plan",
        },
        "spreadsheet": {
            "spreadsheet_id": "sheet-1",
            "title": "Quarterly plan",
            "tabs": [],
        },
        "values": {
            "ranges": [],
            "range_count": 0,
            "row_count": 0,
            "cell_count": 0,
        },
        "materialization": {
            "values_materialized": True,
            "complete_values": True,
        },
    }

    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=BLOCK_PRODUCE,
            namespace="sheets",
            object_ref=ref,
            payload={
                "target": {
                    "raw": {"text": json.dumps(snapshot)},
                    "meta": {
                        "read_range": {
                            "range_kind": "lines",
                            "line_start": 42,
                            "line_end": 42,
                        }
                    },
                }
            },
        ),
    )

    assert response.ok is True
    assert len(response.extra["blocks"]) == 1
    block = response.extra["blocks"][0]
    assert "[SHEETS SNAPSHOT]" in block["text"]
    assert "react.rg" not in block["text"]
    assert "react.read" not in block["text"]
    assert fake.calls == []


@pytest.mark.anyio
async def test_upsert_create_and_tab_update_reuse_google_service() -> None:
    fake = _FakeSheets()
    provider = _provider(fake)

    created = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="sheets",
            object={"title": "Created from named services"},
        ),
    )
    assert created.ok is True
    assert created.object_ref == (
        "sheets:google:account-1:spreadsheet:created-1"
    )

    updated = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_UPSERT,
            namespace="sheets",
            object_ref="sheets:google:account-1:spreadsheet:sheet-1:tab:7",
            object={"title": "Updated"},
        ),
    )
    assert updated.ok is True
    assert updated.object_ref.endswith(":tab:7")
    assert fake.calls[0]["claim"] == (SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM)
    assert fake.calls[1]["payload"]["sheet_id"] == 7


@pytest.mark.anyio
async def test_actions_and_delete_require_explicit_refs() -> None:
    fake = _FakeSheets()
    provider = _provider(fake)
    spreadsheet = "sheets:google:account-1:spreadsheet:sheet-1"
    tab = f"{spreadsheet}:tab:7"

    appended = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="sheets",
            object_ref=spreadsheet,
            action=ACTION_APPEND_ROWS,
            payload={"range": "Plan!A:C", "rows": [[1, 2, 3]]},
        ),
    )
    assert appended.ok is True
    assert fake.calls[-1]["operation"] == ACTION_APPEND_ROWS

    deleted = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_DELETE,
            namespace="sheets",
            object_ref=tab,
        ),
    )
    assert deleted.ok is True
    assert fake.calls[-1]["operation"] == ACTION_DELETE_TAB
    assert fake.calls[-1]["payload"]["sheet_id"] == 7
    assert deleted.object_ref == tab

    refused = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_DELETE,
            namespace="sheets",
            object_ref=spreadsheet,
        ),
    )
    assert refused.ok is False
    assert refused.error.code == "sheets_spreadsheet_delete_not_supported"


@pytest.mark.anyio
async def test_connected_account_consent_error_is_preserved() -> None:
    fake = _FakeSheets()
    fake.error = {
        "ok": False,
        "error": {
            "code": "needs_connected_account_consent",
            "message": "Approve spreadsheet access.",
        },
        "consent": {
            "reason": "claim_upgrade_required",
            "provider_id": "google",
            "claims": [SHEETS_READ_CLAIM],
            "url": "https://runtime.test/connections",
            "retry_hint": True,
        },
    }
    response = await _provider(fake).dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="sheets",
            query="plan",
        ),
    )
    assert response.ok is False
    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    assert response.error.details["reason"] == "claim_upgrade_required"
    assert response.error.details["claims"] == [SHEETS_READ_CLAIM]
    assert response.error.details["connection_hub_url"] == (
        "https://runtime.test/connections"
    )


@pytest.mark.anyio
async def test_canonical_fields_and_ref_boundaries_cannot_be_overridden() -> None:
    fake = _FakeSheets()

    async def _malicious_search(**kwargs: Any) -> dict[str, Any]:
        fake.calls.append(dict(kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": "account-1",
                "items": [
                    {
                        "spreadsheet_id": "sheet-1",
                        "ref": "mail:message:not-a-sheet",
                        "object_kind": "wrong.kind",
                        "account_id": "wrong-account",
                    }
                ],
            },
        }

    provider = SheetsNamedServiceProvider(execute_operation=_malicious_search)
    response = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace="sheets",
            query="",
            filters={"account_id": "account-1"},
        ),
    )
    assert response.ok is True
    assert response.items[0]["ref"] == (
        "sheets:google:account-1:spreadsheet:sheet-1"
    )
    assert response.items[0]["object_kind"] == "sheets.spreadsheet"
    assert response.items[0]["account_id"] == "account-1"


@pytest.mark.anyio
async def test_tab_range_read_and_unknown_provider_fail_before_google_call() -> None:
    fake = _FakeSheets()
    provider = _provider(fake)

    tab_ranges = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace="sheets",
            object_ref="sheets:google:account-1:spreadsheet:sheet-1:tab:7",
            filters={"ranges": ["Plan!A1:B2"]},
        ),
    )
    assert tab_ranges.ok is False
    assert tab_ranges.error.code == "sheets_spreadsheet_ref_required_for_ranges"

    unsupported = await provider.dispatch(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace="sheets",
            object_ref="sheets:excel:account-1:spreadsheet:sheet-1",
            action=ACTION_APPEND_ROWS,
            payload={"range": "Plan!A:C", "rows": [[1, 2, 3]]},
        ),
    )
    assert unsupported.ok is False
    assert unsupported.error.code == "sheets_provider_not_implemented"
    assert fake.calls == []



def test_every_action_speaks_to_a_person_as_well_as_to_an_agent() -> None:
    from kdcube_ai_app.apps.chat.sdk.integrations.sheets.named_service import (
        SHEETS_ACTIONS,
        SHEETS_PRESENTATION,
        SHEETS_SCHEMA,
    )

    presentation = SHEETS_PRESENTATION["actions"]
    for action in SHEETS_ACTIONS:
        entry = presentation.get(action)
        assert entry, f"{action} has no presentation entry"
        label, described = entry["label"], entry["description"]
        assert label and not label.endswith("."), f"{action} label reads oddly"
        # A consent card shows this text; the schema is written for the agent.
        schema_text = (SHEETS_SCHEMA["actions"].get(action) or {}).get("description")
        assert described != schema_text, f"{action} shows its schema text to a person"
        assert len(described) <= 120, f"{action} reads as an instruction, not a line"
