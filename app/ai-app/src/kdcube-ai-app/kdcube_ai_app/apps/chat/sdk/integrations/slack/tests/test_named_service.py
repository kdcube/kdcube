from __future__ import annotations

import base64
import pathlib
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, ExternalEventRouting
from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for

from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import (
    ACTION_POST_MESSAGE,
    ACTION_UPLOAD_FILE,
    SLACK_NAMESPACE,
    SlackNamedServiceProvider,
    account_ref,
    channel_ref,
    file_ref,
    message_ref,
    parse_slack_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
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
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
)


def _ctx() -> NamedServiceContext:
    return NamedServiceContext(tenant="demo", project="project", user_id="user-1")


def _account(account_id: str, *claims: str, metadata: dict[str, Any] | None = None) -> ConnectedAccount:
    return ConnectedAccount(
        account_id=account_id,
        provider_id="slack",
        connector_app_id="demo",
        external_subject=f"slack:{account_id}",
        display_name=f"Workspace {account_id}",
        workspace=f"Workspace {account_id}",
        claims=claims,
        credential_id=f"cred-{account_id}",
        metadata=dict(metadata or {}),
    )


class _FakeSlack:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_slack_channels(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_slack_channels", kwargs))
        account_id = kwargs["account_id"]
        return {
            "ok": True,
            "ret": {
                "account_id": account_id,
                "channels": [
                    {
                        "id": "C123",
                        "name": "general",
                        "is_channel": True,
                        "is_private": False,
                        "is_member": True,
                    }
                ],
            },
        }

    async def search_slack(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search_slack", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "messages": [
                    {
                        "channel_id": "C123",
                        "channel_name": "general",
                        "timestamp": "1783000000.000100",
                        "text": "quarterly revenue",
                    }
                ],
            },
        }

    async def read_slack_channel_history(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("read_slack_channel_history", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "channel": kwargs["channel"],
                "messages": [
                    {
                        "timestamp": "1783000000.000100",
                        "user": "U123",
                        "text": "hello",
                        "files": [{"id": "F123", "name": "report.pdf", "size": 10}],
                    }
                ],
            },
        }

    async def download_slack_file(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("download_slack_file", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "file": {"id": kwargs["file_id"], "name": "report.pdf", "mimetype": "application/pdf"},
                "artifact_path": "fi:turn.files/slack/report.pdf",
            },
        }

    async def post_slack_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("post_slack_message", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "channel": kwargs["channel"],
                "message": {"timestamp": "1783000000.000200", "text": kwargs["text"]},
            },
        }

    async def upload_slack_file(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("upload_slack_file", kwargs))
        return {
            "ok": True,
            "ret": {
                "account_id": kwargs["account_id"],
                "file_id": "F-UP",
                "filename": "report.pdf",
                "channel": kwargs["channel"],
            },
        }


def _url_factory(ctx: Any, info: dict[str, Any]) -> dict[str, Any]:
    del ctx
    return {"url": f"https://runtime.test/download?ref={info['ref']}", "expires_at": 1900000000}


class _Provider(SlackNamedServiceProvider):
    def __init__(
        self,
        accounts: list[ConnectedAccount] | None = None,
        resolution: ClaimResolution | None = None,
        file_url_factory: Any = None,
    ) -> None:
        super().__init__(entrypoint=None, bundle_id="kdcube-services@1-0", file_url_factory=file_url_factory)
        self.accounts = list(accounts or [])
        self._stub_resolution = resolution
        self._slack = _FakeSlack()

    async def _slack_accounts(self, ctx: NamedServiceContext, *, claim: str = "") -> list[ConnectedAccount]:
        del ctx
        return [account for account in self.accounts if not claim or account.allows(claim)]

    async def _resolve_claim(self, ctx: NamedServiceContext, *, claim: str, account_id: str = "") -> ClaimResolution:
        if self._stub_resolution is not None:
            return self._stub_resolution
        return await super()._resolve_claim(ctx, claim=claim, account_id=account_id)


