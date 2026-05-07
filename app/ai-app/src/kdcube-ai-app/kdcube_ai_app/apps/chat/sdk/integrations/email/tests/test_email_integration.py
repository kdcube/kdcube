from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.email import accounts as email_accounts
from kdcube_ai_app.apps.chat.sdk.integrations.email import attachments as email_attachments
from kdcube_ai_app.apps.chat.sdk.integrations.email import claude as email_claude
from kdcube_ai_app.apps.chat.sdk.integrations.email import delivery as email_delivery
from kdcube_ai_app.apps.chat.sdk.integrations.email import icloud as email_icloud
from kdcube_ai_app.apps.chat.sdk.integrations.email import mcp as email_mcp


def test_email_claude_session_id_for_run_is_valid_uuid():
    session_id = email_claude._claude_session_id_for_run("email_mcp_f8f4f14dd02046c19819160d3b9d52b9")

    assert str(uuid.UUID(session_id)) == session_id


def test_email_delivery_builds_html_and_text_parts():
    msg = email_delivery.build_email_message(
        sender_email="reports@example.test",
        recipients=email_delivery.split_email_addresses("a@example.test; a@example.test, b@example.test"),
        subject="Report",
        body_text="# Title\n\n- **Item** with `code`\n\n| A | B |\n|---|---|\n| 1 | 2 |",
        attachments=[
            {
                "filename": "report.txt",
                "mime_type": "text/plain",
                "data": b"report",
            }
        ],
    )

    assert msg["To"] == "a@example.test, b@example.test"
    html_part = msg.get_body(preferencelist=("html",))
    assert html_part is not None
    rendered = html_part.get_content()
    assert "<h1>Title</h1>" in rendered
    assert "<strong>Item</strong>" in rendered
    assert '<table class="kdcube-table">' in rendered
    assert any(part.get_filename() == "report.txt" for part in msg.iter_attachments())


@pytest.mark.asyncio
async def test_email_claude_accepts_recorded_result_when_process_times_out(tmp_path, monkeypatch):
    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.mcp.auth_secret":
                return "mcp-secret"
            if path == "integrations.email.claude_code.timeout_seconds":
                return 90
            return default

    class _Binding:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Agent:
        def __init__(self, *, config, binding, comm=None):
            self.config = config
            self.binding = binding
            self.comm = comm

    class _Result:
        status = "failed"
        exit_code = 0
        error_message = "Claude exceeded timeout of 90.0s"
        stderr_lines = []
        final_text = ""
        model = "claude-sonnet-4-6"
        requested_model = "sonnet"
        usage = {}
        cost_usd = None
        timed_out = True
        timeout_seconds = 90.0
        duration_ms = 90001
        api_duration_ms = None

    async def fake_run_claude_code_turn(*, agent, prompt):
        del prompt
        run_id = agent.config.workspace_path.name
        email_mcp.EmailMCPRunStore(tmp_path, user_id="user-a").update_result(
            run_id=run_id,
            result={
                "processed_message_ids": ["m-1"],
                "matched_message_ids": ["m-1"],
                "summary": "One match.",
            },
        )
        return _Result()

    monkeypatch.setattr(email_claude, "ClaudeCodeAgent", _Agent)
    monkeypatch.setattr(email_claude, "ClaudeCodeAgentConfig", _Config)
    monkeypatch.setattr(email_claude, "ClaudeCodeBinding", _Binding)
    monkeypatch.setattr(email_claude, "run_claude_code_turn", fake_run_claude_code_turn)

    result = await email_claude.run_email_processor_with_claude_code(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        account={"account_id": "google-1", "email": "user@example.test"},
        mailbox="INBOX",
        unread_only=False,
        limit=20,
        gmail_query="after:2026/05/01 before:2026/05/02",
        task_id="",
        task_definition="",
        instruction="Find tech news.",
        messages=[{"message_id": "m-1", "subject": "Tech news"}],
    )

    assert result["ok"] is True
    assert result["recorded_result"]["matched_message_ids"] == ["m-1"]
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == 90.0
    assert result["duration_ms"] == 90001
    assert result["warnings"][0]["code"] == "claude_code_mcp_result_recorded_but_process_failed"


