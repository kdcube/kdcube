---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
title: "Google Services Through KDCube (Gmail, Sheets, Docs)"
summary: "One recipe for connecting Google services to KDCube: one Google OAuth client, one google provider, one gmail connector app serving Gmail, Sheets, and Docs (extensible to Drive/Calendar). Configure provider claims, wire each service's tools and named services, connect, grant, and verify."
status: active
tags: ["recipes", "connections", "connection-hub", "google", "gmail", "sheets", "docs", "oauth", "connected-accounts", "delegated-to-kdcube", "mcp"]
keywords: ["google connected account", "google docs named service", "google sheets tools", "google oauth scopes", "document tab selector", "document comment selector", "document table cells", "set_cells"]
updated_at: 2026-09-21
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/frontend/application/integrations/google.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/google/google-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/resolve-connected-credential-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/provider-error-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/named-services-mcp-README.md
---
# Google Services Through KDCube (Gmail, Sheets, Docs)

Use this recipe to let a signed-in KDCube user connect their own Google account,
then let KDCube tools and named services act on that user's behalf across Google
services. This is the **delegated to KDCube** direction:

```text
Google user
  -> user consents in Google OAuth
  -> Connection Hub stores the connected account credential
  -> KDCube tool/named service resolves that credential for the current user
  -> tool calls the Google API with the user's delegated Google token
```

**One client serves every Google service.** One Google OAuth client, one
`google` provider (`adapter: google.oauth`), and one `gmail` connector app back
Gmail, Sheets, Docs, and any Drive/Calendar service added the same way. Each
service only adds claims, provider scopes, and tools; it does not add an OAuth
client, adapter, or code.

## Operator setup (external)

The Google Cloud work happens **outside** KDCube and is documented once in the
bundle-local operator doc. Do it there, not here:
[Connection Hub - Google (Gmail, Sheets, and Docs) setup](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/frontend/application/integrations/google.md).

That doc covers, in one place:

- the Google Cloud project and OAuth Web-application client;
- the delegated-to-KDCube **Authorized redirect URIs** (the callback path ends
  with `.../connection-hub@1-0/public/delegated_to_kdcube_oauth_callback`) for
  the local, custom-authority, demo, and dev runtimes;
- enabling the per-service product APIs in the same project (Gmail API for mail;
  Sheets API plus Drive API for spreadsheets; Docs API plus Drive API for
  documents);
- the client id/secret keys and the hub-level `oauth_state_secret`.

A completed Google OAuth connection proves identity and consent worked; it does
not prove a product API is enabled. Enable each service's API in the same Google
Cloud project that owns the OAuth client.

## Configure provider claims

Under the `connection-hub@1-0` item at
`config.connections.delegated_to_kdcube.providers.google`, allow every claim on
the one `gmail` connector app and define each claim's provider scopes. Add only
the services you use; the block below shows Gmail, Sheets, and Docs together:

```yaml
connector_apps:
  gmail:
    label: Gmail
    enabled: true
    client_id: "<GOOGLE_OAUTH_CLIENT_ID>"
    client_secret_ref: connections.delegated_to_kdcube.providers.google.connector_apps.gmail.client_secret
    allowed_claims:
      - gmail:read
      - gmail:send
      - sheets:read
      - sheets:write
      - docs:read
      - docs:write
      - docs:comment
claims:
  gmail:read:
    label: Read Gmail
    description: Search and read Gmail messages for the approving user.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/gmail.readonly
  gmail:send:
    label: Send Gmail
    description: Send email through the approving user's Gmail account.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/gmail.send
  sheets:read:
    label: Read Google Sheets
    description: Find spreadsheets and read their metadata and values.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/spreadsheets.readonly
      - https://www.googleapis.com/auth/drive.metadata.readonly
  sheets:write:
    label: Edit Google Sheets
    description: Create and edit spreadsheets, tabs, values, and formatting.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/spreadsheets
      - https://www.googleapis.com/auth/drive.metadata.readonly
      # Creating a spreadsheet creates a Drive FILE (gspread's create() posts to
      # the Drive API), so a Drive WRITE scope is required. drive.file is the
      # least-privilege one: the app may create and manage only the files it
      # makes. Without it, create fails with "Request had insufficient
      # authentication scopes" even though spreadsheets (read/write) is granted.
      - https://www.googleapis.com/auth/drive.file
  docs:read:
    label: Read Google Docs
    description: Find documents, read their text and structure, export to a
      format, and read comments.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/documents.readonly
      # Docs read uses drive.readonly, not sheets' drive.metadata.readonly:
      # get/export stream the document's Drive content (full text, exported
      # bytes), not only file metadata; search reads document-file metadata.
      - https://www.googleapis.com/auth/drive.readonly
  docs:write:
    label: Edit Google Docs
    description: Create or copy documents and apply typed edits -
      insert/append/replace text, text styling, page breaks, embedded images,
      and import.
    provider_scopes:
      - openid
      - email
      - profile
      - https://www.googleapis.com/auth/documents
      # create posts to the Drive API and import uploads a Drive FILE, so a
      # Drive WRITE scope is required. drive.file is least-privilege: the app
      # may create and manage only the files it makes - same rule as Sheets.
      - https://www.googleapis.com/auth/drive.file
  docs:comment:
    label: Comment on Google Docs
    description: List, create, reply to, resolve, and delete comments on a
      document through the Drive comments API.
    provider_scopes:
      - openid
      - email
      - profile
      # Comments (and export) are Drive operations that act on ANY document the
      # user names, including ones this app did not create, so drive.file (which
      # covers only app-created files) is insufficient here - the full drive
      # scope is required.
      - https://www.googleapis.com/auth/drive
```

