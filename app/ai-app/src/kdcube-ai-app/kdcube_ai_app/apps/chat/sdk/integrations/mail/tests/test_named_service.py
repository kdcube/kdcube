from __future__ import annotations

import base64
import json
import pathlib
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, ExternalEventRouting
from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace import artifact_outdir_for

from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
    ACTION_DOWNLOAD_ATTACHMENTS,
    ACTION_FORWARD,
    ACTION_SEND,
    MAIL_NAMESPACE,
    MailNamedServiceProvider,
    account_ref,
    attachment_ref,
    message_ref,
    parse_mail_ref,
)
from connection_hub.delegated_to_kdcube.models import (
    REASON_ACCOUNT_REQUIRED,
    REASON_CLAIM_UPGRADE_REQUIRED,
    REASON_CONNECT_REQUIRED,
    REASON_RECONNECT_REQUIRED,
    ClaimResolution,
    ConnectedAccount,
    account_choice,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    OBJECT_ACTION,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_SCHEMA,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
)


def _ctx() -> NamedServiceContext:
    return NamedServiceContext(tenant="demo", project="project", user_id="user-1")


def _account(account_id: str, *claims: str, metadata: dict[str, Any] | None = None) -> ConnectedAccount:
    return ConnectedAccount(
        account_id=account_id,
        provider_id="google",
        connector_app_id="gmail",
        external_subject=f"google:{account_id}",
        email=f"{account_id}@example.test",
        display_name=f"Account {account_id}",
        claims=claims,
        credential_id=f"cred-{account_id}",
        metadata=dict(metadata or {}),
    )


class _FakeGmail:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search_gmail(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search_gmail", kwargs))
        account_id = kwargs["account_id"]
        return {
            "ok": True,
            "ret": {
                "account_id": account_id,
                "messages": [
                    {
                        "id": f"msg-{account_id}",
                        "thread_id": f"thread-{account_id}",
                        "subject": f"Receipt {account_id}",
                        "from": "billing@example.test",
                        "date": "Mon, 6 Jul 2026 10:00:00 +0000",
                        "snippet": "receipt",
                    }
                ],
            },
        }

    async def read_gmail_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("read_gmail_message", kwargs))
        return {
            "ok": True,
            "ret": {
                "id": kwargs["message_id"],
                "thread_id": "thread-1",
                "account_id": kwargs["account_id"],
                "subject": "Receipt",
                "from": "billing@example.test",
                "date": "Mon, 6 Jul 2026 10:00:00 +0000",
                "snippet": "receipt",
                "body_text": "body",
                # attachment_id rotates on every Gmail fetch; part_id is stable
                "attachments": [{"attachment_id": f"att-rotating-{id(self)}", "part_id": "1", "filename": "invoice.pdf"}],
            },
        }

    async def download_gmail_attachments(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("download_gmail_attachments", kwargs))
        return {"ok": True, "ret": {"files": [{"logical_path": "conv:fi:turn/files/invoice.pdf"}]}}

    async def send_gmail(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send_gmail", kwargs))
        return {
            "ok": True,
            "ret": {
                "id": "sent-1",
                "thread_id": "sent-thread",
                "account_id": kwargs["account_id"],
                "subject": kwargs["subject"],
                "snippet": "",
            },
        }

    async def forward_gmail_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("forward_gmail_message", kwargs))
        return {
            "ok": True,
            "ret": {
                "id": "fwd-1",
                "thread_id": "fwd-thread",
                "account_id": kwargs["account_id"],
                "subject": "Fwd",
                "snippet": "",
            },
        }


def _url_factory(ctx: Any, info: dict[str, Any]) -> dict[str, Any]:
    del ctx
    return {"url": f"https://runtime.test/download?ref={info['ref']}", "expires_at": 1900000000}


class _Provider(MailNamedServiceProvider):
    def __init__(
        self,
        accounts: list[ConnectedAccount] | None = None,
        resolution: ClaimResolution | None = None,
        file_url_factory: Any = None,
    ) -> None:
        super().__init__(entrypoint=None, bundle_id="kdcube-services@1-0", file_url_factory=file_url_factory)
        self.accounts = list(accounts or [])
        self._stub_resolution = resolution
        self._gmail = _FakeGmail()

    async def _gmail_accounts(self, ctx: NamedServiceContext, *, claim: str = "") -> list[ConnectedAccount]:
        del ctx
        return [account for account in self.accounts if not claim or account.allows(claim)]

    async def _resolve_claim(self, ctx: NamedServiceContext, *, claim: str, account_id: str = "") -> ClaimResolution:
        if self._stub_resolution is not None:
            return self._stub_resolution
        return await super()._resolve_claim(ctx, claim=claim, account_id=account_id)