def test_email_account_store_keeps_tokens_out_of_metadata(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    def fake_delete_user_secret(key, *, user_id=None, bundle_id=None):
        secrets.pop((user_id, bundle_id, key), None)

    async def fake_delete_user_secret_async(key, *, user_id=None, bundle_id=None):
        fake_delete_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)
    monkeypatch.setattr(email_mod, "delete_user_secret", fake_delete_user_secret)
    monkeypatch.setattr(email_mod, "delete_user_secret_async", fake_delete_user_secret_async)

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "secret-access", "refresh_token": "secret-refresh"})

    accounts = store.list_accounts()

    assert accounts[0]["email"] == "user@example.test"
    assert accounts[0]["has_token"] is True
    assert "secret-access" not in store.accounts_path.read_text(encoding="utf-8")
    assert store.get_tokens(account["account_id"])["refresh_token"] == "secret-refresh"


@pytest.mark.asyncio
async def test_icloud_account_uses_user_secret_and_shared_provider_dispatch(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account(
        {
            "provider": "icloud",
            "email": "name@icloud.com",
            "settings": {"imap_host": "imap.mail.me.com", "smtp_host": "smtp.mail.me.com"},
        }
    )
    store.set_tokens(account["account_id"], {"username": "name@icloud.com", "password": "app-specific"})

    result = await email_mod.ensure_email_account_access(store=store, entrypoint=object(), account=account)

    assert result["ok"] is True
    assert result["username"] == "name@icloud.com"
    assert "app-specific" not in store.accounts_path.read_text(encoding="utf-8")
    assert store.list_accounts()[0]["provider"] == "icloud"


def test_icloud_search_criteria_supports_sender_recipient_and_dates():
    icloud_mod = email_icloud

    criteria = icloud_mod._imap_search_criteria(
        query='from:sender@example.com to:me@icloud.com subject:"Invoice" after:2026/05/01 before:2026/05/04 urgent',
        unread_only=False,
    )

    assert criteria[:1] == ["ALL"]
    assert "FROM" in criteria
    assert '"sender@example.com"' in criteria
    assert "TO" in criteria
    assert '"me@icloud.com"' in criteria
    assert "SUBJECT" in criteria
    assert '"Invoice"' in criteria
    assert "SINCE" in criteria
    assert "01-May-2026" in criteria
    assert "BEFORE" in criteria
    assert "04-May-2026" in criteria
    assert "TEXT" in criteria
    assert '"urgent"' in criteria


def test_google_oauth_authorize_url_uses_signed_state(tmp_path):
    email_mod = email_accounts

    class _Entry:
        props = {
            "integrations": {
                "email": {
                    "google": {"client_id": "google-client-id"},
                    "oauth": {
                        "state_secret": "state-secret",
                        "redirect_uri": "https://example.test/public/email_oauth_callback",
                    },
                }
            }
        }

        def bundle_prop(self, path, default=None):
            cursor = self.props
            for part in path.split("."):
                if not isinstance(cursor, dict) or part not in cursor:
                    return default
                cursor = cursor[part]
            return cursor

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a")
    result = email_mod.build_google_authorize_url(entrypoint=_Entry(), store=store, source="settings")

    assert result["provider"] == "google"
    assert "client_id=google-client-id" in result["authorize_url"]
    assert "redirect_uri=https%3A%2F%2Fexample.test%2Fpublic%2Femail_oauth_callback" in result["authorize_url"]
    assert "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send" in result["authorize_url"]
    assert any((tmp_path / "email" / "_oauth_states").glob("*.json"))


def test_google_oauth_authorize_url_merges_required_scopes_when_descriptor_is_old(tmp_path):
    email_mod = email_accounts

    class _Entry:
        props = {
            "integrations": {
                "email": {
                    "google": {
                        "client_id": "google-client-id",
                        "scopes": [
                            "openid",
                            "email",
                            "profile",
                            "https://www.googleapis.com/auth/gmail.readonly",
                        ],
                    },
                    "oauth": {
                        "state_secret": "state-secret",
                        "redirect_uri": "https://example.test/public/email_oauth_callback",
                    },
                }
            }
        }

        def bundle_prop(self, path, default=None):
            cursor = self.props
            for part in path.split("."):
                if not isinstance(cursor, dict) or part not in cursor:
                    return default
                cursor = cursor[part]
            return cursor

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a")
    result = email_mod.build_google_authorize_url(entrypoint=_Entry(), store=store, source="settings")

    assert "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in result["authorize_url"]
    assert "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send" in result["authorize_url"]


@pytest.mark.asyncio
async def test_process_user_emails_keeps_run_metadata_without_processed_id_ledger(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return False
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})
    observed_queries = []

    async def fake_fetch_google_messages(**kwargs):
        observed_queries.append(kwargs.get("gmail_query"))
        return {
            "ok": True,
            "messages": [
                {"message_id": "m-1", "subject": "Investor update"},
                {"message_id": "m-2", "subject": "Customer escalation"},
            ],
        }

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    first = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        gmail_query="after:2026/05/02 before:2026/05/03 technology",
        task_definition=json.dumps({"title": "Monitor Gmail"}),
        instruction="Find customer escalations.",
    )
    second = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        instruction="Find customer escalations.",
    )

    assert first["ok"] is True
    assert first["new_count"] == 2
    assert observed_queries[0] == "after:2026/05/02 before:2026/05/03 technology"
    assert second["ok"] is True
    assert second["seen_count"] == 0
    assert second["new_count"] == 2
    run_state_path = tmp_path / "email" / "runs" / "user-a" / "task-email" / f"{account['account_id']}.json"
    assert run_state_path.exists()
    run_state = json.loads(run_state_path.read_text())
    assert "seen_message_ids" not in run_state
    assert "processed_message_ids" not in run_state
    assert run_state["cursor"]["processed_message_count_total"] == 4