The client secret stays in `bundles.secrets.yaml` at the `client_secret_ref`
above (see the operator doc). Adding Sheets to a deployment that already had
Gmail adds no new secret and no environment variable.

**Read-write scopes supersede read-only ones.** When one connect requests both a
Google scope and its read-only sibling (`spreadsheets` and
`spreadsheets.readonly`), Google grants the read-only one and drops the
read-write one, so later writes fail with
`Request had insufficient authentication scopes`. The `google.oauth` adapter
reconciles this at connect time - it drops any `<X>.readonly` whose read-write
base `<X>` is also requested. You still declare each claim's minimal scope as
above. Gmail is unaffected (`gmail.send` is not the read-only sibling of
`gmail.readonly`). The full scope machinery is in the SDK doc,
[Google SDK Integration](../../../sdk/integrations/google/google-README.md).

## Per-service wiring

Each service adds its own tools (and optionally a named-service namespace) on top
of the shared provider claims above.

### Gmail

Give the main agent the Gmail tool module and declare each tool's connected-account
claims. A tool names the provider and claims it needs, never the connector app -
the broker resolves the account at call time:

```yaml
- name: gmail
  kind: python
  module: kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools
  alias: gmail
  allowed:
    - search_gmail
    - read_gmail_message
    - download_gmail_attachments
    - send_gmail
    - forward_gmail_message
  tool_claims:
    search_gmail:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: google
              claims: [gmail:read]
    read_gmail_message:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: google
              claims: [gmail:read]
    download_gmail_attachments:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: google
              claims: [gmail:read]
    send_gmail:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: google
              claims: [gmail:send]
    forward_gmail_message:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: google
              claims: [gmail:read, gmail:send]
```

The same connected Gmail account can also be exposed to external agents through
the provider-neutral `mail` named-service namespace on
`kdcube-services@1-0/public/mcp/named_services`. That adds a second delegated
consent layer (KDCube grants `mail:read`/`mail:send`; the connected account holds
`gmail:read`/`gmail:send`). See
[Mail Named Service Over MCP](mail-named-service-README.md) for the namespace
refs, MCP operations, and Connection Hub boundary config.

### Google Sheets

Sheets has two MCP doors on the built-in `kdcube-services@1-0` app: the typed
`productivity_sheets_*` tools (`public/mcp/productivity`) and the generic
`sheets` named-service namespace (`public/mcp/named_services`). Both call the
same bounded async app service through an app-owned `@venv` subprocess
(`gspread`); the agent never receives a Google token.

Declare the managed surface and select the existing connector app:

```yaml
config:
  surfaces:
    as_provider:
      mcp:
        productivity:
          auth:
            mode: managed
            authority_id: delegated_client
            selected_tool_grants: true
          connector_apps:
            google: gmail
            slack: slack-demo
        named_services:
          auth:
            mode: managed
            authority_id: delegated_client
            selected_tool_grants: true
```

`resolve_connector_app_id("google")` reads this `connector_apps.google: gmail`
declaration; tool code does not hard-code the OAuth client. Add the optional
dependency to `kdcube-services@1-0/requirements.txt` (`gspread==6.2.1`) and
refresh the app so it builds the cached venv.

Publish the delegable capabilities and tools in Connection Hub under
`config.connections.delegated_credentials.oauth`. For the typed productivity
door, grant each `productivity_sheets_*` tool:

```yaml
resources:
  - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/productivity*"
    label: KDCube productivity MCP
    tools:
      productivity_sheets_search:            {grants: [sheets:read]}
      productivity_sheets_describe:          {grants: [sheets:read]}
      productivity_sheets_read:              {grants: [sheets:read]}
      productivity_sheets_update_values:     {grants: [sheets:write]}
      productivity_sheets_append_rows:       {grants: [sheets:write]}
      productivity_sheets_clear_values:      {grants: [sheets:write]}
      productivity_sheets_create_spreadsheet:{grants: [sheets:write]}
      productivity_sheets_add_tab:           {grants: [sheets:write]}
      productivity_sheets_update_tab:        {grants: [sheets:write]}
      productivity_sheets_delete_tab:        {grants: [sheets:write]}
      productivity_sheets_format_range:      {grants: [sheets:write]}
```