def test_mail_refs_are_provider_neutral():
    assert account_ref("gmail", "acc-1") == "mail:gmail:acc-1"
    assert message_ref("gmail", "acc-1", "msg-1") == "mail:gmail:acc-1:message:msg-1"
    assert (
        attachment_ref("gmail", "acc-1", "msg-1", "att:1")
        == "mail:gmail:acc-1:attachment:msg-1:att:1"
    )
    assert parse_mail_ref("mail:gmail:acc-1") == {
        "provider": "gmail",
        "account_id": "acc-1",
        "kind": "account",
    }
    assert parse_mail_ref("mail:gmail:acc-1:message:msg:1") == {
        "provider": "gmail",
        "account_id": "acc-1",
        "kind": "message",
        "message_id": "msg:1",
    }


@pytest.mark.asyncio
async def test_about_capabilities_and_schema_expose_mail_contract():
    provider = _Provider()

    about = await provider.provider_about(_ctx(), NamedServiceRequest(operation=PROVIDER_ABOUT, namespace=MAIL_NAMESPACE))
    capabilities = await provider.provider_capabilities(
        _ctx(),
        NamedServiceRequest(operation=PROVIDER_CAPABILITIES, namespace=MAIL_NAMESPACE),
    )
    schema = await provider.object_schema(_ctx(), NamedServiceRequest(operation=OBJECT_SCHEMA, namespace=MAIL_NAMESPACE))

    assert about.ok is True
    assert "object.list" in about.ret["extra"]["workflow"][0]
    assert capabilities.ret["attrs"]["capabilities"]["actions"] == [
        ACTION_DOWNLOAD_ATTACHMENTS,
        ACTION_SEND,
        ACTION_FORWARD,
        "request_upload",
        "discard_upload",
    ]
    assert schema.ret["extra"]["schema"]["namespace"] == MAIL_NAMESPACE
    assert schema.ret["extra"]["schema"]["refs"]["message"] == "mail:<provider>:<account_id>:message:<message_id>"


@pytest.mark.asyncio
async def test_object_list_returns_connected_mail_accounts():
    provider = _Provider(
        [
            _account("acc-1", "gmail:read"),
            _account("acc-2", "gmail:send", metadata={"credential_status": "reconnect_required"}),
        ]
    )

    response = await provider.object_list(_ctx(), NamedServiceRequest(operation=OBJECT_LIST, namespace=MAIL_NAMESPACE))

    assert response.ok is True
    assert [item["ref"] for item in response.ret["items"]] == ["mail:gmail:acc-1", "mail:gmail:acc-2"]
    assert response.ret["items"][0]["email"] == "acc-1@example.test"
    assert response.ret["items"][0]["label"] == "Account acc-1"
    assert response.ret["items"][0]["credential_status"] == "active"
    assert response.ret["items"][1]["credential_status"] == "reconnect_required"


@pytest.mark.asyncio
async def test_object_list_without_accounts_carries_connect_hint():
    provider = _Provider([])

    response = await provider.object_list(_ctx(), NamedServiceRequest(operation=OBJECT_LIST, namespace=MAIL_NAMESPACE))

    assert response.ok is True
    assert response.ret["items"] == []
    consent = response.ret["extra"]["consent"]
    assert consent["reason"] == REASON_CONNECT_REQUIRED
    assert consent["retry_hint"] is True
    assert consent["url"].startswith("/api/integrations/bundles/demo/project/")


@pytest.mark.asyncio
async def test_search_fans_out_to_readable_gmail_accounts():
    provider = _Provider([_account("acc-1", "gmail:read"), _account("acc-2", "gmail:read"), _account("send-only", "gmail:send")])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=MAIL_NAMESPACE, query="receipt", limit=5),
    )

    assert response.ok is True
    assert [item["ref"] for item in response.ret["items"]] == [
        "mail:gmail:acc-1:message:msg-acc-1",
        "mail:gmail:acc-2:message:msg-acc-2",
    ]
    assert [call[1]["account_id"] for call in provider._gmail.calls] == ["acc-1", "acc-2"]
    assert [(item["account_id"], item["account_label"]) for item in response.ret["items"]] == [
        ("acc-1", "Account acc-1"),
        ("acc-2", "Account acc-2"),
    ]