def test_slack_refs_are_stable():
    assert account_ref("acc-1") == "slack:acc-1"
    assert channel_ref("acc-1", "C123") == "slack:acc-1:channel:C123"
    assert message_ref("acc-1", "C123", "1783000000.000100") == "slack:acc-1:message:C123:1783000000.000100"
    assert file_ref("acc-1", "F123") == "slack:acc-1:file:F123"
    assert parse_slack_ref("slack:acc-1:channel:C123") == {
        "account_id": "acc-1",
        "kind": "channel",
        "channel_id": "C123",
    }


@pytest.mark.asyncio
async def test_about_capabilities_and_schema_expose_slack_contract():
    provider = _Provider()

    about = await provider.provider_about(_ctx(), NamedServiceRequest(operation=PROVIDER_ABOUT, namespace=SLACK_NAMESPACE))
    capabilities = await provider.provider_capabilities(
        _ctx(),
        NamedServiceRequest(operation=PROVIDER_CAPABILITIES, namespace=SLACK_NAMESPACE),
    )
    schema = await provider.object_schema(_ctx(), NamedServiceRequest(operation=OBJECT_SCHEMA, namespace=SLACK_NAMESPACE))

    assert about.ok is True
    assert "object.list" in about.ret["extra"]["workflow"][0]
    assert ACTION_POST_MESSAGE in capabilities.ret["attrs"]["capabilities"]["actions"]
    assert schema.ret["extra"]["schema"]["refs"]["channel"] == "slack:<account_id>:channel:<channel_id>"


@pytest.mark.asyncio
async def test_object_list_returns_connected_slack_accounts():
    provider = _Provider(
        [
            _account("acc-1", "slack:search"),
            _account("acc-2", "slack:post", metadata={"credential_status": "reconnect_required"}),
        ]
    )

    response = await provider.object_list(_ctx(), NamedServiceRequest(operation=OBJECT_LIST, namespace=SLACK_NAMESPACE))

    assert response.ok is True
    assert [item["ref"] for item in response.ret["items"]] == ["slack:acc-1", "slack:acc-2"]
    assert response.ret["items"][0]["label"] == "Workspace acc-1"
    assert response.ret["items"][0]["credential_status"] == "active"
    assert response.ret["items"][1]["credential_status"] == "reconnect_required"


@pytest.mark.asyncio
async def test_object_list_without_accounts_carries_connect_hint():
    provider = _Provider([])

    response = await provider.object_list(_ctx(), NamedServiceRequest(operation=OBJECT_LIST, namespace=SLACK_NAMESPACE))

    assert response.ok is True
    assert response.ret["items"] == []
    consent = response.ret["extra"]["consent"]
    assert consent["reason"] == REASON_CONNECT_REQUIRED
    assert consent["retry_hint"] is True
    assert consent["url"].startswith("/api/integrations/bundles/demo/project/")


@pytest.mark.asyncio
async def test_object_list_channels_dispatches_to_slack_tool():
    provider = _Provider([_account("acc-1", "slack:channels")])

    response = await provider.object_list(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_LIST,
            namespace=SLACK_NAMESPACE,
            filters={"kind": "channels"},
        ),
    )

    assert response.ok is True
    assert response.ret["items"][0]["ref"] == "slack:acc-1:channel:C123"
    assert provider._slack.calls[0][0] == "list_slack_channels"


@pytest.mark.asyncio
async def test_search_without_searchable_account_returns_connect_required():
    provider = _Provider([])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.ok is False
    assert response.status == 403
    assert response.error is not None
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_CONNECT_REQUIRED
    assert details["retry_hint"] is True
    assert details["provider_id"] == "slack"
    assert details["connector_app_id"] == "demo"
    assert details["claims"] == ["slack:search"]
    assert details["connection_hub_url"].startswith("/api/integrations/bundles/demo/project/")
    assert details["consent"]["kind"] == "delegated_to_kdcube.connected_account"