For the generic named-services door, add the `sheets` namespace to its resource.
The bridge dispatches an action to provider `object.action` but authorizes the
exact `object.action.<action>`, so granting one Sheets mutation does not grant
every mutation:

```yaml
- resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  label: KDCube named services MCP
  named_services:
    connector_apps:
      google: gmail
    namespaces:
      sheets:
        label: Spreadsheets
        authority_id: delegated_client
        tools:
          about:        {operation: provider.about,        grants: [named_services:use]}
          capabilities: {operation: provider.capabilities, grants: [named_services:use]}
          list:         {operation: object.list,           grants: [named_services:use, sheets:read]}
          schema:       {operation: object.schema,         grants: [named_services:use]}
          search:       {operation: object.search,         grants: [named_services:use, sheets:read]}
          get:          {operation: object.get,            grants: [named_services:use, sheets:read]}
          upsert:       {operation: object.upsert,         grants: [named_services:use, sheets:write]}
          action:
            operation: object.action
            operations:
              object.action.update_values: {grants: [named_services:use, sheets:write]}
              object.action.append_rows:   {grants: [named_services:use, sheets:write]}
              object.action.clear_values:  {grants: [named_services:use, sheets:write]}
              object.action.add_tab:       {grants: [named_services:use, sheets:write]}
              object.action.update_tab:    {grants: [named_services:use, sheets:write]}
              object.action.delete_tab:    {grants: [named_services:use, sheets:write]}
              object.action.format_range:  {grants: [named_services:use, sheets:write]}
          delete:       {operation: object.delete,         grants: [named_services:use, sheets:write]}
```

These are caller grants under **Delegated by KDCube**. The separate connected
Google account uses `sheets:read` for reads and `sheets:write` for mutations.
Give each capability the roles/permissions your deployment allows to delegate.

**Connect and grant.**

1. Open **Connection Hub -> Delegated to KDCube** and connect the Google account.
   An account connected earlier for Gmail receives `claim_upgrade_required` when
   Sheets access is first requested; approve it.
2. Open **Connection Hub -> Delegated by KDCube** for the agent or external MCP
   client, select only the required Sheets tools and account claims, and save.

The caller grant never contains the Google token. It binds the caller, the
selected resource/tools, the KDCube grants, and the user's approved connected
account.

Spreadsheet and tab refs are provider-neutral at the agent boundary:

```text
sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>
sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>:tab:<sheet_id>
```

Search returns at most 50 results. Mutations are bounded: at most 20 ranges and
10,000 cells per write, 1,000 appended rows, and 1,000,000 cells in a new or
resized tab. Reads preserve every value the provider returns. `append_rows` and
`create_spreadsheet` are not exactly-once; on an `outcome_unknown` transport
failure, inspect/search before retrying. Provider failures preserve Google's safe
message plus `provider_status`, `provider_code`, `provider_reason`, `stage`, and
`retryable`, per the
[Provider Error And Observability Contract](../../../sdk/integrations/provider-error-contract-README.md).

The SDK mechanics behind these tools (the async gspread proxy, the credential
resolver, and the snapshot artifacts) are in
[Google SDK Integration](../../../sdk/integrations/google/google-README.md).

### Google Docs

Docs has the same two MCP doors on `kdcube-services@1-0` as Sheets: the typed
`productivity_docs_*` tools (`public/mcp/productivity`) and the generic `docs`
named-service namespace (`public/mcp/named_services`). Both call the same bounded
async app service; the agent never receives a Google token. Docs differs from
Sheets in one mechanical way: the proxy speaks raw REST to the Docs API and the
Drive API over async `httpx`, so it runs in-proc with no `@venv`/`gspread`
subprocess. No new connector app, adapter, or requirement is added.

The same managed `productivity` and `named_services` surfaces already declared
for Sheets serve Docs; no additional surface wiring is needed. Publish the
delegable Docs capabilities and tools in Connection Hub under
`config.connections.delegated_credentials.oauth`.

Docs splits into three connected-account claims (Sheets had two). `docs:read`
and `docs:write` together cover document creation, copy/conversion, and typed
edits; `docs:read` alone covers find, read, export, and reading comments;
`docs:read` and `docs:comment` together cover comment mutations. For the typed
productivity door, grant each caller-visible
`productivity_docs_*` tool:

```yaml
resources:
  - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/productivity*"
    label: KDCube productivity MCP
    tools:
      productivity_docs_search:           {grants: [docs:read]}
      productivity_docs_get:              {grants: [docs:read]}
      productivity_docs_export:           {grants: [docs:read]}
      productivity_docs_list_comments:    {grants: [docs:read]}
      productivity_docs_get_comment:      {grants: [docs:read]}
      productivity_docs_create:           {grants: [docs:write]}
      productivity_docs_copy:             {grants: [docs:write]}
      productivity_docs_insert_text:      {grants: [docs:write]}
      productivity_docs_append_text:      {grants: [docs:write]}
      productivity_docs_replace_text:     {grants: [docs:write]}
      productivity_docs_apply_text_style: {grants: [docs:write]}
      productivity_docs_insert_page_break:{grants: [docs:write]}
      productivity_docs_embed_image:      {grants: [docs:write]}
      productivity_docs_set_cells:        {grants: [docs:write]}
      productivity_docs_import:           {grants: [docs:write]}
      productivity_docs_create_comment:   {grants: [docs:comment]}
      productivity_docs_reply_comment:    {grants: [docs:comment]}
      productivity_docs_resolve_comment:  {grants: [docs:comment]}
      productivity_docs_delete_comment:   {grants: [docs:comment]}
```

For the generic named-services door, add the `docs` namespace alongside `sheets`
on its resource. As with Sheets, the bridge authorizes the exact
`object.action.<action>`, so granting one edit does not grant every edit, and the
comment mutations key on `docs:comment` rather than `docs:write`:

```yaml
- resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  label: KDCube named services MCP
  named_services:
    connector_apps:
      google: gmail
    namespaces:
      docs:
        label: Documents
        authority_id: delegated_client
        tools:
          about:        {operation: provider.about,        grants: [named_services:use]}
          capabilities: {operation: provider.capabilities, grants: [named_services:use]}
          schema:       {operation: object.schema,         grants: [named_services:use]}
          search:       {operation: object.search,         grants: [named_services:use, docs:read]}
          get:          {operation: object.get,            grants: [named_services:use, docs:read]}
          upsert:       {operation: object.upsert,         grants: [named_services:use, docs:write]}
          action:
            operation: object.action
            operations:
              object.action.copy:              {grants: [named_services:use, docs:write]}
              object.action.insert_text:       {grants: [named_services:use, docs:write]}
              object.action.append_text:       {grants: [named_services:use, docs:write]}
              object.action.replace_text:      {grants: [named_services:use, docs:write]}
              object.action.apply_text_style:  {grants: [named_services:use, docs:write]}
              object.action.insert_page_break: {grants: [named_services:use, docs:write]}
              object.action.embed_image:       {grants: [named_services:use, docs:write]}
              object.action.set_cells:         {grants: [named_services:use, docs:write]}
              object.action.export:            {grants: [named_services:use, docs:read]}
              object.action.import:            {grants: [named_services:use, docs:write]}
              object.action.list_comments:     {grants: [named_services:use, docs:read]}
              object.action.get_comment:       {grants: [named_services:use, docs:read]}
              object.action.create_comment:    {grants: [named_services:use, docs:comment]}
              object.action.reply_comment:     {grants: [named_services:use, docs:comment]}
              object.action.resolve_comment:   {grants: [named_services:use, docs:comment]}
              object.action.delete_comment:    {grants: [named_services:use, docs:comment]}
          delete:       {operation: object.delete,         grants: [named_services:use, docs:comment]}
```

These are caller grants under **Delegated by KDCube**; the separate connected
Google account holds `docs:read`/`docs:write`/`docs:comment`. The connect/grant
two-gate machinery is identical to Sheets above.

**Connect and grant.** Connect the Google account under **Delegated to
KDCube** (an account connected earlier for Gmail or Sheets returns
`claim_upgrade_required` when Docs access is first requested; approve it), then
select the required Docs tools and account claims under **Delegated by KDCube**.

Document refs are provider-neutral at the agent boundary:

```text
docs:<provider>:<account_id>:document:<document_id>
docs:<provider>:<account_id>:source:<file_id>
docs:<provider>:<account_id>:export:<format>:<document_id>
```

### What an agent can do with the Docs realm

The named-service schema gives an agent a document vocabulary, not a list of
Google endpoint names. These are complete user-facing scenarios supported by
the current `docs` provider:

| User request | What the agent can do |
| --- | --- |
| "Find `26_006`, copy it as `26_007`, update the invoice values, and send me the result." | Search by exact logical title, so `26_006` can match `26_006.docx`; recognize the result as an import source; copy and convert it to a native Google Doc; read its content and tab inventory; replace the named old values; read again to verify; export the result and return it as a file. |
| "Append this approval note to the tab whose title contains `July`." | Read the document structure, resolve the literal title fragment to one tab, translate it to Google's `tabId`, and append only there. |
| "Replace `Draft` with `Final` in the second tab." | Resolve the 1-based tab position from Google's current document structure and scope the replacement to that tab. A nested tab can instead be selected by its full root-to-tab hierarchy. |
| "Replace the old company name everywhere in this document." | Use `all_tabs=true` only because the user explicitly requested every tab. An omitted tab scope never silently becomes an all-tab edit. |
| "In the Tasks table, mark `Fix login` as Done and assign it to the reviewer." | Read the document's table list, pick the table under the `Tasks` heading, find the one row whose `Task` cell is `Fix login`, and write the `Status` and `Owner` cells of that row in one call. No provider index is computed by the agent. |
| "Reply `Approved` to my unresolved comment about payment terms." | Read bounded document-level comment pages, match literal text, connected-user authorship, and unresolved state, translate the one match to Google's `commentId`, then reply. The same selectors support reading, resolving, and deleting a thread. |
| "Create a report with a styled heading, a page break, this image, and give me a DOCX." | Create a native document, insert and style text, insert the page break and image, verify the document, export it, and return the portable file. |
| "Use my work Google account for this document." | Select the requested connected account when several accounts are eligible. Without a clear selection, return `account_required` with the available choices instead of guessing. |