@pytest.mark.asyncio
async def test_search_fan_out_respects_the_agent_account_binding():
    # Parity with the send path: a read fan-out must be limited to the accounts
    # THIS agent is bound to. Two readable accounts, but the agent is bound to
    # acc-1 only -> acc-2 is never searched (it once was — a per-account leak).
    from connection_hub.agent_account_scope import (
        clear_agent_account_scope,
        set_agent_account_scope,
    )

    provider = _Provider([_account("acc-1", "gmail:read"), _account("acc-2", "gmail:read")])
    set_agent_account_scope({"google": {"acc-1": ["gmail:read"]}})
    try:
        response = await provider.object_search(
            _ctx(),
            NamedServiceRequest(operation=OBJECT_SEARCH, namespace=MAIL_NAMESPACE, query="receipt", limit=5),
        )
    finally:
        clear_agent_account_scope()

    assert response.ok is True
    assert [item["ref"] for item in response.ret["items"]] == ["mail:gmail:acc-1:message:msg-acc-1"]
    assert [call[1]["account_id"] for call in provider._gmail.calls] == ["acc-1"]


@pytest.mark.asyncio
async def test_search_with_explicit_account_id_targets_only_that_account():
    provider = _Provider([_account("acc-1", "gmail:read"), _account("acc-2", "gmail:read")])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace=MAIL_NAMESPACE,
            query="receipt",
            limit=5,
            filters={"account_id": "acc-2"},
        ),
    )

    assert response.ok is True
    assert [call[1]["account_id"] for call in provider._gmail.calls] == ["acc-2"]
    assert response.ret["items"][0]["account_id"] == "acc-2"
    assert response.ret["items"][0]["account_label"] == "Account acc-2"


@pytest.mark.asyncio
async def test_search_cursor_is_forwarded_and_returned_for_one_account():
    provider = _Provider([_account("acc-1", "gmail:read")])

    async def _search(**kwargs: Any) -> dict[str, Any]:
        provider._gmail.calls.append(("search_gmail", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "messages": [],
                "next_cursor": "gmail-page-3",
            },
        }

    provider._gmail.search_gmail = _search
    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace=MAIL_NAMESPACE,
            query="receipt",
            cursor="gmail-page-2",
            filters={"account_id": "acc-1"},
        ),
    )

    assert provider._gmail.calls[0][1]["cursor"] == "gmail-page-2"
    assert response.ret["attrs"]["next_cursor"] == "gmail-page-3"


@pytest.mark.asyncio
async def test_search_without_readable_account_returns_connect_required():
    provider = _Provider([])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=MAIL_NAMESPACE, query="receipt", limit=5),
    )

    assert response.ok is False
    assert response.status == 403
    assert response.error is not None
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_CONNECT_REQUIRED
    assert details["retry_hint"] is True
    assert details["provider_id"] == "google"
    assert details["connector_app_id"] == ""  # no service declaration bound -> provider-wide
    assert details["claims"] == ["gmail:read"]
    assert details["connection_hub_url"].startswith("/api/integrations/bundles/demo/project/")
    assert details["consent"]["kind"] == "delegated_to_kdcube.connected_account"


@pytest.mark.asyncio
async def test_search_with_unapproved_claim_returns_claim_upgrade_with_candidates():
    send_only = _account("acc-1", "gmail:send")
    provider = _Provider(
        [send_only],
        resolution=ClaimResolution(
            ok=False,
            provider_id="google",
            claim="gmail:read",
            connector_app_id="gmail",
            error=REASON_CLAIM_UPGRADE_REQUIRED,
            message="Approve gmail:read for your connected Google account.",
            candidates=(account_choice(send_only),),
            retry_hint=True,
        ),
    )

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=MAIL_NAMESPACE, query="receipt", limit=5),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_CLAIM_UPGRADE_REQUIRED
    assert details["retry_hint"] is True
    assert details["candidates"][0]["account_id"] == "acc-1"
    assert details["candidates"][0]["label"] == "Account acc-1"