@pytest.mark.asyncio
async def test_process_user_emails_does_not_hide_previous_messages_by_default(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return False
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    async def fake_fetch_google_messages(**kwargs):
        return {
            "ok": True,
            "messages": [
                {"message_id": "m-1", "subject": "AI newsletter"},
                {"message_id": "m-2", "subject": "Developer tools update"},
            ],
        }

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    first = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        gmail_query="after:2026/05/02 before:2026/05/03 technology",
        instruction="Find technology newsletters.",
    )
    second = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        gmail_query="after:2026/05/02 before:2026/05/03 technology",
        instruction="Find vendor updates instead.",
    )

    assert first["ok"] is True
    assert first["new_count"] == 2
    assert second["ok"] is True
    assert second["seen_count"] == 0
    assert second["new_count"] == 2


@pytest.mark.asyncio
async def test_fetch_google_messages_classifies_structured_google_403(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    async def fake_google_get(url, *, access_token):
        raise email_mod.ProviderHttpError(
            status=403,
            reason="Forbidden",
            url=url,
            body='{"error":{"code":403,"message":"Gmail API has not been used in project 123 before or it is disabled.","status":"PERMISSION_DENIED"}}',
            parsed={
                "error": {
                    "code": 403,
                    "message": "Gmail API has not been used in project 123 before or it is disabled.",
                    "status": "PERMISSION_DENIED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "SERVICE_DISABLED",
                            "domain": "googleapis.com",
                            "metadata": {"service": "gmail.googleapis.com"},
                        }
                    ],
                }
            },
        )

    monkeypatch.setattr(email_mod, "_google_get", fake_google_get)

    result = await email_mod.fetch_google_messages(
        store=store,
        entrypoint=_Entry(),
        account=account,
        mailbox="INBOX",
        unread_only=False,
        limit=10,
        gmail_query="after:2026/05/02 before:2026/05/03",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "google_gmail_api_not_enabled"
    assert result["error"]["category"] == "deployment_config"
    assert result["error"]["user_action_required"] is False
    assert result["error"]["provider_reason"] == "SERVICE_DISABLED"
    assert result["error"]["provider_metadata"]["service"] == "gmail.googleapis.com"
    assert "Enable Gmail API" in result["error"]["message"]


@pytest.mark.asyncio
async def test_process_user_emails_selects_account_by_email_when_multiple_connected(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return False
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    work = store.upsert_account({"provider": "google", "email": "work@example.test"})
    personal = store.upsert_account({"provider": "google", "email": "personal@example.test"})
    store.set_tokens(work["account_id"], {"access_token": "work-access"})
    store.set_tokens(personal["account_id"], {"access_token": "personal-access"})
    selected = []

    async def fake_fetch_google_messages(**kwargs):
        selected.append(kwargs["account"])
        return {"ok": True, "messages": [{"message_id": "m-1", "subject": "Tech news"}]}

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    missing_account = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        instruction="Summarize technology news.",
    )
    by_email = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        account="work@example.test",
        instruction="Summarize technology news.",
    )

    assert missing_account["ok"] is False
    assert missing_account["error"]["code"] == "email_account_required"
    assert by_email["ok"] is True
    assert by_email["account"]["email"] == "work@example.test"
    assert selected[0]["account_id"] == work["account_id"]