The same realm works for a KDCube ReAct agent and for an external MCP client.
The ReAct agent can pull a document ref into its turn as a structured snapshot;
an external client can call the same named-service discovery, schema, search,
get, action, and export operations over MCP. Both paths keep the Google token on
the trusted service side.

The provider also states its present boundaries so the agent does not invent a
workflow:

- document discovery searches Drive titles; it does not search document-body
  meaning and KDCube does not build a second index over the user's Drive;
- title, title-fragment, position, and hierarchy selectors choose an existing
  tab for content operations; creating, renaming, moving, or deleting the tab
  itself is not part of the current Docs action set;
- stable Google Drive comments are document-level. The agent can select a
  thread naturally by literal text, quoted text, author, and resolved state,
  but it cannot claim that the thread belongs to a particular tab;
- tables are addressed by tab, table, row, and column; a cell holding a nested
  table, a cell merged into another, and (for a replacement) a cell holding an
  image or chip are refused rather than edited partially;
- several matching tabs, tables, rows, or comments produce bounded candidates
  and no write.
  The agent can use the user's wording to narrow the selector or ask one short
  disambiguating question.

Search returns at most 50 results. Operations are bounded: text reads and
replacements are capped at 200,000 characters, at most 50 replacements per
`replace_text`, table reads at 5 tables and 2,000 cells per call (50 rows per
table by default), comment bodies at 20,000 characters, at most 100 comments listed,
titles at 300 characters, and export/import at 10 MiB. A natural comment
selector may inspect up to five such comment pages; it reports an incomplete
bounded scan when more provider pages remain.

A non-blank search does not enumerate Drive pages looking for a title. It asks
Drive for exact provider titles and exact logical titles first, then fills the
remaining result page with title-prefix matches. A logical title removes the
known extension from a compatible import source, so `26_006` exactly matches
`26_006.docx`. Exact rows carry `exact_title_match: true`; each result also says
whether it is a native document or an import source that requires conversion.
The response carries `exact_match_count`, `match_mode`, and
`incomplete_search`. The Drive query includes files visible through My Drive
and Shared Drives. Search is title discovery, not semantic or document-body
search.

`copy` uses Drive's native copy for a native Google Doc. For a DOCX, ODT, or RTF
source, it reads that source and creates a new native Google Doc through Drive's
upload conversion; the original file is unchanged. The returned document ref
is immediately usable by `get`, replacement edits, comments, and export. Import
sources have their own `docs:...:source:...` refs, and their metadata tells the
agent to copy before editing. `create`, `copy`, and `import` are not exactly-once;
on an `outcome_unknown` transport failure, search for the intended target title
before retrying. Provider failures preserve Google's safe message plus
`provider_status`, `provider_code`, `provider_reason`, `stage`, and `retryable`, per the
[Provider Error And Observability Contract](../../../sdk/integrations/provider-error-contract-README.md).

### Read and edit document tabs

Drive title search returns document metadata; it does not open the document or
inspect its tabs. Read the selected native document before editing it. `get`
returns the extracted text from every tab, `tab_count`, and a `tabs` inventory
with each tab's stable `tab_id`, title, order, parent, nesting level, and body
end index.

Single-tab documents keep the short form: the caller can omit tab selection.
For a document with several tabs, the write scope is explicit:

- named-service insert, append, styling, page-break, and image operations accept
  one `tab_selector` by exact title, literal title fragment, 1-based position,
  or root-to-tab hierarchy;
- named-service replacement accepts one `tab_selector`, several
  `tab_selectors`, or `all_tabs=true` when replacing in every tab is
  intentional;
- exact `tab_id` and `tab_ids` remain available to callers that already hold
  the provider handles;
- the flexible batch editor requires one `tab_id`; `all_tabs=true` is available
  only for a batch made entirely of `replaceAllText` requests.

If a multi-tab mutation omits that scope, the provider returns
`docs_tab_selection_required` with the available tabs and sends no write to
Google. An agent can then choose a tab named by the user, or ask which tab to
edit when the request is ambiguous. This avoids relying on Google API defaults:
some omitted tab ids target the first tab, while an unscoped replacement can
span every tab.

### Read and write document tables