@pytest.mark.asyncio
async def test_search_with_broken_credential_returns_reconnect_payload():
    provider = _Provider(
        [],
        resolution=ClaimResolution(
            ok=False,
            provider_id="google",
            claim="gmail:read",
            connector_app_id="gmail",
            account_id="acc-1",
            error=REASON_RECONNECT_REQUIRED,
            message="The connected account authorization expired and could not be refreshed.",
            retry_hint=True,
        ),
    )

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace=MAIL_NAMESPACE,
            query="receipt",
            filters={"account_id": "acc-1"},
        ),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_RECONNECT_REQUIRED
    assert details["retry_hint"] is True
    assert details["account_id"] == "acc-1"
    assert details["connection_hub_url"]


@pytest.mark.asyncio
async def test_send_ambiguity_passes_account_required_candidates_through():
    provider = _Provider([_account("acc-1", "gmail:send"), _account("acc-2", "gmail:send")])
    consent = {
        "kind": "delegated_to_kdcube.connected_account",
        "reason": REASON_ACCOUNT_REQUIRED,
        "retry_hint": True,
        "provider_id": "google",
        "connector_app_id": "gmail",
        "claims": ["gmail:send"],
        "account_id": "",
        "candidates": [
            {"account_id": "acc-1", "label": "Account acc-1"},
            {"account_id": "acc-2", "label": "Account acc-2"},
        ],
        "url": "/api/integrations/bundles/demo/project/connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube",
        "action_label": "Choose account",
    }

    async def _ambiguous_send(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "ok": False,
            "error": {
                "code": "needs_connected_account_consent",
                "message": "Several connected accounts can satisfy this claim; choose an account_id.",
                "consent": consent,
            },
            "consent": consent,
            "ret": {"ok": False},
        }

    provider._gmail.send_gmail = _ambiguous_send

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            action=ACTION_SEND,
            payload={"to": "user@example.test", "subject": "Hello", "body_markdown": "Body"},
        ),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_ACCOUNT_REQUIRED
    assert details["retry_hint"] is True
    assert [item["label"] for item in details["candidates"]] == ["Account acc-1", "Account acc-2"]
    assert details["connection_hub_url"] == consent["url"]


@pytest.mark.asyncio
async def test_get_reads_gmail_message_and_decorates_attachment_refs():
    provider = _Provider([_account("acc-1", "gmail:read")])

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1:message:msg-1",
        ),
    )

    assert response.ok is True
    obj = response.ret["object"]
    assert obj["ref"] == "mail:gmail:acc-1:message:msg-1"
    assert obj["account_label"] == "Account acc-1"
    # decorated refs use the STABLE part id, never the rotating attachment id
    assert obj["attachments"][0]["ref"] == "mail:gmail:acc-1:attachment:msg-1:1"


@pytest.mark.asyncio
async def test_turnless_message_get_exposes_complete_live_snapshot_url():
    provider = _Provider(
        [_account("acc-1", "gmail:read")], file_url_factory=_url_factory
    )
    ref = "mail:gmail:acc-1:message:msg-1"

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=MAIL_NAMESPACE,
            object_ref=ref,
        ),
    )

    snapshot = response.ret["object"]["snapshot"]
    assert snapshot["complete_body"] is True
    assert snapshot["schema"] == "kdcube.mail.message.snapshot.v1"
    assert snapshot["download"]["url"] == f"https://runtime.test/download?ref={ref}"
    assert response.ret["object"]["body_text"] == "body"


@pytest.mark.asyncio
async def test_message_pull_streams_complete_snapshot_for_materialization(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service as mail_service

    provider = _Provider([_account("acc-1", "gmail:read")])
    provider._entrypoint = object()

    async def _chunks():
        yield b'{"schema":"kdcube.mail.message.snapshot.v1",'
        yield b'"message":{"body_text":"complete"}}'

    async def _fetch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "chunks": _chunks(),
            "filename": "gmail-msg-1.message.json",
            "mime_type": "application/vnd.kdcube.mail.message.snapshot+json;version=1",
            "status": 200,
        }

    monkeypatch.setattr(mail_service, "fetch_mail_message_snapshot", _fetch)
    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1:message:msg-1",
            response_mode="stream",
            context={"source": "react.pull", "materialize": True},
        ),
    )

    assert response.response.ok is True
    assert response.response.ret["attrs"]["materialization"]["complete"] is True
    assert b"complete" in b"".join([chunk async for chunk in response.chunks])