@pytest.mark.asyncio
async def test_process_user_emails_marks_seen_after_claude_mcp_success(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return True
            if path == "integrations.email.mcp.auth_secret":
                return "mcp-secret"
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    async def fake_fetch_google_messages(**kwargs):
        return {
            "ok": True,
            "messages": [
                {"message_id": "m-1", "subject": "Customer escalation"},
                {"message_id": "m-2", "subject": "Newsletter"},
            ],
        }

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    async def fake_run_email_processor_with_claude_code(**kwargs):
        return {
            "ok": True,
            "run_id": "email_mcp_test",
            "status": "completed",
            "final_text": "One matched escalation.",
            "candidate_message_count": 2,
            "messages": [
                {"message_id": "m-1", "subject": "Customer escalation", "internal_date": "1777900000000"},
                {"message_id": "m-2", "subject": "Newsletter", "internal_date": "1777900001000"},
            ],
            "executive_journal": [
                {
                    "prefix": "EXECUTIVE_JOURNAL",
                    "payload": {"stage": "classification", "status": "completed", "matched_count": 1},
                    "raw_line": "EXECUTIVE_JOURNAL {\"stage\":\"classification\",\"status\":\"completed\",\"matched_count\":1}",
                }
            ],
            "recorded_result": {
                "processed_message_ids": ["m-1", "m-2"],
                "matched_message_ids": ["m-1"],
                "summary": "One matched escalation.",
                "details": {"category": "customer_escalation", "message_id": "m-1"},
            },
        }

    claude_mod = email_claude
    monkeypatch.setattr(claude_mod, "run_email_processor_with_claude_code", fake_run_email_processor_with_claude_code)

    first = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        execution_id="exec-1",
        task_definition=json.dumps({"title": "Monitor Gmail"}),
        instruction="Find customer escalations.",
    )
    second = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        execution_id="exec-2",
        instruction="Find customer escalations.",
    )

    assert first["ok"] is True
    assert first["processing_mode"] == "claude_code_mcp"
    assert first["processed_count"] == 2
    assert first["processor_result"]["matched_message_ids"] == ["m-1"]
    assert first["processor_result"]["summary"] == "One matched escalation."
    assert first["processor_result"]["details"]["category"] == "customer_escalation"
    assert first["executive_journal"][0]["payload"]["stage"] == "classification"
    assert first["claude_code_mcp"]["executive_journal_count"] == 1
    assert first["claude_code_mcp"]["run_id"] == "email_mcp_test"
    assert "final_text" not in first["claude_code_mcp"]
    assert "recorded_result" not in first["claude_code_mcp"]
    assert "messages" not in first["claude_code_mcp"]
    assert second["ok"] is True
    assert second["seen_count"] == 0
    assert second["new_count"] == 2
    mcp_mod = email_mcp
    synced = mcp_mod.EmailMCPRunStore(tmp_path, user_id="user-a").read_task_state(
        task_id="task-email",
        account_id=account["account_id"],
    )
    assert synced["exists"] is True
    assert "seen_message_ids" not in synced["state"]["sdk_email_run_state"]
    assert "processed_message_ids" not in synced["state"]["sdk_email_run_state"]
    assert synced["state"]["sdk_email_run_state"]["cursor"]["high_watermark_at"]
    assert "diagnostic only" in synced["state"]["sdk_email_run_state"]["state_policy"]
    assert synced["state"]["sdk_email_run_state"]["last_claude_code_run_id"] == "email_mcp_test"


