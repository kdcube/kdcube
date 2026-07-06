from __future__ import annotations

from typing import Any

import pytest

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
    ConnectedAccount,
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


def _account(account_id: str, *claims: str) -> ConnectedAccount:
    return ConnectedAccount(
        account_id=account_id,
        provider_id="slack",
        connector_app_id="demo",
        external_subject=f"slack:{account_id}",
        display_name=f"Workspace {account_id}",
        workspace=f"Workspace {account_id}",
        claims=claims,
        credential_id=f"cred-{account_id}",
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


class _Provider(SlackNamedServiceProvider):
    def __init__(self, accounts: list[ConnectedAccount] | None = None) -> None:
        super().__init__(entrypoint=None, bundle_id="kdcube-services@1-0")
        self.accounts = list(accounts or [])
        self._slack = _FakeSlack()

    async def _slack_accounts(self, ctx: NamedServiceContext, *, claim: str = "") -> list[ConnectedAccount]:
        del ctx
        return [account for account in self.accounts if not claim or account.allows(claim)]


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
    provider = _Provider([_account("acc-1", "slack:search"), _account("acc-2", "slack:post")])

    response = await provider.object_list(_ctx(), NamedServiceRequest(operation=OBJECT_LIST, namespace=SLACK_NAMESPACE))

    assert response.ok is True
    assert [item["ref"] for item in response.ret["items"]] == ["slack:acc-1", "slack:acc-2"]


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
async def test_search_without_searchable_account_returns_consent_payload():
    provider = _Provider([])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.ok is False
    assert response.status == 403
    assert response.error is not None
    assert response.error.code == "connected_account_consent_required"
    assert response.error.details["consent"]["provider_id"] == "slack"


@pytest.mark.asyncio
async def test_search_dispatches_to_slack_message_search():
    provider = _Provider([_account("acc-1", "slack:search")])

    response = await provider.object_search(
        _ctx(),
        NamedServiceRequest(operation=OBJECT_SEARCH, namespace=SLACK_NAMESPACE, query="revenue"),
    )

    assert response.ok is True
    assert response.ret["items"][0]["ref"] == "slack:acc-1:message:C123:1783000000.000100"


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