@pytest.mark.asyncio
async def test_get_attachment_ref_returns_download_url():
    provider = _Provider([_account("acc-1", "gmail:read")], file_url_factory=_url_factory)
    ref = "mail:gmail:acc-1:attachment:msg-1:1"

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace=MAIL_NAMESPACE, object_ref=ref),
    )

    assert response.ok is True
    obj = response.ret["object"]
    assert obj["object_kind"] == "mail.attachment"
    assert obj["filename"] == "invoice.pdf"
    assert obj["download"]["encoding"] == "url"
    assert obj["download"]["url"] == f"https://runtime.test/download?ref={ref}"
    assert obj["download"]["expires_at"] == 1900000000


@pytest.mark.asyncio
async def test_get_attachment_ref_without_delivery_path_reports_none():
    provider = _Provider([_account("acc-1", "gmail:read")])

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1:attachment:msg-1:1",
        ),
    )

    assert response.ok is True
    assert response.ret["object"]["download"]["encoding"] == "none"


@pytest.mark.asyncio
async def test_download_attachments_falls_back_to_urls_without_workspace():
    provider = _Provider([_account("acc-1", "gmail:read")], file_url_factory=_url_factory)

    async def _no_workspace(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "ok": False,
            "error": {
                "code": "artifact_workspace_unavailable",
                "message": "Current ReAct turn id or artifact workspace is unavailable.",
            },
            "ret": {},
        }

    provider._gmail.download_gmail_attachments = _no_workspace

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1:message:msg-1",
            action=ACTION_DOWNLOAD_ATTACHMENTS,
        ),
    )

    assert response.ok is True
    items = response.ret["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "invoice.pdf"
    assert items[0]["ref"] == "mail:gmail:acc-1:attachment:msg-1:1"
    assert items[0]["download"]["encoding"] == "url"
    assert response.ret["extra"]["delivery"] == "url"


@pytest.mark.asyncio
async def test_send_with_inline_attachments_stages_bytes_in_ephemeral_workspace():
    provider = _Provider([_account("acc-1", "gmail:send")])
    captured: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> dict[str, Any]:
        outdir = str(run_ctx.OUTDIR_CV.get("") or "")
        captured["outdir"] = outdir
        paths = json.loads(kwargs["attachment_paths"])
        captured["paths"] = paths
        root = artifact_outdir_for(pathlib.Path(outdir), create=False)
        captured["staged_bytes"] = (root / paths[0]).read_bytes()
        return {"ok": True, "ret": {"id": "sent-1", "account_id": kwargs["account_id"], "subject": kwargs["subject"], "snippet": ""}}

    provider._gmail.send_gmail = _send
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )

    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            _ctx(),
            NamedServiceRequest(
                operation=OBJECT_ACTION,
                namespace=MAIL_NAMESPACE,
                action=ACTION_SEND,
                payload={
                    "to": "user@example.test",
                    "subject": "Hello",
                    "body_markdown": "Body",
                    "attachments": [
                        {"filename": "logo.png", "content_base64": base64.b64encode(b"png-bytes").decode()}
                    ],
                },
            ),
        )

    assert response.ok is True
    assert captured["staged_bytes"] == b"png-bytes"
    assert captured["paths"][0].endswith("/logo.png")
    # the disposable workspace is unbound and removed after the call
    assert str(run_ctx.OUTDIR_CV.get("") or "") == ""
    assert not pathlib.Path(captured["outdir"]).exists()


@pytest.mark.asyncio
async def test_send_with_string_attachment_entries_fails_loudly():
    # The surfaced case: a caller passed file refs as plain strings in
    # `attachments`. The mail must NOT go out bare with an ok status — the
    # action fails and the error message carries the accepted entry forms.
    provider = _Provider([_account("acc-1", "gmail:send")])
    called: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> dict[str, Any]:
        called["send"] = True
        return {"ok": True, "ret": {"id": "sent-1", "account_id": kwargs["account_id"], "subject": "", "snippet": ""}}

    provider._gmail.send_gmail = _send

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            action=ACTION_SEND,
            payload={
                "to": "user@example.test",
                "subject": "Diagram",
                "body_markdown": "Body",
                "attachments": [
                    "conv:fi:turn_2026-07-09.user.attachments/named_services/task/abc/diagram.svg"
                ],
            },
        ),
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "mail_attachment_entry_invalid"
    assert "staged_ref" in response.error.message
    assert "attachment_paths" in response.error.message
    assert "send" not in called


