---
id: ks:docs/sdk/integrations/email/README.md
title: "Email SDK Integration"
summary: "Reusable email integration mechanics for KDCube bundles: account metadata, Gmail OAuth/API access, iCloud IMAP/SMTP, attachment materialization, email MCP runs, and Claude Code email processing."
tags: ["sdk", "integrations", "email", "gmail", "icloud", "imap", "mcp", "bundles"]
keywords: ["email integration", "gmail integration", "email accounts", "email attachments", "email mcp", "claude code email"]
see_also:
  - ks:docs/sdk/integrations/telegram/README.md
  - ks:docs/service/servicing-interfaces-README.md
---

# Email SDK Integration

The email SDK integration contains reusable provider and processing mechanics
that bundles can import from:

```python
from kdcube_ai_app.apps.chat.sdk.integrations import email
```

The SDK owns email protocol mechanics. The bundle still owns product policy:
who may connect accounts, how an account is exposed in UI, which task or agent
uses the mailbox, and how outputs are delivered.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.integrations.email
  accounts.py      account metadata, Gmail OAuth helpers, token storage hooks,
                   Gmail message/list/attachment APIs, provider dispatch,
                   process_user_emails(...)
  icloud.py        iCloud/IMAP account defaults, app-password validation,
                   message search/read/attachment fetch, SMTP send primitive
  attachments.py   materialize selected email attachments into the current
                   ReAct turn as file artifacts
  settings.py      configurable account settings operations for status,
                   OAuth start/callback, disconnect, and app-password connect
  mcp.py           task-scoped Email MCP run store, signed run tokens,
                   FastAPI MCP app factory
  claude.py        Claude Code email processor that uses the Email MCP server
```

`__init__.py` re-exports the stable bundle-facing symbols for normal imports.

## Account Store

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import EmailAccountStore

store = EmailAccountStore(storage_root, user_id=user_id, bundle_id=bundle_id)
account = store.upsert_account({
    "provider": "google",
    "email": "user@example.com",
    "scope": ["https://www.googleapis.com/auth/gmail.readonly"],
})
store.set_tokens(account["account_id"], {"access_token": "...", "refresh_token": "..."})
```

`EmailAccountStore` stores account metadata under the bundle storage root and
stores secrets through the KDCube user-secret API. Account JSON records keep
only metadata and a `has_token` flag; OAuth tokens and iCloud app passwords do
not live in account metadata files.

The store is provider-neutral. Provider-specific checks happen through:

```python
await ensure_email_account_access(store=store, entrypoint=entrypoint, account=account)
```

## Account Settings Operations

`settings.py` provides reusable account-management operations for bundle UIs
and Telegram Mini Apps. The bundle supplies its storage root, user resolution,
and optional Telegram identity resolver:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import settings as email_settings

email_settings.configure_email_settings(
    storage_root_or_error=storage_root,
    target_user_id=target_user_id,
    resolve_identity=telegram_widget_auth.resolve_identity,
    bundle_id="my.bundle@1-0",
)

payload = email_settings.status(entrypoint, user_id="user-a")
oauth = email_settings.start_oauth(entrypoint, request=request)
```

The operations cover:

- `status(...)`
- `start_oauth(...)`
- `callback(...)`
- `disconnect(...)`
- `connect_app_password(...)`
- Telegram Web App variants that first resolve Telegram `initData`

## Gmail OAuth

The SDK provides Gmail OAuth URL construction, callback exchange, token refresh,
profile fetch, and provider error normalization:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    build_google_authorize_url,
    exchange_google_code,
    fetch_google_profile,
)
```

The bundle supplies the `entrypoint` so descriptor values and bundle-scoped
secrets can be resolved:

```text
integrations.email.google.client_id
integrations.email.google.client_secret
integrations.email.google.scopes
integrations.email.oauth.redirect_uri
integrations.email.oauth.state_secret
```

Provider failures are returned as structured payloads with `code`, `category`,
`provider`, `operation`, and provider diagnostics when available.

## Message Access

Use provider-specific functions when the provider is known:

```python
await fetch_google_messages(...)
await fetch_google_message(...)
await fetch_google_attachment(...)

await fetch_icloud_messages(...)
await fetch_icloud_message(...)
await fetch_icloud_attachment(...)
```

Use provider-dispatch functions when the caller only has an account record:

```python
await fetch_email_messages(...)
await fetch_email_message(...)
await fetch_email_attachment(...)
```

The normalized message shape includes:

```text
message_id, thread_id, from, to, subject, date, internal_date,
snippet, body_excerpt, body_truncated, label_ids, attachments[]
```

Attachments are represented with:

```text
attachment_id, filename, mime_type, size_bytes, part_id
```

## Attachment Materialization

`attachments.py` turns selected email attachments into current-turn ReAct file
artifacts:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    materialize_email_attachments_for_current_turn,
)

result = await materialize_email_attachments_for_current_turn(
    entrypoint=entrypoint,
    store=store,
    artifact_scope=artifact_scope,
    account="user@example.com",
    selections=[{"message_id": "m-1", "attachment_id": "att-1"}],
    visibility="visible",
)
```

The SDK performs provider fetch, base64 decode, filename/mime normalization,
and artifact writes. The bundle decides which messages or attachments are
selected and whether the resulting files are user-visible.

## Process User Emails

`process_user_emails(...)` is the shared high-level mailbox processing entry:

```python
result = await process_user_emails(
    entrypoint=entrypoint,
    storage_root=storage_root,
    user_id=user_id,
    bundle_id=bundle_id,
    tenant=tenant,
    project=project,
    account="user@example.com",
    gmail_query="after:2026/05/01 before:2026/05/02",
    instruction="Find customer escalations.",
)
```

It selects the connected account, fetches candidate messages, records diagnostic
run metadata, and optionally delegates deeper analysis to Claude Code through
the Email MCP service when enabled by bundle config.

Run metadata is diagnostic. It does not hide old messages with a processed-id
ledger; task-specific future-run decisions belong to the task or MCP processor.

## Email MCP Service

`mcp.py` provides a short-lived, task-scoped MCP service for external processors
such as Claude Code:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    build_email_mcp_app,
    create_email_mcp_run,
    verify_email_mcp_token,
)

prepared = create_email_mcp_run(
    entrypoint=entrypoint,
    storage_root=storage_root,
    user_id=user_id,
    task_id=task_id,
    execution_id=execution_id,
    account=account,
    mailbox="INBOX",
    gmail_query="after:2026/05/01",
)
```

The run document records the selected account, mailbox/query, task/execution
identity, TTL, and bundle id. The signed token is accepted only for that run.

The MCP app exposes email tools for the processor and writes the processor
result back to `EmailMCPRunStore`. The bundle can mount the app as a public or
internal route, but the SDK owns token verification and run lookup.

## Claude Code Processor

`claude.py` wires the Email MCP run into a Claude Code execution:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    claude_code_enabled,
    run_email_processor_with_claude_code,
)
```

The processor creates an MCP run, starts Claude Code with an email-specific
prompt, reads the recorded MCP result, and returns a compact result payload.
If the Claude process times out or fails after recording a result, the recorded
result remains authoritative.

Bundle compatibility shims may set:

```python
EMAIL_CLAUDE_SKILLS_DESCRIPTOR
EMAIL_CLAUDE_BUNDLE_ROOT
```

to provide bundle-local skill descriptors while keeping the processor mechanics
in the SDK.

## Bundle Boundary

The SDK owns:

- Gmail OAuth and Gmail API request mechanics
- iCloud IMAP/SMTP mechanics
- account metadata and user-secret token storage helpers
- provider error normalization
- attachment fetch and ReAct artifact materialization
- Email MCP run/token/service mechanics
- Claude Code email processing mechanics

The bundle owns:

- user/admin policy and UI routes
- which conversation or task requested the mailbox operation
- account selection policy when multiple accounts are connected
- generated reports, delivery policy, and task scheduling
- Telegram, web widget, or other product-specific presentation