@pytest.mark.asyncio
async def test_search_with_unapproved_claim_returns_claim_upgrade_with_candidates():
    post_only = _account("acc-1", "slack:post")
    provider = _Provider(
        [post_only],
        resolution=ClaimResolution(
            ok=False,
            provider_id="slack",
            claim="slack:search",
            connector_app_id="demo",
            error=REASON_CLAIM_UPGRADE_REQUIRED,
            message="Approve slack:search for your connected Slack account.",
            candidates=(account_choice(post_only),),
            retry_hint=True,
        ),
    )

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_CLAIM_UPGRADE_REQUIRED
    assert details["retry_hint"] is True
    assert details["candidates"][0]["account_id"] == "acc-1"
    assert details["candidates"][0]["label"] == "Workspace acc-1"


@pytest.mark.asyncio
async def test_search_with_broken_credential_returns_reconnect_payload():
    provider = _Provider(
        [],
        resolution=ClaimResolution(
            ok=False,
            provider_id="slack",
            claim="slack:search",
            connector_app_id="demo",
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
            namespace=SLACK_NAMESPACE,
            query="revenue",
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
async def test_search_dispatches_to_slack_message_search():
    provider = _Provider([_account("acc-1", "slack:search")])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.ok is True
    assert response.ret["items"][0]["ref"] == "slack:acc-1:message:C123:1783000000.000100"
    assert response.ret["items"][0]["account_id"] == "acc-1"
    assert response.ret["items"][0]["account_label"] == "Workspace acc-1"


@pytest.mark.asyncio
async def test_search_with_explicit_account_id_targets_only_that_account():
    provider = _Provider([_account("acc-1", "slack:search"), _account("acc-2", "slack:search")])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_SEARCH,
            namespace=SLACK_NAMESPACE,
            query="revenue",
            filters={"account_id": "acc-2"},
        ),
    )

    assert response.ok is True
    assert [call[1]["account_id"] for call in provider._slack.calls] == ["acc-2"]
    assert response.ret["items"][0]["account_id"] == "acc-2"
    assert response.ret["items"][0]["account_label"] == "Workspace acc-2"


@pytest.mark.asyncio
async def test_upload_with_inline_content_stages_bytes_in_ephemeral_workspace():
    provider = _Provider([_account("acc-1", "slack:files:write")])
    captured: dict[str, Any] = {}

    async def _upload(**kwargs: Any) -> dict[str, Any]:
        outdir = str(run_ctx.OUTDIR_CV.get("") or "")
        captured["outdir"] = outdir
        captured["file_path"] = kwargs["file_path"]
        captured["filename"] = kwargs["filename"]
        root = artifact_outdir_for(pathlib.Path(outdir), create=False)
        captured["staged_bytes"] = (root / kwargs["file_path"]).read_bytes()
        return {
            "ok": True,
            "ret": {"account_id": kwargs["account_id"], "file_id": "F-UP", "filename": kwargs["filename"], "channel": kwargs["channel"]},
        }

    provider._slack.upload_slack_file = _upload
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )

    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            _ctx(),
            NamedServiceRequest(
                operation=OBJECT_ACTION,
                namespace=SLACK_NAMESPACE,
                object_ref="slack:acc-1:channel:C123",
                action=ACTION_UPLOAD_FILE,
                payload={
                    "filename": "logo.png",
                    "content_base64": base64.b64encode(b"png-bytes").decode(),
                    "initial_comment": "the icon",
                },
            ),
        )

    assert response.ok is True
    assert captured["staged_bytes"] == b"png-bytes"
    assert captured["filename"] == "logo.png"
    assert str(run_ctx.OUTDIR_CV.get("") or "") == ""
    assert not pathlib.Path(captured["outdir"]).exists()


@pytest.mark.asyncio
async def test_upload_with_inline_content_requires_filename():
    provider = _Provider([_account("acc-1", "slack:files:write")])

    async def _upload(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("upload must not be called for invalid inline files")

    provider._slack.upload_slack_file = _upload

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=SLACK_NAMESPACE,
            object_ref="slack:acc-1:channel:C123",
            action=ACTION_UPLOAD_FILE,
            payload={"content_base64": base64.b64encode(b"data").decode()},
        ),
    )

    assert response.ok is False
    assert response.status == 400
    assert response.error.code == "slack_inline_file_invalid"