@pytest.mark.asyncio
async def test_send_with_unrecognized_attachment_object_fails_loudly():
    # The surfaced case: {"file_ref": <conv path>} — an object entry that
    # carries no bytes source. The contract error must name the accepted
    # forms and point workspace files at attachment_paths (previously this
    # fell through to inline handling and died on a misleading
    # "plain filename" message).
    provider = _Provider([_account("acc-1", "gmail:send")])
    called: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> dict[str, Any]:
        called["send"] = True
        return {"ok": True, "ret": {"id": "sent-1", "account_id": kwargs["account_id"], "subject": "", "snippet": ""}}

    provider._gmail.send_gmail = _send

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            action=ACTION_SEND,
            payload={
                "to": "user@example.test",
                "subject": "Diagram",
                "body_markdown": "Body",
                "attachments": [
                    {"file_ref": "conv:fi:turn_2026-07-10.user.attachments/named_services/task/abc/diagram.svg"}
                ],
            },
        ),
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "mail_attachment_entry_invalid"
    assert "attachment_paths" in response.error.message
    assert "send" not in called


@pytest.mark.asyncio
async def test_request_upload_returns_slot_and_send_consumes_staged_ref(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import new_staged_ref, save_staged

    async def _slot_factory(ctx: Any, info: dict[str, Any]) -> dict[str, Any]:
        del ctx
        return {
            "upload_url": f"https://runtime.test/upload?name={info['filename']}",
            "staged_ref": new_staged_ref(info["filename"]),
            "expires_at": 1900000000,
            "max_bytes": 25 * 1024 * 1024,
        }

    provider = _Provider([_account("acc-1", "gmail:send")])
    provider._upload_slot_factory = _slot_factory
    provider._staging_root = lambda: tmp_path

    slot = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1",
            action="request_upload",
            payload={"filename": "logo.png"},
        ),
    )
    assert slot.ok is True
    staged_ref = slot.ret["extra"]["staged_ref"]
    assert staged_ref.startswith("staged:")
    assert slot.ret["extra"]["upload_url"].startswith("https://runtime.test/upload")

    # the client PUTs bytes to upload_url out-of-band; simulate the route's write
    save_staged(tmp_path, staged_ref, b"png-bytes", owner=_ctx())

    captured: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> dict[str, Any]:
        paths = json.loads(kwargs["attachment_paths"])
        outdir = str(run_ctx.OUTDIR_CV.get("") or "")
        root = artifact_outdir_for(pathlib.Path(outdir), create=False)
        captured["staged_bytes"] = (root / paths[0]).read_bytes()
        return {"ok": True, "ret": {"id": "sent-1", "account_id": kwargs["account_id"], "subject": kwargs["subject"], "snippet": ""}}

    provider._gmail.send_gmail = _send
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )
    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            _ctx(),
            NamedServiceRequest(
                operation=OBJECT_ACTION,
                namespace=MAIL_NAMESPACE,
                action=ACTION_SEND,
                payload={
                    "to": "user@example.test",
                    "subject": "Hello",
                    "attachments": [{"staged_ref": staged_ref}],
                },
            ),
        )

    assert response.ok is True
    assert captured["staged_bytes"] == b"png-bytes"
    # staged file is single-use: consumed and deleted after a successful send
    staged_id = staged_ref.split(":")[1]
    assert not (tmp_path / staged_id).exists()


@pytest.mark.asyncio
async def test_send_with_unknown_staged_ref_fails_before_provider_call(tmp_path):
    provider = _Provider([_account("acc-1", "gmail:send")])
    provider._staging_root = lambda: tmp_path

    async def _send(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("send must not be called for unknown staged refs")

    provider._gmail.send_gmail = _send

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            action=ACTION_SEND,
            payload={
                "to": "user@example.test",
                "attachments": [{"staged_ref": "staged:deadbeef:missing.pdf"}],
            },
        ),
    )

    assert response.ok is False
    assert response.status == 400
    assert response.error.code == "mail_inline_files_invalid"
    assert "staged" in response.error.message