@pytest.mark.asyncio
async def test_process_user_emails_delegates_gmail_scope_to_claude_when_enabled(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return True
            if path == "integrations.email.mcp.auth_secret":
                return "mcp-secret"
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})
    fetch_calls = []

    async def fake_fetch_google_messages(**kwargs):
        fetch_calls.append(kwargs)
        return {"ok": True, "messages": [{"message_id": "fallback", "subject": "fallback"}]}

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    async def fake_run_email_processor_with_claude_code(**kwargs):
        assert kwargs["messages"] == []
        return {
            "ok": True,
            "run_id": "email_mcp_test",
            "status": "completed",
            "candidate_message_count": 2,
            "messages": [
                {"message_id": "m-1", "subject": "Customer escalation"},
                {"message_id": "m-2", "subject": "Newsletter"},
            ],
            "recorded_result": {
                "processed_message_ids": ["m-1", "m-2"],
                "matched_message_ids": ["m-1"],
                "summary": "One matched escalation.",
            },
        }

    claude_mod = email_claude
    monkeypatch.setattr(claude_mod, "run_email_processor_with_claude_code", fake_run_email_processor_with_claude_code)

    result = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        task_id="task-email",
        execution_id="exec-1",
        task_definition=json.dumps({"title": "Monitor Gmail"}),
        instruction="Find customer escalations.",
    )

    assert result["ok"] is True
    assert result["processing_mode"] == "claude_code_mcp"
    assert result["new_count"] == 2
    assert result["processed_count"] == 2
    assert fetch_calls == []


