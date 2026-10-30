---
id: kdcube-services@1-0
title: "KDCube Services App"
summary: "Built-in KDCube service surfaces for delegated clients: conversations, named services, and connected-account productivity tools."
status: active
tags: ["app", "bundle", "mcp", "storage", "connection-hub", "delegated-credentials", "conversations"]
keywords: ["kdcube services", "named services mcp", "google docs productivity", "delegated credentials", "signed file transfer"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube-services@1-0/interface/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
module: entrypoint
singleton: false
primary_surfaces:
  - "Widget `bundle_storage` — privileged operational storage browser"
  - "Privileged platform administration widgets — economics, conversations, gateway, Redis, and apps"
  - "MCP endpoint `conversations` — delegated access to conversations_export"
  - "MCP endpoint `named_services` — delegated access to configured namespaces, including provider-neutral spreadsheets and documents"
  - "MCP endpoint `productivity` — governed Slack, mail, Google Sheets, and Google Docs tools over connected accounts"
  - "Signed public transfer — conversation, mail, Slack, Sheets/Docs snapshots, and staged uploads"
links:
  config: config/bundles.template.yaml
  interface: interface/README.md
  openapi: interface/kdcube-services.openapi.yaml
  design: docs/README.md
  storage: docs/storage/README.md
  journal: docs/journal/README.md
---

# KDCube Services App

`kdcube-services@1-0` is the built-in app for KDCube-owned service surfaces that
external clients may access through Connection Hub delegated credentials.

It is intentionally neutral: it is not an "admin bundle" as a whole. Some tools
are admin-only because their descriptor grants are delegable only by admins.
Other future tools can be regular user services.

## Current Services

### Storage Browser

Widget:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/widgets/bundle_storage
```

`bundle_storage` is the privileged operational storage browser. It is built from
the shared SDK source:

```text
sdk://solutions/storage/ui.widget.storage
```

The widget consumes the platform admin storage APIs:

| API | Purpose |
| --- | --- |
| `/api/admin/control-plane/storage/roots` | Discover bundle storage, managed app folders, and shared storage roots. |
| `/api/admin/control-plane/storage/list` | Browse a selected local filesystem root. |
| `/api/admin/control-plane/storage/export` | Export selected files/directories as a zip. |
| `/api/admin/control-plane/storage/delete` | Delete selected files/directories after confirmation. |
| `/admin/integrations/bundles/storage-registry` | Compare managed app folders with the active registry. |

Cloud deployments must mount the browsed filesystem roots into `chat-ingress`,
because the storage APIs are served by ingress.

### Platform Administration Widgets

The app is the stable home for these privileged platform widgets:

| Alias | Purpose |
| --- | --- |
| `control_plane` | Economics control-plane dashboard. |
| `conversation_browser` | Conversation inspection and operations. |
| `svc_gateway` | Gateway monitoring. |
| `redis_browser` | Redis inspection. |
| `ai_bundles` | App registry and administration. |

All are authenticated, privileged surfaces. They are not public MCP tools.

### Conversations

MCP endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/conversations
```

Tool:

| Tool | Grant | Default delegability |
| --- | --- | --- |
| `conversations_export` | `conversations:read` | `kdcube:role:super-admin` |

This is the platform-native replacement for older root `/mcp`
conversation-export shortcuts. The OAuth protocol and consent screen remain
Connection Hub responsibilities; this bundle only owns the protected product
surface.

### Named Services

MCP endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

Tools:

| Tool | Outer Grant | Purpose |
| --- | --- | --- |
| `named_services_list` | `named_services:use` | List namespaces exposed by this MCP surface. |
| `named_services_about` | `named_services:use` | Read provider about metadata. |
| `named_services_capabilities` | `named_services:use` | Read provider capabilities for a configured namespace. |
| `named_services_schema` | `named_services:use` | Read object schema metadata. |
| `named_services_search` | `named_services:use` | Search objects and continue with a provider cursor. |
| `named_services_get` | `named_services:use` | Read one object by ref. |
| `named_services_upsert` | `named_services:use` | Create or update one object if the namespace permits `object.upsert`. |
| `named_services_host_file` | `named_services:use` | Host/register a file ref if the namespace permits `object.host_file`. |
| `named_services_action` | `named_services:use` | Run a bounded action authorized as exact `object.action.<action>`. |
| `named_services_delete` | `named_services:use` | Delete/archive one object if the namespace permits `object.delete`. |
| `named_services_call` | `named_services:use` | Generic named-service operation wrapper. |

Each namespace can require additional grants per operation. Those namespace
boundaries are not configured in this hosting bundle. Connection Hub owns the
resource consent catalog and persists the approved catalog into the delegated
credential grant record:

```yaml
connections:
  delegated_credentials:
    oauth:
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
                  search:
                    operation: object.search
                    grants: [memories:read]
                  upsert:
                    operation: object.upsert
                    grants: [memories:write]
                  action:
                    operation: object.action
                    grants: [memories:read]
                  delete:
                    operation: object.delete
                    grants: [memories:write]
                  get:
                    operation: object.get
                    grants: [memories:read]
              task:
                authority_id: delegated_client
                tools:
                  search:
                    operation: object.search
                    grants: [tasks:read]
                  upsert:
                    operation: object.upsert
                    grants: [tasks:write]
                  host_file:
                    operation: object.host_file
                    grants: [tasks:write]
                  delete:
                    operation: object.delete
                    grants: [tasks:write]
              cnv:
                authority_id: delegated_client
                tools:
                  search:
                    operation: object.search
                    grants: [canvas:read]
                  upsert:
                    operation: object.upsert
                    grants: [canvas:write]
```

The outer MCP guard checks the selected MCP tool and `named_services:use`.
The named-services bridge then checks the namespace/operation authority and
grant from the delegated credential grant record before it calls the provider.
If the delegated credential lacks a namespace grant, the tool returns a
structured `delegated_consent_required` payload. Current MCP clients do not
reliably convert that tool result into a new OAuth consent flow, so production
resources should advertise likely namespace grants during initial Connection
Hub consent.

The built-in `sheets` namespace is an ontology adapter over the same Google
Sheets service used by `public/mcp/productivity`:

```text
named_services_search namespace=sheets
  -> sheets:google:<account_id>:spreadsheet:<spreadsheet_id>
named_services_get <spreadsheet ref>
  -> metadata, tab refs, signed full-snapshot URL, or selected A1 values
react.pull <spreadsheet or tab ref>
  -> JSON snapshot rehosted into the agent turn workspace
react.read <rehosted snapshot ref>
  -> compact workbook/tab inventory; cell values stay in the local JSON
named_services_upsert / named_services_action / named_services_delete
  -> bounded spreadsheet, value, formatting, and tab mutations
```

The namespace remains provider-neutral. Google is its first backing provider.
A read requires a caller grant and connected-account claim for `sheets:read`.
A mutation requires the caller's `sheets:write` grant, while the connected
Google account must hold both `sheets:read` and `sheets:write`. These two
boundaries are checked independently.

Harness compatibility is consumer-controlled. An agent app declares
provider-backed `pull: object.get` and `block_production: block.produce`
event-source policies for `namespace: sheets`. The generic named-service
rehoster requests a `kdcube.sheets.snapshot.v1` stream with canonical identity,
metadata, and used values. A whole-file `react.read` projects a compact
inventory without cell values. Ranged reads remain exact JSON chunks, and code
can process the full snapshot by physical path. The snapshot is transient turn
input, not a copy retained by this app.

That pull/read path belongs to KDCube's ReAct adapter. External MCP clients use
the named-service operations shown above. Turnless get responses carry a
short-lived signed snapshot URL when file delivery is configured, including
beside selected inline ranges. The MCP surface does not provide `react.*`
tools.

The built-in `docs` namespace is the same kind of ontology adapter over the
Google Docs service used by `public/mcp/productivity`:

```text
named_services_search namespace=docs
  -> docs:google:<account_id>:document:<document_id>
  -> docs:google:<account_id>:source:<file_id> for DOCX/ODT/RTF imports
named_services_get <document ref>
  -> text/structure, exported bytes, comments, or a signed full-snapshot URL
named_services_action copy <source ref>
  -> editable native document ref; the source file stays unchanged
named_services_get <native document ref>
  -> text from every tab plus stable tab ids, titles, hierarchy, and end indices
named_services_upsert  -> create a document
named_services_action  -> bounded typed edits (docs:write) and comment
                          mutations (docs:comment)
```

The namespace stays provider-neutral with Google as its first backing provider.
A read requires the caller's `docs:read` grant plus the same connected-account
claim; edits require `docs:read` + `docs:write`, and comment mutations require
`docs:read` + `docs:comment` on the connected Google account. The caller-facing
grant remains the exact requested operation's grant. Docs splits reads, edits,
and comments into three claims where Sheets used two. The generic
snapshot/harness pull path is shared with `sheets`; only the object kind and the
`kdcube.docs.snapshot.v1` media type differ. See
[interface/README.md](interface/README.md) for the full operation/grant table.
On a multi-tab document, named-service edits can select one tab by title,
literal title fragment, position, or hierarchy; exact tab ids remain accepted.
Document-level comment actions can select one thread by literal text, author,
resolved state, or position. Ambiguous selectors return candidates and perform
no provider write. The stable Drive path returns a precise capability error for
tab-scoped comment requests.

### Productivity

MCP endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/productivity
```

This typed MCP surface runs over accounts the approving user connected through
Connection Hub. It currently exposes Slack search, mail search/read, Google
Sheets tools, Google Docs tools, and web search/fetch.

| Web tools | Connected account | Operator knobs |
| --- | --- | --- |
| `productivity_web_search` (multi-query discovery), `productivity_web_fetch` (dereference known URLs) | none — runs on the platform's search and fetch backends | `web.allowlist`/`web.allowlist_file` and `web.blocklist`/`web.blocklist_file` (egress filter enforced server-side before any content fetch, deny wins; a refused fetch answers `denied_by_allowlist` or `denied_by_blocklist` with the host and the list named), per-call `sites` scoping that narrows inside the filter, `web.use_llm_default` (LLM snippet relevance scoring and content refinement; off by default, no model keys needed) — under `surfaces.as_provider.mcp.productivity.web` in the descriptor |

| Sheets tools | Caller grant | Connected Google claim |
| --- | --- | --- |
| `productivity_sheets_search`, `productivity_sheets_describe`, `productivity_sheets_read` | `sheets:read` | `sheets:read` |
| values, spreadsheet, tab, and formatting mutations | `sheets:write` | `sheets:read` + `sheets:write` |

| Docs tools | Caller grant | Connected Google claim |
| --- | --- | --- |
| `productivity_docs_search`, `productivity_docs_get`, `productivity_docs_export`, `productivity_docs_list_comments`, `productivity_docs_get_comment` | `docs:read` | `docs:read` |
| document creation, copy/conversion, and typed edits (`productivity_docs_create`, `copy`, `insert/append/replace_text`, `apply_text_style`, `insert_page_break`, `embed_image`, `import`) | `docs:write` | `docs:read` + `docs:write` |
| comment mutations (`productivity_docs_create_comment`, `reply_comment`, `resolve_comment`, `delete_comment`) | `docs:comment` | `docs:read` + `docs:comment` |

The caller grant is selected under **Delegated by KDCube**. The Google claim is
approved under **Delegated to KDCube**. The app resolves the credential only
after both controls pass and never returns it to the MCP client or model.

Sheets calls use the app-owned `requirements.txt` and async `@venv` helper in
`services/productivity/google_sheets.py`; provider normalization lives in the
SDK proxy. Docs calls instead run async in-proc over raw REST in
`services/productivity/google_docs.py` (the Docs and Drive APIs via the SDK
proxy), with no venv. The
[Google Services recipe](../../../../../../../../../docs/recipes/connections/integrations/google-service-README.md)
contains the exact setup and regression walk for both.

### Signed File Transfer

The named-service MCP surface keeps binary bytes out of model context through
three session-less, signed routes:

| Alias | Method | Purpose |
| --- | --- | --- |
| `integration_file_upload` | POST | Upload one short-lived `staged:` file for a later mail/Slack action. |
| `integration_file_download` | GET | Stream a complete Mail message/attachment, Slack file, Sheets/Docs snapshot, or portable Docs export under the signed delegated-user scope. |
| `conv_file_download` | GET | Stream a `conv:fi:` conversation artifact under the signed user/conversation scope. |

These routes are public only in transport terms. A managed MCP call mints the
short-lived token, and the route trusts the verified token rather than a
browser session or query-supplied identity.

### Telegram Notify

Send text and images to the authenticated caller's own connected Telegram
account, through the deployment bot the workspace app runs on. No chat id in
any descriptor: the user links Telegram once through the bot's Mini App
(Connect tab embeds the Connection Hub widget), and this bundle resolves the
hub's identity edge per call. The integration row references the hub's
authenticator bot token and declares no webhook (the workspace bundle owns
the bot's single webhook).

| Alias | Method | Purpose |
| --- | --- | --- |
| `telegram_status` | POST | Integration state, bot deep link, and whether the caller's identity family carries a Telegram edge. |
| `telegram_send` | POST | One text message to the caller's connected account. Text-only: file-shaped keys are refused. |
| `telegram_send_images` | POST | Images/documents with optional captions: single-use `staged:` refs, public URLs, or capped inline base64. |
| `telegram_request_upload` | POST | Signed single-use upload slot for one outbound image (same staging plumbing as the named services). |

## Shape

```text
kdcube-services@1-0/
  entrypoint.py                  # thin surface adapter
  surfaces/
    mcp/
      conversations.py           # MCP tool registration
      named_services.py          # MCP named-service bridge registration
      productivity.py            # productivity surface composition
      productivity_sheets.py     # typed Google Sheets MCP registrations
  services/
    conversations/
      export.py                  # conversation export product logic
    named_services/
      bridge.py                  # grant-record namespace policy + dispatch
    productivity/
      google_sheets.py           # credential orchestration + app-owned @venv
  requirements.txt               # optional app dependencies (gspread)
  config/
    bundles.template.yaml
    bundles.secrets.template.yaml
  interface/
    README.md
    kdcube-services.openapi.yaml
  docs/
    README.md
    storage/
      README.md
    journal/
  tests/
    test_interface_contract.py
```

The reusable ontology adapter lives outside the app at
`sdk/integrations/sheets/named_service.py`; the app injects its
`GoogleSheetsService.execute` function when registering the provider.

## Auth Model

The `@mcp(..., auth_config="surfaces.as_provider.mcp.conversations.auth")`
decorator points the platform to the descriptor path for the auth policy.
The actual policy is descriptor-owned:

```yaml
surfaces:
  as_provider:
    mcp:
      conversations:
        auth:
          mode: managed
          authority_id: delegated_client
          tools:
            conversations_export:
              grants: [conversations:read]
          selected_tool_grants: true
```

When a client calls the MCP endpoint:

```text
Bearer token
  -> proc managed MCP guard
  -> Connection Hub delegated credential grant record
  -> authority_id + resource + selected tool + required grants
  -> MCP tool dispatch
```

The tool does not check roles itself. By the time it runs, the surface guard has
validated the delegated credential and selected tool grant.

The `KDCubeMCPServer` surface uses stateless streamable HTTP because the proc
bridge dispatches each bundle MCP request independently. It negotiates modern
and legacy MCP clients on the same endpoint.

For the complete ownership map, including conversation read-through storage,
provider-owned bytes, temporary upload staging, Redis coordination, generated
UI output, and the signing secret, see
[docs/storage/README.md](docs/storage/README.md). Upload slots retain their authenticated
owner on the server; upload URLs carry a reference and expiry, and consuming
actions verify the same user and deployment scope.

## Extension Rule

Add future KDCube service families as separate modules and separate MCP aliases.
For example:

```text
services/
  conversations/
  usage/
  users/
```

Each family gets its own `surfaces.as_provider.mcp.<alias>.auth` policy and
Connection Hub resource entry, so consent remains concrete and tool-centric.

For named-service republishing, add namespace boundaries under the Connection
Hub delegated credential resource metadata:
`connections.delegated_credentials.oauth.resources[].named_services.namespaces`.
This keeps the MCP service name aligned with the named-service system while
avoiding a separate MCP server per namespace.