The extracted body text renders each table as one line per row, under a caption
naming its size and whether it has a header row, with cells separated by `|` and
a marker where a cell holds something other than text:

```text
[table · 5 rows × 4 columns · header row]
Task | Status | Owner | Comment
Fix login |  | owner-a | 
Review | Open | [person] | 
```

A literal `|` inside a cell is escaped. That text is for reading; addressing a
cell uses the table inventory below.

`get` on a native document lists every table under `tables`: its tab, 1-based
position in the tab, the nearest heading of any level above it, row and column
counts, the header row when the document marks one (`header_rows`, `header`),
otherwise the `first_row`, and a ready `selector`. It carries no cell text and no
provider index.

To read the cells of every table, pass `include: ["tables"]`. To read named
tables, pass `filters.tables` with one selector, or a list of up to five:

```json
{"table": {"after_heading": "Tasks"}, "rows": "1-50", "header": 1}
```

Each table returns up to 50 rows by default; `truncated` and `next_rows` say how
to continue. All tables in one call share a 2,000-cell budget; a table that no
longer fits returns `skipped: "cell_limit"`, and `tables_truncated` says the
document holds more than five tables. Every cell carries its own `row` and
`column`, so no reader counts rows itself, and names what it holds besides text -
a person chip reads as `objects: [{"kind": "person", "email": ...}]` where the
flat body text shows nothing. The complete JSON snapshot carries every table with all of its cells.

`object.action set_cells` writes text into cells of one row:

```json
{
  "tab_selector": {"title": "Main"},
  "table": {"after_heading": "Tasks"},
  "row": {"where": {"column": "Task", "equals": "Fix login"}},
  "cells": {"Status": "Done", "Owner": "reviewer"},
  "mode": "replace"
}
```

- **Table:** `position`, `after_heading` (exact heading text), or
  `header_contains` (a header cell fragment, or a first-row fragment without a
  header). Combined with another field, `position` counts that field's matches;
  a bare number is a position.
- **Row:** a physical 1-based number (header rows count), or
  `where: {column, equals | contains}` matching exactly one data row. Header
  rows never match a `where`.
- **Column:** a number always works; a name needs a header row. When the
  document does not mark one, pass `header: 1` to use the first row.
- **Mode:** `replace` (default; an empty text clears the cell), `append`, or
  `prepend`.
- **Person chip:** a list entry may carry `person` instead of `text`, for
  example `[{"column": "Owner", "person": "owner-a@example.com"}]`. A cell takes
  text or a person, not both.

Nothing is written when a selector matches zero or several targets
(`docs_table_not_found`, `docs_table_ambiguous`, `docs_table_row_not_found`,
`docs_table_row_ambiguous`, `docs_table_column_not_found`,
`docs_table_no_header`) or when a cell is refused: `docs_table_cell_merged`
names the cell it is merged into, `docs_table_nested` marks a nested table, and
`docs_table_cell_has_objects` lists images or chips a replacement would remove -
that decision belongs to the person whose document it is. A write that does
replace them reports them as `before_objects`, so a chip removed by mistake can
be written back.

The provider resolves the selectors on a fresh read and writes with that
revision required. If the document changes in between, it reads again and
resolves the selectors once more, so a `where` row survives a row inserted
above it. A caller that reasoned over an earlier read can pass its
`revision_id`; the write is then refused with `docs_revision_changed` when the
document has changed since.

On the typed productivity door the same reads use `all_tables` or `table_reads`
on `productivity_docs_get`, and writes use `productivity_docs_set_cells`.
`productivity_docs_get_structure` additionally lists each table's cells with
`start_index`/`end_index` and `content_start`/`content_end` (the editable text
range) for callers composing `productivity_docs_batch_edit` requests. That door
also builds table structure: `insertTable`, `insertTableRow`,
`insertTableColumn`, `deleteTableRow`, `deleteTableColumn`, `mergeTableCells`,
`unmergeTableCells` and `pinTableHeaderRows`. Pin a new table's header row and
every later read names its columns without `header`.

### Add a row, and take a document out of the way

`object.action add_row` adds one row to a table named the way `set_cells` names
it, and fills it in the same call when `cells` is given. The row goes to the end
unless `after_row` names the row to put it below, by number or by
`where: {column, equals | contains}`. Appending a dated record is one call, with
no index arithmetic and the same refusals.

`object.action trash` moves the document to the Drive trash, where it stays
recoverable; `restore` brings it back. Both act on the document itself rather
than its contents, so they carry their own claim, `docs:delete` - a card granted
for editing tables does not decide whether a document stays in Drive. The claim
maps to Drive's `drive.file` scope, which reaches documents this deployment
created; a document made by hand elsewhere is outside it. On the typed door the
same verbs are `productivity_docs_add_row`, `productivity_docs_trash` and
`productivity_docs_restore`.

### Look before an index-based write

`insert_text`, `apply_text_style` and `replace_text` take `preview: true`.
Nothing is written and the answer says what is already there:

- an insert reports the paragraph or table cell the index falls in, with the
  text on either side - the guess that once turned a header cell's `Comment`
  into `Commen…t` shows up here as text landing mid-word;
- a style reports the text its range covers;
- a replacement counts each phrase's matches and shows where they sit, which
  `replaceAllText` reports only after it has changed them.

Table cells need no preview: `set_cells` already answers with `before` and
`after` per cell, and refuses rather than guess.

### Manage the document's tabs

`object.action add_tab` adds a tab: without a title Google names it, `index`
places it among its siblings (zero-based) and `parent_tab_id` nests it under an
existing tab. `addDocumentTab` returns no id of its own, so the operation reads
the document again and reports the tab the document gained, with the full tab
list. `update_tab` renames a tab or moves it, sending only the fields the call
changes. `delete_tab` removes one tab and everything in it: Google deletes its
child tabs too, and the result lists them as `deleted_child_tab_ids`. A document
keeps at least one tab, so deleting the only tab is refused with
`docs_last_tab`. All three ride `docs:write`, and on the typed door they are
`productivity_docs_add_tab`, `productivity_docs_update_tab` and
`productivity_docs_delete_tab`.

### Address document comments naturally

`object.action update_comment` rewrites a comment's text, or one reply's with `reply_id`; Google allows it only for the comment's own author.

The stable Drive comments path manages document-level threads. Named-service
actions that read, reply to, resolve, or delete one comment accept either an
exact `comment_id` or a `comment_selector`. A selector can combine a literal
comment or quoted-text fragment, an author (`author: me` selects the connected
user), resolved state, and 1-based position. The adapter pages through a bounded
provider result, translates one unambiguous match to the provider id, performs
the action, and returns the resolved candidate in `selector_resolution`.

Several matches return `docs_comment_selector_ambiguous` with bounded
candidates. A bounded scan with more provider pages returns
`docs_comment_selector_incomplete`. Stable Drive comments do not carry native
Google Docs tab placement, so a request that combines a comment action with a
tab selector returns `tab_anchored_comments_unavailable` rather than creating a
broader document comment silently.

For a ReAct agent, declare `docs` as both a named-service namespace and an event
source. The namespace tools discover document and import-source refs. A native
document resolves through `react.pull` into a complete JSON snapshot with tab
metadata, paragraph text, every table with its cells, and open comments. An import source
resolves to file metadata and conversion guidance; after `object.action.copy`,
the returned native document ref can be pulled and inspected before bounded
replacement edits:

```yaml
event_sources:
  - kind: named_service
    namespace: docs
    enabled: true
    discovery: {mode: service_discovery}
    policies:
      block_production: {mode: provider, operation: block.produce}
      pull: {mode: provider, operation: object.get}
```

A reliable clone-and-edit chain is: search the exact source title, search the
intended target title to avoid duplicates, copy the source ref, get or pull the
new ref, replace the explicit old values on that new ref, get it again to verify,
then call `object.action export` when the user asks for a file. Export returns a
portable `docs:...:export:...` ref. In KDCube chat, the tool gate emits that ref
as a file card and keeps the signed URL out of model context. A materializing
client can stream the same export ref; a turnless MCP client receives the
short-lived download URL.

The SDK mechanics (the async REST proxy over the Docs and Drive APIs, and the
shared credential resolver) are in
[Google SDK Integration](../../../sdk/integrations/google/google-README.md).

## Declare a new action in two places

A named-service action is declared twice, and both declarations are needed
before a hosted agent can call it:

1. **The grant catalogue** - a row under
   `connections.delegated_credentials.oauth.resources[].named_services.<namespace>.tools`
   with the claims the operation needs. This is what makes the operation
   delegable at all.
2. **The calling agent's roster** - the operation's name under
   `surfaces.as_consumer.agents.<agent>.tools[].namespaces.<namespace>.allowed`
   in that agent's bundle. This is what the Control Card is derived from.

Only the second changes the descriptor revision the Control Card is keyed on.
Adding a catalogue row alone leaves the ceiling at its previous generation: the
operation stays outside it, a consent grant for it is erased by the next
capability sync, and no action in Connection Hub can repair that. After both
declarations, reload the agent's bundle and let one agent turn run; the Control
Card takes a new revision and the operation becomes callable.

The typed door is separate: a tool there needs its own row under the
productivity resource and, for a caller-held card, that card's grant.

## Verify

Refresh the runtime after descriptor changes, then walk both services on
disposable data:

**Gmail.** Connect Gmail first, then from an agent that has the Gmail tools:

- with `gmail:read`: `search_gmail` finds messages, `read_gmail_message` reads a
  body and lists attachment ids, `download_gmail_attachments` materializes
  attachments as KDCube files;
- with `gmail:send`: `send_gmail` sends, including KDCube-file attachments;
- with both: `forward_gmail_message` forwards with original attachments;
- if the account lacks a claim, the tool returns a managed connected-account
  consent error the chat UI can surface as a connect/upgrade action.