@pytest.mark.asyncio
async def test_process_user_emails_returns_messages_when_claude_mcp_did_not_record_result(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            if path == "integrations.email.claude_code.enabled":
                return True
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    async def fake_fetch_google_messages(**kwargs):
        return {
            "ok": True,
            "messages": [{"message_id": "m-1", "subject": "Technology newsletter"}],
        }

    monkeypatch.setattr(email_mod, "fetch_google_messages", fake_fetch_google_messages)

    async def fake_run_email_processor_with_claude_code(**kwargs):
        return {
            "ok": False,
            "run_id": "email_mcp_test",
            "status": "completed",
            "final_text": "MCP tools were unavailable.",
            "recorded_result": None,
            "error_code": "claude_code_mcp_result_not_recorded",
            "effective_error_message": "Claude Code completed without recording an MCP result.",
        }

    claude_mod = email_claude
    monkeypatch.setattr(claude_mod, "run_email_processor_with_claude_code", fake_run_email_processor_with_claude_code)

    result = await email_mod.process_user_emails(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        tenant="demo-tenant",
        project="demo-project",
        instruction="Find technology news.",
    )

    assert result["ok"] is True
    assert result["processing_mode"] == "react_agent_review"
    assert result["new_count"] == 1
    assert result["messages"][0]["message_id"] == "m-1"
    assert result["warnings"][0]["code"] == "claude_code_mcp_result_not_recorded"
    assert result["claude_code_mcp"]["ok"] is False


def test_email_mcp_token_is_task_scoped_and_verifiable(tmp_path):
    mcp_mod = email_mcp

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.mcp.auth_secret":
                return "mcp-secret"
            return default

    prepared = mcp_mod.create_email_mcp_run(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        task_id="task-email",
        execution_id="exec-1",
        account={"account_id": "google_1", "provider": "google", "email": "user@example.test"},
        mailbox="inbox",
        unread_only=True,
        limit=20,
        gmail_query="after:2026/05/02 before:2026/05/03 technology",
        task_definition='{"title":"Monitor Gmail"}',
        instruction="Find customer escalations.",
        messages=[{"message_id": "m-1", "subject": "Customer escalation"}],
    )

    payload = mcp_mod.verify_email_mcp_token(prepared["token"], secret="mcp-secret")
    run = mcp_mod.EmailMCPRunStore(tmp_path, user_id="user-a").read_run(payload["run_id"])

    assert payload["scope"] == mcp_mod.EMAIL_MCP_TOKEN_SCOPE
    assert payload["task_id"] == "task-email"
    assert payload["execution_id"] == "exec-1"
    assert run["candidate_message_ids"] == ["m-1"]
    assert "mcp__task_memo_email__restore_current_task_state" in prepared["allowed_tools"]
    assert "mcp__task_memo_email__search_messages" in prepared["allowed_tools"]
    assert "mcp__task_memo_email__get_message_attachment" in prepared["allowed_tools"]
    assert "mcp__task_memo_email__store_current_task_state" in prepared["allowed_tools"]

    mcp_app = mcp_mod.build_email_mcp_app(
        entrypoint=_Entry(),
        request=SimpleNamespace(headers={mcp_mod.EMAIL_MCP_TOKEN_HEADER: prepared["token"]}),
        storage_root=tmp_path,
    )
    assert getattr(mcp_app.settings, "stateless_http", False) is True


def test_email_mcp_task_state_is_user_task_account_scoped(tmp_path):
    mcp_mod = email_mcp

    store = mcp_mod.EmailMCPRunStore(tmp_path, user_id="user-a")
    empty = store.read_task_state(task_id="task-email", account_id="google_1")
    assert empty["exists"] is False
    assert empty["state"] == {}

    written = store.write_task_state(
        task_id="task-email",
        account_id="google_1",
        state={"cursor": "m-9", "last_range": "after:2026/05/01"},
        note="next run should continue after this cursor",
        run_id="email_mcp_123",
        execution_id="exec-1",
    )
    restored = store.read_task_state(task_id="task-email", account_id="google_1")

    assert written["state"]["cursor"] == "m-9"
    assert restored["exists"] is True
    assert restored["state"]["last_range"] == "after:2026/05/01"
    assert restored["last_run_id"] == "email_mcp_123"
    assert store.read_task_state(task_id="task-other", account_id="google_1")["exists"] is False
    assert mcp_mod.EmailMCPRunStore(tmp_path, user_id="user-b").read_task_state(
        task_id="task-email",
        account_id="google_1",
    )["exists"] is False


def test_gmail_message_summary_exposes_attachment_metadata():
    email_mod = email_accounts

    summary = email_mod._message_summary(
        {
            "id": "m-1",
            "threadId": "t-1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Report"}],
                "parts": [
                    {
                        "partId": "1",
                        "filename": "report.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-1", "size": 1234},
                    }
                ],
            },
        }
    )

    assert summary["has_attachments"] is True
    assert summary["attachments"] == [
        {
            "part_id": "1",
            "attachment_id": "att-1",
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1234,
        }
    ]