@pytest.mark.asyncio
async def test_post_ambiguity_passes_account_required_candidates_through():
    provider = _Provider([_account("acc-1", "slack:post"), _account("acc-2", "slack:post")])
    consent = {
        "kind": "delegated_to_kdcube.connected_account",
        "reason": REASON_ACCOUNT_REQUIRED,
        "retry_hint": True,
        "provider_id": "slack",
        "connector_app_id": "demo",
        "claims": ["slack:post"],
        "account_id": "",
        "candidates": [
            {"account_id": "acc-1", "label": "Workspace acc-1"},
            {"account_id": "acc-2", "label": "Workspace acc-2"},
        ],
        "url": "/api/integrations/bundles/demo/project/connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube",
        "action_label": "Choose account",
    }

    async def _ambiguous_post(**kwargs: Any) -> dict[str, Any]:
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

    provider._slack.post_slack_message = _ambiguous_post

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=SLACK_NAMESPACE,
            action=ACTION_POST_MESSAGE,
            payload={"channel": "C123", "text": "hello"},
        ),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    details = response.error.details
    assert details["reason"] == REASON_ACCOUNT_REQUIRED
    assert details["retry_hint"] is True
    assert [item["label"] for item in details["candidates"]] == ["Workspace acc-1", "Workspace acc-2"]
    assert details["connection_hub_url"] == consent["url"]


@pytest.mark.asyncio
async def test_get_channel_reads_history_and_decorates_file_refs():
    provider = _Provider([_account("acc-1", "slack:history")])

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=SLACK_NAMESPACE,
            object_ref="slack:acc-1:channel:C123",
        ),
    )

    assert response.ok is True
    message = response.ret["object"]["messages"][0]
    assert message["ref"] == "slack:acc-1:message:C123:1783000000.000100"
    assert message["files"][0]["ref"] == "slack:acc-1:file:F123"


@pytest.mark.asyncio
async def test_get_file_downloads_slack_file():
    provider = _Provider([_account("acc-1", "slack:files:read")])

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=SLACK_NAMESPACE,
            object_ref="slack:acc-1:file:F123",
        ),
    )

    assert response.ok is True
    assert response.ret["object"]["artifact_path"] == "fi:turn.files/slack/report.pdf"


@pytest.mark.asyncio
async def test_get_file_falls_back_to_download_url_without_workspace():
    provider = _Provider([_account("acc-1", "slack:files:read")], file_url_factory=_url_factory)

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

    provider._slack.download_slack_file = _no_workspace
    ref = "slack:acc-1:file:F123"

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace=SLACK_NAMESPACE, object_ref=ref),
    )

    assert response.ok is True
    obj = response.ret["object"]
    assert obj["object_kind"] == "slack.file"
    assert obj["download"]["encoding"] == "url"
    assert obj["download"]["url"] == f"https://runtime.test/download?ref={ref}"
    assert response.ret["extra"]["delivery"] == "url"


@pytest.mark.asyncio
async def test_get_file_without_delivery_path_keeps_tool_error():
    provider = _Provider([_account("acc-1", "slack:files:read")])

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

    provider._slack.download_slack_file = _no_workspace

    response = await provider.object_get(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_GET, namespace=SLACK_NAMESPACE, object_ref="slack:acc-1:file:F123"),
    )

    assert response.ok is False
    assert response.error.code == "artifact_workspace_unavailable"


@pytest.mark.asyncio
async def test_actions_dispatch_to_slack_transport():
    provider = _Provider([_account("acc-1", "slack:post", "slack:files:write")])

    posted = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=SLACK_NAMESPACE,
            object_ref="slack:acc-1:channel:C123",
            action=ACTION_POST_MESSAGE,
            payload={"text": "hello"},
        ),
    )
    uploaded = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=SLACK_NAMESPACE,
            object_ref="slack:acc-1:channel:C123",
            action=ACTION_UPLOAD_FILE,
            payload={"file_path": "fi:turn.files/report.pdf"},
        ),
    )

    assert posted.ok is True
    assert uploaded.ok is True
    assert [call[0] for call in provider._slack.calls] == ["post_slack_message", "upload_slack_file"]


