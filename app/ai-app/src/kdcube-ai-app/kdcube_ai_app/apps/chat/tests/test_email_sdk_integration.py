# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    EMAIL_MCP_TOKEN_HEADER,
    EmailAccountStore,
    EmailMCPRunStore,
    create_email_mcp_run,
    default_icloud_account_settings,
    verify_email_mcp_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.email import settings as email_settings


class _Entry:
    config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="demo.bundle@1-0"))

    def bundle_prop(self, path, default=None):
        if path == "integrations.email.mcp.auth_secret":
            return "mcp-secret"
        return default


def test_email_account_store_keeps_account_metadata_without_tokens(tmp_path):
    store = EmailAccountStore(tmp_path, user_id="user-a", bundle_id="demo.bundle@1-0")

    account = store.upsert_account(
        {
            "provider": "icloud",
            "email": "user@example.test",
            "settings": default_icloud_account_settings(),
        }
    )

    assert account["provider"] == "icloud"
    assert account["email"] == "user@example.test"
    assert store.get_account("user@example.test")["account_id"] == account["account_id"]


def test_email_settings_module_uses_configured_bundle_hooks(tmp_path):
    class _SettingsEntry:
        pass

    email_settings.configure_email_settings(
        storage_root_or_error=lambda entrypoint: tmp_path,
        target_user_id=lambda entrypoint, user_id=None, fingerprint=None: user_id or f"fp:{fingerprint}",
        bundle_id="demo.bundle@1-0",
    )

    store, resolved_user = email_settings.store_for(_SettingsEntry(), fingerprint="abc")

    assert resolved_user == "fp:abc"
    assert store.user_id == "fp:abc"
    assert store.bundle_id == "demo.bundle@1-0"


def test_email_mcp_run_token_is_scoped_to_run_and_bundle(tmp_path):
    prepared = create_email_mcp_run(
        entrypoint=_Entry(),
        storage_root=tmp_path,
        user_id="user-a",
        task_id="task-email",
        execution_id="exec-1",
        account={"account_id": "google_1", "provider": "google", "email": "user@example.test"},
        mailbox="inbox",
        unread_only=True,
        limit=20,
        gmail_query="after:2026/05/02 before:2026/05/03",
        task_definition="Check emails",
        instruction="Find escalations.",
        messages=[{"message_id": "m-1", "subject": "Escalation"}],
    )

    payload = verify_email_mcp_token(prepared["token"], secret="mcp-secret")
    run = EmailMCPRunStore(tmp_path, user_id="user-a").read_run(payload["run_id"])

    assert prepared["token_header"] == EMAIL_MCP_TOKEN_HEADER
    assert payload["bundle_id"] == "demo.bundle@1-0"
    assert run["bundle_id"] == "demo.bundle@1-0"
    assert run["candidate_message_ids"] == ["m-1"]
