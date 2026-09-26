---
id: kdcube-services@1-0/interface
title: "KDCube Services Interface"
summary: "Public contract for KDCube-owned managed service widgets and MCP surfaces."
status: active
tags: ["interface", "widget", "mcp", "storage", "delegated-credentials", "connection-hub"]
keywords: ["kdcube services interface", "named services surface", "productivity mcp", "document namespace", "delegated access"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube-services@1-0/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
---

# KDCube Services — Interface

The machine-readable HTTP contract is
[kdcube-services.openapi.yaml](kdcube-services.openapi.yaml). This README owns
the human contract, including MCP tools, Data Bus and named-service surfaces,
storage/auth boundaries, and deployment configuration.

Current declared surface families:

| Family | Aliases |
| --- | --- |
| Widgets | `bundle_storage`, `app_config`, `agentic_instructions`, `control_plane`, `conversation_browser`, `svc_gateway`, `redis_browser`, `ai_bundles` |
| Operations | `bundle_storage_widget`, `app_config_widget`, `agentic_instructions_widget`, `agentic_instructions`, `control_plane`, `conversation_browser`, `svc_gateway`, `redis_browser`, `ai_bundles` |
| MCP | `conversations`, `named_services`, `productivity` |
| Signed public files | `integration_file_upload`, `integration_file_download`, `conv_file_download`, `provider_fetch_download` |
| Data Bus | `kdcube.named_service.relay.v1` |

See [../docs/storage/README.md](../docs/storage/README.md) for the authority and
storage map behind these surfaces.

## Widget: `bundle_storage`

```text
GET /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/widgets/bundle_storage
```

Visibility: privileged platform users.

Static source:

```text
sdk://solutions/storage/ui.widget.storage
```

Backend APIs:

| API | Runtime | Purpose |
| --- | --- | --- |
| `/api/admin/control-plane/storage/roots` | ingress | Discover browsable storage roots and availability. |
| `/api/admin/control-plane/storage/tenants-projects` | ingress | Discover tenant/project folders for scoped roots. |
| `/api/admin/control-plane/storage/list` | ingress | Browse selected local filesystem path. |
| `/api/admin/control-plane/storage/export` | ingress | Export selected files/directories. |
| `/api/admin/control-plane/storage/delete` | ingress | Delete selected files/directories. |
| `/admin/integrations/bundles/storage-registry` | proc | Read active app registry storage references. |

Ingress must have the same local storage roots mounted that the widget is
allowed to browse. In ECS that means `/kdcube-storage`, `/bundle-storage`, and
`/bundles` are mounted into `chat-ingress`.

## Privileged Platform Widgets

All widget and matching operation surfaces below require a privileged platform
session:

| Widget alias | Operation alias | Purpose |
| --- | --- | --- |
| `bundle_storage` | `bundle_storage_widget` | Browse permitted platform storage roots. |
| `app_config` | `app_config_widget` | Inspect and edit app configuration. |
| `agentic_instructions` | `agentic_instructions_widget` | Configure and preview agent instruction sets through the `agentic_instructions` operation. |
| `control_plane` | `control_plane` | Economics control plane. |
| `conversation_browser` | `conversation_browser` | Inspect conversation state and artifacts. |
| `svc_gateway` | `svc_gateway` | Monitor gateway behavior. |
| `redis_browser` | `redis_browser` | Inspect Redis state. |
| `ai_bundles` | `ai_bundles` | Inspect and administer installed apps. |

## MCP Endpoint: Conversations

```text
POST /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/conversations
```

Transport: `streamable-http`

Auth: platform-managed delegated credential, configured at:

```text
surfaces.as_provider.mcp.conversations.auth
```

Default policy:

```yaml
mode: managed
authority_id: delegated_client
tools:
  conversations_export:
    grants:
      - conversations:read
selected_tool_grants: true
```

`auth_config` in `entrypoint.py` is only the pointer to this policy path.
Descriptors own the actual grants and tool allowlist.

## Tool: `conversations_export`

Purpose: read-only conversation transcript export for feedback triage and
operational review.

Arguments:

| Name | Type | Description |
| --- | --- | --- |
| `since` | string | Optional ISO timestamp. Limits to conversations started at or after this time. |
| `tenant` | string | Optional tenant id. Must be supplied together with `project`. |
| `project` | string | Optional project id. Must be supplied together with `tenant`. |
| `limit` | integer | Maximum returned conversation records. Clamped to `1..500`. |

Result:

```json
{
  "ok": true,
  "count": 10,
  "total_available": 10,
  "limited": false,
  "conversations": []
}
```

Each conversation record contains:

```text
conversation_id
tenant
project
user_id
source
started_at
title
turns[]
```

Each turn contains:

```text
turn_id
ts
user
assistant
attachments[]
citations[]
```

## Consent And Resource Metadata

Connection Hub must include a resource entry matching the endpoint URL:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/conversations*"
          label: "KDCube conversations MCP"
          tools:
            conversations_export:
              label: "Export conversations"
              description: "Read conversation transcripts for feedback triage."
              grants:
                - conversations:read
```

The capability grant remains separately configured:

```yaml
capabilities:
  - grant: conversations:read
    delegable_roles:
      - kdcube:role:super-admin
```

This split lets Connection Hub show concrete tools for the requested resource
while still checking whether the approving user may delegate each grant.

## MCP Endpoint: Named Services

```text
POST /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

Transport: `streamable-http`

Auth: platform-managed delegated credential, configured at:

```text
surfaces.as_provider.mcp.named_services.auth
```

Default outer policy:

```yaml
mode: managed
authority_id: delegated_client
tools:
  named_services_list:
    grants: [named_services:use]
  named_services_about:
    grants: [named_services:use]
  named_services_schema:
    grants: [named_services:use]
  named_services_search:
    grants: [named_services:use]
  named_services_get:
    grants: [named_services:use]
  named_services_call:
    grants: [named_services:use]
selected_tool_grants: true
```

Namespace boundary policy lives in Connection Hub resource metadata, not in the
hosting bundle MCP auth section:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          named_services:
            namespaces:
              mem:
                label: User memories
                authority_id: delegated_client
                tools:
                  about:
                    operation: provider.about
                    grants: [memories:read]
                  schema:
                    operation: object.schema
                    grants: [memories:read]
                  search:
                    operation: object.search
                    grants: [memories:read]
                  get:
                    operation: object.get
                    grants: [memories:read]
```

Tools:

| Tool | Description |
| --- | --- |
| `named_services_list` | List configured namespaces and per-operation grants. |
| `named_services_about(namespace)` | Read provider about metadata. |
| `named_services_capabilities(namespace)` | Read provider capabilities. |
| `named_services_schema(namespace, object_kind?)` | Read provider object schema. |
| `named_services_search(namespace, query?, limit?, cursor?, filters_json?)` | Search namespace objects and continue with a provider `next_cursor`. |
| `named_services_get(namespace, object_ref, filters_json?)` | Read one object by ref, with optional provider filters such as selected sheet ranges. |
| `named_services_upsert(namespace, object_json, ...)` | Create or update one object when `object.upsert` is allowed. |
| `named_services_host_file(namespace, file_ref, ...)` | Host/register one file ref when `object.host_file` is allowed. |
| `named_services_action(namespace, object_ref, action, ...)` | Run a bounded action authorized as exact `object.action.<action>`. |
| `named_services_delete(namespace, object_ref, ...)` | Delete/archive one object when `object.delete` is allowed. |
| `named_services_call(operation, namespace, ...)` | Generic operation wrapper. |

When a namespace operation needs a grant that the delegated credential lacks,
the tool returns:

```json
{
  "ok": false,
  "error": "delegated_consent_required",
  "namespace": "mem",
  "operation": "object.schema",
  "required_grants": ["memories:read"],
  "missing_grants": ["memories:read"]
}
```

That result is the provider-boundary signal. It does not guarantee that every
MCP client will automatically open an incremental OAuth flow. For current
Claude-facing resources, include likely namespace grants in the initial
Connection Hub resource metadata as a nested namespace/tool catalog when a
one-step user experience is required:

```yaml
resources:
  - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
    tools:
      named_services_schema:
        grants: [named_services:use]
    named_services:
      namespaces:
        mem:
          authority_id: delegated_client
          tools:
            schema:
              operation: object.schema
              grants: [memories:read]
```

The protected-resource discovery document exposes this nested catalog as
`kdcube_named_services`, next to the generic `kdcube_tools` list. The OAuth
authorization code, refresh token, and access-grant record then preserve the
same catalog for runtime enforcement.

### Named-service namespace: `sheets`

The `sheets` namespace exposes connected spreadsheets through the generic tools
above. Google Sheets is the first backing provider, but the agent contract uses
provider-neutral object kinds and refs:

```text
sheets.spreadsheet
  sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>

sheets.tab
  sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>:tab:<sheet_id>
```

| Named-service operation | Behavior | Required namespace grants |
| --- | --- | --- |
| `object.list` | List recently modified spreadsheets. | `named_services:use`, `sheets:read` |
| `object.search` | Find spreadsheets by title. | `named_services:use`, `sheets:read` |
| `object.get` | Read metadata or selected A1 ranges; a turnless result can carry a signed full-snapshot URL, and stream mode returns a materializable spreadsheet/tab snapshot. | `named_services:use`, `sheets:read` |
| `object.upsert` | Create a spreadsheet, replace values, or update tab properties. | `named_services:use`, `sheets:write` |
| `object.action` | Append/clear values, manage tabs, or format one bounded range. | `named_services:use`, `sheets:write` |
| `object.delete` | Delete one tab. Spreadsheet-file deletion is not exposed. | `named_services:use`, `sheets:write` |

`object.schema` is authoritative for action payloads and bounds. Range reads
use the parent spreadsheet ref and place the tab title in each A1 range. The
adapter calls the same `GoogleSheetsService` as the typed productivity surface,
so credential custody, account selection, claim-upgrade behavior, provider
limits, and app-owned venv execution do not diverge.

For `response_mode=stream`, `object.get` returns media type
`application/vnd.kdcube.sheets.snapshot+json;version=1`. The streamed
`kdcube.sheets.snapshot.v1` document contains the canonical `object_ref`, owner
metadata, and used values for selected grid tabs. KDCube does not impose a
read-range or returned-cell ceiling. Provider failures remain explicit rather
than returning a silently incomplete artifact.

The provider also implements internal `event.resolve` and `block.produce`
operations for configured harness consumers. A whole-file read renders only
workbook/tab inventory, counts, completeness, and workspace paths. It does not
render cell values. Explicit ranged reads bypass that projection and expose the
requested JSON chunk, allowing `react.rg` results to feed `react.read`. These
internal owner operations are not additional public MCP tools.

External MCP clients therefore stay on `named_services.*`: they search, inspect
metadata, fetch the signed complete snapshot URL, request selected A1 ranges
inline, and mutate through those tools. `react.pull`, `react.read`, and
`react.rg` exist only when a KDCube ReAct consumer enables this event-source
adapter. The snapshot response is an async chunked JSON stream and therefore
does not need a precomputed `Content-Length`; this does not truncate the
artifact.

The table lists caller grants under **Delegated by KDCube**. On the separate
connected-account boundary, reads require `sheets:read` and mutations require
both `sheets:read` and `sheets:write` from the approving user's Google account.

### Named-service namespace: `docs`

The `docs` namespace exposes connected documents through the same generic tools.
Google Docs is the first backing provider; the agent contract stays
provider-neutral:

```text
docs.document
  docs:<provider>:<account_id>:document:<document_id>
docs.import_source
  docs:<provider>:<account_id>:source:<file_id>
```

| Named-service operation | Behavior | Required namespace grants |
| --- | --- | --- |
| `object.search` | Find native documents and compatible DOCX/ODT/RTF import sources by provider or logical title. | `named_services:use`, `docs:read` |
| `object.get` | Read native text from every tab plus tab ids, titles, hierarchy, and end indices, or inspect import-source metadata; a turnless native result can carry a signed full-snapshot URL, and stream mode returns a materializable snapshot. | `named_services:use`, `docs:read` |
| `object.upsert` | Create a document. | `named_services:use`, `docs:write` |
| `object.action` | `copy` preserves a native Doc or converts an import source into a new native Doc. Native refs also support typed edits, export, and comment actions authorized as exact `object.action.<action>`. Multi-tab edits accept declared natural tab selectors; document-level comments accept declared natural comment selectors. | edits: `named_services:use`, `docs:write`; reads: `named_services:use`, `docs:read`; comments: `named_services:use`, `docs:comment` |

Document deletion is not exposed; comment deletion is the `delete_comment`
action. `object.schema` is authoritative for action payloads and bounds. Edits
are typed `batchUpdate` requests, never raw document JSON. The adapter calls the
same `GoogleDocsService` as the typed productivity surface, so credential custody,
account selection, claim-upgrade behavior, and provider limits do not diverge.
Unlike Sheets, the Docs service runs async in-proc over raw REST (no venv).

For `response_mode=stream`, `object.get` returns media type
`application/vnd.kdcube.docs.snapshot+json;version=1` carrying a
`kdcube.docs.snapshot.v1` document. The shared snapshot, signed-URL, and
`react.pull`/`react.read` harness mechanics are the same as the `sheets`
namespace above; only the object kind and media type differ.

The table lists caller grants under **Delegated by KDCube**. On the separate
connected-account boundary, reads require `docs:read`; creation, copy/conversion,
and edits require `docs:read` + `docs:write`; comment mutations require
`docs:read` + `docs:comment` from the approving user's Google account.

## MCP Endpoint: Productivity

```text
POST /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/productivity
```

Transport: `streamable-http`

Auth: managed delegated credential at
`surfaces.as_provider.mcp.productivity.auth`, with selected tool grants.

The outer guard authorizes the caller and selected tool. Each tool then checks
its connected-account provider claim before resolving a live credential.

| Group | Tools | Connected-account claims |
| --- | --- | --- |
| Find and inspect | `productivity_sheets_search`, `productivity_sheets_describe` | `sheets:read` |
| Read values | `productivity_sheets_read` | `sheets:read` |
| Write values | `productivity_sheets_update_values`, `productivity_sheets_append_rows`, `productivity_sheets_clear_values` | `sheets:read`, `sheets:write` |
| Structure | `productivity_sheets_create_spreadsheet`, `productivity_sheets_add_tab`, `productivity_sheets_update_tab`, `productivity_sheets_delete_tab` | `sheets:read`, `sheets:write` |
| Presentation | `productivity_sheets_format_range` | `sheets:read`, `sheets:write` |

The `productivity_docs_*` tool group covers Google Docs over the same connected
account, split into three claims:

| Group | Tools | Connected-account claims |
| --- | --- | --- |
| Find and read | `productivity_docs_search`, `productivity_docs_get`, `productivity_docs_export`, `productivity_docs_read_image`, `productivity_docs_list_comments`, `productivity_docs_get_comment` | `docs:read` |
| Edit | `productivity_docs_create`, `productivity_docs_copy`, `productivity_docs_insert_text`, `productivity_docs_append_text`, `productivity_docs_replace_text`, `productivity_docs_apply_text_style`, `productivity_docs_insert_page_break`, `productivity_docs_embed_image`, `productivity_docs_set_cells`, `productivity_docs_add_row`, `productivity_docs_add_tab`, `productivity_docs_update_tab`, `productivity_docs_delete_tab`, `productivity_docs_import` | `docs:read`, `docs:write` |
| Trash | `productivity_docs_trash`, `productivity_docs_restore` | `docs:read`, `docs:delete` |
| Comment | `productivity_docs_create_comment`, `productivity_docs_reply_comment`, `productivity_docs_update_comment`, `productivity_docs_resolve_comment`, `productivity_docs_delete_comment` | `docs:read`, `docs:comment` |
| Drive files as themselves | `productivity_drive_upload_file` (no conversion, optional folder placement), `productivity_drive_list_folder` | `drive:write` / `drive:read` |

Import accepts an optional `parent_id` Drive folder; placement into a
pre-existing folder additionally needs `drive:write` on the connected account,
because `docs:write` carries only the `drive.file` scope, which cannot reach
folders the connector app never created or opened.

Mail is a REALM whose members are DISCOVERED from the Connection Hub catalog
by adapter family: the Google OAuth provider (Gmail claims) plus every provider
instance on the `email.imap_smtp_app_password` adapter (iCloud Mail, Yahoo, a
company server: each a descriptor block carrying its IMAP/SMTP hosts under
`settings:`; the SDK names no provider instance). `productivity_mail_accounts`
(claim-free) lists every connected mailbox with its `account_id` and what it may
do; `productivity_mail_search`, `_get`, and `_draft` take that `account_id`.
Their gate is the same enforcement every tool uses, declared at call time as an
`any_of` group over the members' own claims: one eligible mailbox is used, two
eligible mailboxes answer `account_required` with provider-labeled candidates
(ask the user, never pick a provider), none leads with the connect-first
consent naming every member, and the chosen account is enforced on its OWN
claim (`gmail:*` or `email:*`) before its transport runs (Gmail API, or
IMAP/SMTP with the instance's hosts). `productivity_mail_draft` creates a DRAFT
the person sends themselves, deliberately without send authority: Gmail via
`drafts.create` (`gmail:compose`), IMAP/SMTP via an APPEND into Drafts
(`email:send`, a mailbox write), attachments as inline base64 entries. The
`mail` named service follows the same discovery: `object.list` spans every
member, message refs name their provider (`mail:gmail:…`,
`mail:icloud_mail:…`), and the `draft` action joins `send`; IMAP/SMTP `forward`
and attachment download are not implemented yet and say so.

Search uses Google Drive metadata. Docs search returns native documents and
compatible DOCX/ODT/RTF import sources; extensionless queries can exactly match
their logical title. Copy converts an import source into a new native Google Doc
and leaves the source unchanged. Later calls accept the returned stable
spreadsheet/document id or a full Google Sheets/Docs URL. Sheets describe
returns stable sheet ids. A Docs read returns every document tab with its stable
tab id and body indices. Through `named_services`, multi-tab edits can resolve
tabs by title, literal title fragment, position, or hierarchy, and
document-level comment actions can resolve one thread by text, author, resolved
state, or position. Exact ids remain valid for direct typed operations.
Ambiguity returns candidates and performs no write; a tab-scoped comment request
returns `tab_anchored_comments_unavailable`. MCP `tools/list` is authoritative
for typed parameters and bounds. Docs edits are typed `batchUpdate` operations,
never raw JSON, and comment operations require `docs:comment`, distinct from the
`docs:write` edit claim.

The Google token crosses only from Connection Hub into the trusted app service
(Sheets through an app-owned venv subprocess; Docs async in-proc). It is never an
MCP argument or result. See
the [Google Services recipe](../../../../../../../../../../docs/recipes/connections/integrations/google-service-README.md)
for provider claims, resource configuration, consent, and verification.

## Signed File Routes

Binary transfer is out-of-band so MCP JSON does not inject file bytes into the
model context:

| Alias | Method | Required query | Result |
| --- | --- | --- | --- |
| `integration_file_upload` | POST | `object_ref`, `upload_token` | Raw request bytes become a short-lived, single-use `staged:` ref. |
| `provider_fetch_download` | GET | `object_ref`, `download_token` | One staged file, served to a provider that fetches it with no identity of its own. The token binds that one staged ref and expires within a minute by default; the action that staged the file deletes it as soon as the provider call returns, after which this answers `fetch_file_gone`. |
| `integration_file_download` | GET | `object_ref`, `download_token` | Complete Mail message JSON, raw Mail attachment/Slack file bytes, a Sheets/Docs JSON snapshot, or a portable Google Docs export with `Content-Disposition` and `private, no-store`. |
| `conv_file_download` | GET | `object_ref`, `download_token` | Raw conversation artifact bytes under token-bound user/conversation scope. |

The routes require no browser session because the HMAC token binds the exact
ref, user, tenant/project, optional conversation, and expiry. The signing key
is `conversations.file_download_secret` in app secrets. Missing secret fails
closed and no usable URL is emitted.

Mail, Slack, and Sheets delivery URLs are live provider proxies. On every GET,
the route verifies the signed identity, resolves the user's current connected
credential through Connection Hub, checks the current provider claim, fetches
the current provider object, and streams it. Provider credentials never enter
the URL or response. The delegated caller grant is checked when the URL is
minted; the URL itself is the short-lived GET capability. Connected-account
revocation blocks an existing URL, while revoking only the caller grant blocks
minting a replacement URL. ReAct/harness `pull` uses the same provider stream
but writes a stable copy into the current turn workspace.

Uploads are limited to 25 MiB and staged for at most one hour as a cleanup
backstop. See the storage map for the current filesystem-sharing constraint.

## Data Bus Relay

`kdcube.named_service.relay.v1` is an idempotent Data Bus handler for detached
runtimes that cannot hold an in-process named-service registry caller. It
dispatches the request under the message actor context and returns the recorded
response for redelivery. This is not a public REST route.

## Dataflow

```text
Claude / external MCP client
  -> resource URL: /public/mcp/conversations
  -> discovers Connection Hub OAuth metadata
  -> user signs in to KDCube
  -> consent screen shows conversations_export
  -> access token is issued with selected tool + conversations:read
  -> MCP tools/list / tools/call
  -> proc managed MCP guard validates token/resource/tool/grant
  -> kdcube-services MCP tool                     (bundle: tool schema/wrapper)
  -> export_current_user_conversations            (SDK: sdk/solutions/conversation/mcp_export.py)
  -> ConversationExportService                    (SDK: sdk/solutions/conversation/export.py)
  -> ConversationReadService                      (SDK: sdk/solutions/conversation/read.py)
```

The export implementation is SDK-owned and user-scoped. The bundle publishes
only the MCP tool schema; the implementation runs through
`sdk/solutions/conversation/mcp_export.py` and the same read/export facade used
by the `conv` named service. The tool contract above is unchanged by that split.