@pytest.mark.asyncio
async def test_send_with_invalid_inline_attachment_fails_whole_action():
    provider = _Provider([_account("acc-1", "gmail:send")])

    async def _send(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("send must not be called for invalid inline files")

    provider._gmail.send_gmail = _send

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            action=ACTION_SEND,
            payload={
                "to": "user@example.test",
                "attachments": [{"filename": "x.bin", "content_base64": "not-base64!!"}],
            },
        ),
    )

    assert response.ok is False
    assert response.status == 400
    assert response.error.code == "mail_inline_files_invalid"


@pytest.mark.asyncio
async def test_actions_dispatch_to_gmail_transport():
    provider = _Provider([_account("acc-1", "gmail:read", "gmail:send")])
    message = "mail:gmail:acc-1:message:msg-1"

    downloaded = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            object_ref=message,
            action=ACTION_DOWNLOAD_ATTACHMENTS,
            payload={"attachment_ids": ["att-1"]},
        ),
    )
    sent = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            object_ref="mail:gmail:acc-1",
            action=ACTION_SEND,
            payload={"to": "user@example.test", "subject": "Hello", "body_markdown": "Body"},
        ),
    )
    forwarded = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=MAIL_NAMESPACE,
            object_ref=message,
            action=ACTION_FORWARD,
            payload={"to": "user@example.test", "note_markdown": "FYI"},
        ),
    )

    assert downloaded.ok is True
    assert sent.ret["object"]["ref"] == "mail:gmail:acc-1:message:sent-1"
    assert forwarded.ret["object"]["ref"] == "mail:gmail:acc-1:message:fwd-1"
    assert [name for name, _ in provider._gmail.calls] == [
        "download_gmail_attachments",
        "send_gmail",
        "forward_gmail_message",
    ]


@pytest.mark.asyncio
async def test_schema_teaches_ref_attachments_to_in_chat_agents(monkeypatch):
    """Regression: the agent-facing send contract advertised inline
    {filename, content_base64} attachments, so the model base64-encoded a
    pulled file into its own context to attach it. In a chat turn the schema
    must teach the ref forms only — attachment_paths / staged_ref — and stop
    offering content_base64."""
    import kdcube_ai_app.apps.chat.sdk.integrations.inline_files as inline_files

    monkeypatch.setattr(inline_files, "has_turn_workspace", lambda: True)
    provider = _Provider()
    schema = await provider.object_schema(
        _ctx(), NamedServiceRequest(operation=OBJECT_SCHEMA, namespace=MAIL_NAMESPACE)
    )
    actions = schema.ret["extra"]["schema"]["actions"]

    for action in (ACTION_SEND, ACTION_FORWARD):
        description = actions[action]["description"]
        assert "attachment_paths" in description
        assert "staged_ref" in description
        assert "content_base64" not in description
    assert actions[ACTION_SEND]["description"].count("conv:fi:") >= 1
    upload = actions["request_upload"]["description"]
    assert "attachment_paths" in upload  # workspace files skip the upload path


@pytest.mark.asyncio
async def test_schema_keeps_staged_and_inline_forms_for_turnless_clients(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.integrations.inline_files as inline_files

    monkeypatch.setattr(inline_files, "has_turn_workspace", lambda: False)
    provider = _Provider()
    schema = await provider.object_schema(
        _ctx(), NamedServiceRequest(operation=OBJECT_SCHEMA, namespace=MAIL_NAMESPACE)
    )
    send = schema.ret["extra"]["schema"]["actions"][ACTION_SEND]["description"]

    assert "staged_ref" in send
    # inline stays available for byte-holding clients, marked last resort
    assert "content_base64" in send
    assert "last resort" in send


def test_registered_spec_carries_connected_accounts_requirement():
    """The spec builder used at registration - not the decorator metadata - is
    what discovery reads for proactive consent, the capability picker, and
    connect-first demand ordering. It must carry connected_accounts, or an
    account-backed realm silently falls back to the agent-grant view instead
    of leading with connect (the mail regression 2026-07-26)."""
    from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
        mail_named_service_spec,
    )

    ca = mail_named_service_spec().metadata.get("connected_accounts")
    assert ca, "mail registered spec must declare connected_accounts"
    assert ca[0]["provider_id"] == "google"
    assert "claims_by_operation" in ca[0]
    assert ca[0]["claims_by_operation"]["object.action.request_upload"] == [
        "gmail:send"
    ]
    metadata = mail_named_service_spec().metadata
    assert metadata["canonical_refs"]["message"].startswith("mail:<provider>")
    assert metadata["presentation"]["actions"][ACTION_SEND]["label"] == "Send email"