@pytest.mark.asyncio
async def test_fetch_google_attachment_returns_text_and_base64(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    async def fake_google_get(url, *, access_token):
        assert access_token == "access"
        if "/attachments/att-1" in url:
            return {"data": "aGVsbG8td29ybGQ"}
        return {
            "id": "m-1",
            "payload": {
                "parts": [
                    {
                        "partId": "1",
                        "filename": "note.txt",
                        "mimeType": "text/plain",
                        "body": {"attachmentId": "att-1", "size": 11},
                    }
                ]
            },
        }

    monkeypatch.setattr(email_mod, "_google_get", fake_google_get)

    result = await email_mod.fetch_google_attachment(
        store=store,
        entrypoint=_Entry(),
        account=account,
        message_id="m-1",
        attachment_id="att-1",
    )

    assert result["ok"] is True
    assert result["filename"] == "note.txt"
    assert result["text"] == "hello-world"
    assert result["base64"] == "aGVsbG8td29ybGQ="


@pytest.mark.asyncio
async def test_fetch_google_attachment_tries_endpoint_when_refetched_metadata_misses_id(tmp_path, monkeypatch):
    email_mod = email_accounts
    secrets = {}

    def fake_set_user_secret(key, value, *, user_id=None, bundle_id=None):
        secrets[(user_id, bundle_id, key)] = value

    async def fake_set_user_secret_async(key, value, *, user_id=None, bundle_id=None):
        fake_set_user_secret(key, value, user_id=user_id, bundle_id=bundle_id)

    def fake_get_user_secret(key, *, user_id=None, bundle_id=None):
        return secrets.get((user_id, bundle_id, key))

    async def fake_get_user_secret_async(key, *, user_id=None, bundle_id=None):
        return fake_get_user_secret(key, user_id=user_id, bundle_id=bundle_id)

    monkeypatch.setattr(email_mod, "set_user_secret", fake_set_user_secret)
    monkeypatch.setattr(email_mod, "set_user_secret_async", fake_set_user_secret_async)
    monkeypatch.setattr(email_mod, "get_user_secret", fake_get_user_secret)
    monkeypatch.setattr(email_mod, "get_user_secret_async", fake_get_user_secret_async)

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.email.google.client_id":
                return "client-id"
            return default

    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    account = store.upsert_account({"provider": "google", "email": "user@example.test"})
    store.set_tokens(account["account_id"], {"access_token": "access"})

    calls = []

    async def fake_google_get(url, *, access_token):
        calls.append(url)
        assert access_token == "access"
        if "/attachments/att-1" in url:
            return {"data": "cGRmLWJ5dGVz"}
        return {
            "id": "m-1",
            "payload": {
                "parts": [
                    {
                        "partId": "1",
                        "filename": "invoice.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "different-id", "size": 9},
                    }
                ]
            },
        }

    monkeypatch.setattr(email_mod, "_google_get", fake_google_get)

    result = await email_mod.fetch_google_attachment(
        store=store,
        entrypoint=_Entry(),
        account=account,
        message_id="m-1",
        attachment_id="att-1",
    )

    assert result["ok"] is True
    assert result["base64"] == "cGRmLWJ5dGVz"
    assert any("/attachments/att-1" in url for url in calls)


@pytest.mark.asyncio
async def test_email_attachment_materializer_uses_exact_selection_when_message_metadata_misses_id(tmp_path, monkeypatch):
    email_mod = email_attachments
    store = email_mod.EmailAccountStore(tmp_path, user_id="user-a", bundle_id="task-and-memo-app@1-0")
    store.upsert_account({"provider": "google", "email": "user@example.test"})

    async def fake_fetch_email_message(**kwargs):
        assert kwargs["message_id"] == "m-1"
        return {
            "ok": True,
            "message": {
                "message_id": "m-1",
                "subject": "Invoice",
                "from": "billing@example.test",
                "attachments": [],
            },
        }

    async def fake_fetch_email_attachment(**kwargs):
        assert kwargs["message_id"] == "m-1"
        assert kwargs["attachment_id"] == "att-1"
        return {
            "ok": True,
            "message_id": "m-1",
            "filename": "invoice.pdf",
            "mime_type": "application/pdf",
            "base64": "cGRmLWJ5dGVz",
        }

    monkeypatch.setattr(email_mod, "fetch_email_message", fake_fetch_email_message)
    monkeypatch.setattr(email_mod, "fetch_email_attachment", fake_fetch_email_attachment)

    outdir = tmp_path / "out"
    outdir.mkdir()
    result = await email_mod.materialize_email_attachments_for_current_turn(
        entrypoint=SimpleNamespace(),
        storage_root=tmp_path,
        outdir=outdir,
        turn_id="turn-1",
        user_id="user-a",
        bundle_id="task-and-memo-app@1-0",
        account="user@example.test",
        attachment_selection_json=json.dumps(
            [
                {
                    "message_id": "m-1",
                    "attachment_id": "att-1",
                    "filename": "invoice.pdf",
                    "mime_type": "application/pdf",
                }
            ]
        ),
        visibility="external",
    )

    assert result["ok"] is True
    assert result["file_count"] == 1
    assert result["files"][0]["filename"] == "invoice.pdf"
    assert (outdir / result["files"][0]["physical_path"]).read_bytes() == b"pdf-bytes"
    assert result["errors"] == []
    assert result["warnings"][0]["code"] == "email_attachment_metadata_mismatch"
    assert result["warnings"][0]["message_id"] == "m-1"
    assert result["warnings"][0]["attachment_id"] == "att-1"