**Sheets.** On a disposable spreadsheet:

1. `search`, `describe`, and `read` work with a read-only grant.
2. A write tool is denied until both the selected-tool grant and the
   `sheets:write` connected-account claim exist.
3. Update values, append a row, format a range, add/update/delete a test tab,
   and create a test spreadsheet.
4. With two Google accounts, an ambiguous call returns `account_required`; the
   retry succeeds with one returned `account_id`.
5. Revoke the caller's tool grant; the next call stops at gate 1. Restore it,
   revoke the Google claim; the next call stops at gate 2.
6. Inspect tool output, timeline, logs, and model input: no Google bearer or
   refresh token appears.
7. Repeat search/read and one disposable update through `namespace=sheets` on the
   named-services endpoint, and fetch `ret.object.snapshot.download.url` to verify
   the complete JSON snapshot.

**Docs.** On a disposable document:

1. Create an old document whose title contains an underscore and whose body has
   a table. Verify an exact-title search returns it first with
   `exact_title_match: true`, without paginating a blank list.
2. `search`, `get`, and `export` work with a read-only grant; `get` includes the
   table-cell text and all tab titles.
3. A write tool (`insert_text`, `copy`, `create`, ...) is denied until both the
   selected-tool grant and the `docs:write` connected-account claim exist.
4. Copy the old document to a new title, update table values with `set_cells`,
   and re-read the new document to verify the source is unchanged.
5. Create a document, append/insert/replace text, apply a style, insert a page
   break, embed an image, and import a source document.
6. A comment tool (`create_comment`, `resolve_comment`, ...) is denied until the
   `docs:comment` claim exists - `docs:write` alone does not authorize it.
7. Revoke the caller's tool grant; the next call stops at gate 1. Restore it,
   revoke the Google claim; the next call stops at gate 2.
8. Inspect tool output, timeline, logs, and model input: no Google bearer or
   refresh token appears.
9. Repeat search/copy/get through `namespace=docs`, pull the returned document
   ref from ReAct, and fetch the signed snapshot URL from a turnless client to
   verify the complete JSON snapshot.
10. Call `object.action export` on the copied ref. Verify KDCube chat receives a
    downloadable file card, the model-visible result contains a delivery note
    rather than a signed URL or base64, and a streaming get of the export ref
    yields the complete file bytes.
11. Create a document with two tabs and read it. Verify `tab_count` and the tab
    inventory are present. Append by `tab_selector.title`, replace another tab
    by `tab_selector.position`, and select a nested tab by full hierarchy. Create
    `Internal Notes` and `Invoice Notes`, select by the shared `Notes` title
    fragment, and verify the adapter returns both candidates instead of writing.
    Then explicitly replace across all tabs and verify both scopes.
12. Create two document-level comments with overlapping text. Reply using a
    selector that adds author or resolved state, and verify
    `selector_resolution` names the chosen thread. Retry with only the shared
    text and verify the ambiguous response performs no mutation. Add a tab
    selector to a comment request and verify
    `tab_anchored_comments_unavailable`.
13. Under a heading, add a table with a marked header row (`Task | Status |
    Owner`), a second table without a header, and a merged cell. Verify `get`
    lists both tables with selectors, `filters.tables` returns cell text, and
    `set_cells` writes one row found by `where` without touching the header.
    Verify two rows with the same value, a merged-away cell, and a column name
    on the headerless table each return their refusal with no write, and that
    `header: 1` makes the name resolve.

## Add another Google service, the same way

Any other Google API connects the same way: enable the API in Google Cloud, and
add a claim under `providers.google.claims` mapping to the real Google scopes,
then wire its tools or named service as Sheets does above. Scopes are managed as
connector claims in `bundles.yaml` (Connection Hub) — **not** on the console
consent screen: while the OAuth app is in *Testing*, the descriptor drives the
authorization request and a test user grants the scopes at connect time. The
console is only for enabling the API, the OAuth client, redirect URIs, and test
users. The connect, grant, and two-gate machinery are unchanged.

| Service | Read claim -> scope | Read-write claim -> scope |
| --- | --- | --- |
| Sheets | `sheets:read` -> `spreadsheets.readonly` | `sheets:write` -> `spreadsheets` |
| Drive | `drive:read` -> `drive.readonly` | `drive:write` -> `drive` (or `drive.file`, per file) |
| Calendar | `calendar:read` -> `calendar.readonly` | `calendar:write` -> `calendar` |
| Docs | `docs:read` -> `documents.readonly` | `docs:write` -> `documents` |

The read-write-supersedes-read-only reconciliation is generic: connecting a
read + write pair for ANY of these sends only the read-write scope (Google grants
read and write), because the adapter drops `<X>.readonly` when `<X>` is present.
It keys on the exact `<X>` / `<X>.readonly` pair, so scopes that are not a clean
read-only/read-write pair are left alone.