# ── External-agent (MCP) no-consent path ─────────────────────────────────────
# An external MCP client holds namespace grants but ZERO provider consent. The
# structured error IS its whole consent loop: scoped claims for the attempted
# action, labeled candidates, the delegated-to-KDCube deep link seeded with
# exactly those claims, retry_hint, and agent-facing instructions.


@pytest.mark.asyncio
async def test_no_consent_attempt_carries_agent_instructions_and_seeded_deep_link():
    provider = _Provider([])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.status == 403
    details = response.error.details
    # Demand-driven scoping: exactly the attempted action's claim, never a union.
    assert details["claims"] == ["slack:search"]
    # Deep link lands on the delegated-to-KDCube plan seeded with those claims.
    url = details["connection_hub_url"]
    assert "tab=delegated_to_kdcube" in url
    assert "provider_id=slack" in url
    assert "claims=slack%3Asearch" in url
    assert details["retry_hint"] is True
    # Agent-facing instructions: link + claims + retry, self-contained (this
    # surface has no chat banner).
    instructions = details["instructions"]
    assert "retry" in instructions.lower()
    assert details["connection_hub_url"] in instructions
    assert "slack:search" in instructions


@pytest.mark.asyncio
async def test_account_required_instructions_say_resend_with_account_id():
    provider = _Provider([_account("acc-1", "slack:post"), _account("acc-2", "slack:post")])
    consent = {
        "kind": "delegated_to_kdcube.connected_account",
        "reason": REASON_ACCOUNT_REQUIRED,
        "retry_hint": True,
        "provider_id": "slack",
        "connector_app_id": "demo",
        "claims": ["slack:post"],
        "account_id": "",
        "candidates": [
            {"account_id": "acc-1", "label": "Workspace acc-1"},
            {"account_id": "acc-2", "label": "Workspace acc-2"},
        ],
        "url": "/api/integrations/bundles/demo/project/connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube",
        "action_label": "Choose account",
    }

    async def _ambiguous_post(**kwargs: Any) -> dict[str, Any]:
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

    provider._slack.post_slack_message = _ambiguous_post

    response = await provider.object_action(
        _ctx(),
        NamedServiceRequest(
            operation=OBJECT_ACTION,
            namespace=SLACK_NAMESPACE,
            action=ACTION_POST_MESSAGE,
            payload={"channel": "C123", "text": "hello"},
        ),
    )

    assert response.status == 403
    details = response.error.details
    assert details["reason"] == REASON_ACCOUNT_REQUIRED
    # Choice failures instruct a resend with account_id — no consent action.
    instructions = details["instructions"]
    assert "account_id" in instructions
    assert "candidates" in instructions
    assert [c["account_id"] for c in details["candidates"]] == ["acc-1", "acc-2"]


@pytest.mark.asyncio
async def test_no_consent_attempt_records_no_conversation_demand(monkeypatch):
    """MCP attempts are conversation-less: the provider consent error performs
    ZERO demand bookkeeping — no pending snapshot, no hub-registry entry, no
    lane event. The consent loop is response + link + retry only."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
        consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    demand_calls: list = []
    prop_writes: list = []
    original_record = consent_demand.record_consent_demand

    async def _spy_record(**kwargs):
        demand_calls.append(kwargs)
        return await original_record(**kwargs)

    monkeypatch.setattr(consent_demand, "record_consent_demand", _spy_record)
    monkeypatch.setattr(
        sdk_config, "set_user_prop",
        lambda *a, **k: prop_writes.append((a, k)),
        raising=False,
    )

    provider = _Provider([])
    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"
    assert demand_calls == []
    assert prop_writes == []
