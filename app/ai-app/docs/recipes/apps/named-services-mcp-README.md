---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/named-services-mcp-README.md
title: "Make A Named Service Agent-Friendly (MCP)"
summary: "What a named-service namespace should implement so a generic external agent (Claude over MCP) can discover, search, read files, and act through object refs alone — using conv as the worked example and mail, Slack, and sheets as integration namespaces."
status: active
tags: ["recipes", "kdcube-for-agents", "named-services", "mcp", "conv", "mail", "slack", "sheets", "search", "files", "schema", "agent"]
updated_at: 2026-08-17
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/consume-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
---
# Make A Named Service Agent-Friendly (MCP)

A named service becomes an MCP surface: a generic external agent (for example
Claude) connects to it, discovers the namespaces on it, and works purely through
**object refs** such as `conv:conversation:<id>`, `conv:turn:<id>`, and
`conv:fi:<path>`. The agent carries no KDCube session, no ambient conversation,
and no prior knowledge of your realm.

The full configuration reference - every layer, the provider connected-accounts
contract, both consent gates, and all three scenarios - is
[Authenticated MCP: The Full Configuration Chain](../../sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md).

Agent-friendliness is the set of choices that let that agent succeed on the first
try: honest discovery, lean results, self-contained refs, and file bytes that stay
out of the model's context. This recipe walks each story using the `conv`
namespace (conversation memory, in the `kdcube-services@1-0` app) as the worked
example.

Two neighbours own the parts this recipe points at rather than restates:

- Writing the provider itself (operations, events, object.action, render) →
  [Named-Service App](../components/named-service-README.md).
- Connecting an external agent through delegated consent →
  [Delegate A KDCube Service To An External Client](../connections/delegate-kdcube-service-to-external-client-README.md).

## The Shape An Agent Sees

One MCP surface exposes generic, namespace-agnostic tools. The agent picks the
namespace and operation; the surface routes to the matching provider.

The reference `kdcube-services@1-0` surface includes platform namespaces such as
`conv`, application-context namespaces such as `mail`, and connected-account
integration namespaces such as `slack` and `sheets`.

```text
MCP surface: kdcube-services@1-0 / public/mcp/named_services
  named_services_list          discover namespaces
  named_services_about         intro + search scopes for one namespace
  named_services_capabilities  what this namespace supports right now
  named_services_schema        object kinds, filters, scopes, grant hints
  named_services_search        find objects -> refs
  named_services_get           read one object (or many refs) by ref
  named_services_call          generic operation (e.g. object.action)
  # write realms additionally: upsert, action, host_file, delete
```

The intended agent workflow — encode it so the surface teaches it:

```text
1. list                      unless the user named the namespace + operation
2. capabilities + schema     for an unfamiliar namespace
3. search                    to find the object ref when it is not known
4. get                       read by ref
5. call/action/upsert/...    only when the user asks and capability allows
```

## How To Connect

The surface is one MCP URL:

```text
https://<runtime>/api/integrations/bundles/<tenant>/<project>/
  kdcube-services@1-0/public/mcp/named_services
```

Two kinds of agent reach it, with the same enforcement behind both:

**An external agent** (Claude over MCP) adds the URL as a connector and
completes KDCube consent in the browser — the OAuth journey (probe → consent →
delegated credential) lives in
[Delegate A KDCube Service To An External Client](../connections/delegate-kdcube-service-to-external-client-README.md).

**A hosted agent** (an agent inside a KDCube app) declares a delegated
connection in its app's tool inventory — no OAuth dance; the user grants THIS
agent in Connection Hub (or from the chat consent demand, one click):

```yaml
# surfaces.as_consumer.agents.<agent_id>.tools
- name: slack
  kind: mcp
  server_id: slack
  alias: slack
  url: https://<runtime>/api/integrations/bundles/<T>/<P>/kdcube-services@1-0/public/mcp/named_services
  resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  transport: streamable_http
  delegated: true
  scopes: [named_services:use]   # Door only. Namespace and provider claims are catalog-derived per call.
```

`resource` must byte-match the deployment's configured delegated-resource id
(commonly a wildcard pattern) — the per-agent grant is keyed under it. The
exact connection semantics and the two-consent chain (agent grant + the user's
own connected-account consent, checked per call):
[Connect An MCP Service To A KDCube Agent](consume-mcp-service-README.md),
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

For the hosted named-services bridge, the connection-level `scopes` list is an
admission contract for the MCP door. Keep it to `named_services:use`. Do not put
conversation grants such as `conversations:read` or provider claims such as
`slack:post` in that list. The named-service bridge raises those requirements
from the Connection Hub resource catalog only when the agent calls an operation
that needs them.

Either way, consent grants concrete tools/grants/identity-scope; the surface
enforces them per call. The rest of this recipe assumes the agent is connected
and asks: what should each operation return so the agent can actually use it?

## The Schema Story

`object.schema` is the contract the agent reads before it acts. Make it teach the
namespace, not merely list types.

For `conv`, the schema advertises the object kinds, the search filters, the read
scopes, and the file-retrieval story in one place:

```text
object_kinds:
  conversation       conv:conversation:<id>   (summary_fields, full_fields, turn_fields)
  turn               conv:turn:<id>           (search hit)
  conversation.file  conv:fi:<path>           (uploaded / produced / snapshot / pulled)

scope:
  mode: {enum: [self, user], default: self}
  user_id: selected platform user id (mode=user; admin, :any_user grant)

search:  { purpose, filters, behavior, returns, recovery }
files:   { operation: object.get, ref: conv:fi:<path>, purpose, returns }

grant_hints: { object.search: [conversations:read], selected_user: [conversations:read:any_user] }
```

Nuances that make the schema agent-friendly:

```text
Advertise only actionable fields.
  conv dropped ordinal/order/rank_score from the agent-facing schema — internal
  ranking knobs an agent cannot reason about seed wrong calls.

Every filter is self-describing.
  type, description, enum, default, and an example — so the agent fills it without
  guessing. See the search filters below.

Default to the useful choice.
  conv search defaults scope=user (recall across the user's conversations), which
  is what external recall almost always wants.

Only expose a knob that is meaningful.
  conv has no min_score filter: its hybrid score is not normalized 0..1, so a
  threshold would be a trap. Expose a knob only when the agent can set it sanely.

Capabilities reflect what is wired right now.
  search/list/get report true only when the backing service is configured, so the
  agent never calls an operation that will 501.
```

## The Search Story

`object.search` is how the agent turns intent into refs. Return lean, honest hits.

Filters `conv` advertises:

```text
targets   assistant | user | attachment | summary | notes   (default: assistant+user+attachment+summary)
scope     user (default, recall across conversations) | conversation
from/to   ISO window                                          (date-window recall)
days      lookback window
include_recovery_sessions  default false
bundle_id permitted application to search                     (hosted default: own app; external: explicit Card target)
```

Behaviors from one operation (empty query is valid):

```text
topic            query set        -> hybrid semantic+lexical+recency
topic_in_window  query + from/to
date_window      from/to, no query -> turns in that window
overview         no query, targets=[summary] -> working summaries
```

A hit shape the agent can act on:

```text
{ ref: "conv:turn:<id>",
  title: "<first meaningful snippet text>",
  body: { conversation_id, turn_id, turn_index_path,
          snippets: [ {role, path, text, ts} ] },
  score }
```

Nuances:

```text
Hits are lean.
  A search item carries ref/title/body/score only. The single-object envelope
  (schema/mime/namespace/identity/label/summary) belongs on object.get, not on
  every hit — it drowns the agent in duplicate metadata.

Titles are meaningful.
  Derive the title from the first snippet's text. Fall back to the id only when
  there is no text — never a blank title.

Snippets carry text AND a resolvable handle.
  An empty snippet is dead weight. Each snippet keeps its text plus a path the
  agent can pass straight back to object.get.

Refs are self-contained.
  The agent has no ambient conversation, so cross-conversation file refs include
  both the ReAct conversation owner namespace and the conversation body segment:
  conv:fi:conv_<conversation_id>.turn_<id>.<...>. A ref that only resolves with
  server-side ambient state is not agent-friendly.
```

## The Files Story

This is the story most easily gotten wrong. An MCP tool result is JSON — bytes can
only ride inline as **base64**, and base64 lands in the model's context. A 143 KB
image becomes ~190 KB of base64 that the agent must hold, decode, and walk. That
is the opposite of friendly.

`conv` delivers a `conv:fi:<path>` file by type:

```text
object.get conv:fi:conv_<id>.turn_<id>.files/chart.png
  ->
  { ref, filename, mime, size, encoding, ... }

encoding = text    content is the decoded text (inline). Small, context-safe.
encoding = url     fetch bytes from `url` over HTTP — a short-lived signed link.
                   The default for binaries: bytes never enter the model's context.
encoding = base64  content is base64. Only for small binaries (<= 32 KB).
encoding = none    delivery is unavailable in this deployment; capabilities and
                   schema must report that limitation.
```

The download link is the key move for binaries:

```text
{ "encoding": "url",
  "url": "https://<runtime>/api/integrations/bundles/<t>/<p>/
            kdcube-services@1-0/public/conv_file_download?object_ref=...&download_token=...",
  "expires_at": 1751420000,
  "filename": "chart.png", "mime": "image/png", "size": 143145 }
```

How the link stays safe and self-serving:

```text
Signed + bound.
  The token is a stateless HMAC stamp bound to the exact file + requester
  (fi_ref + user_id + conversation_id + tenant + project + short expiry). The
  download route trusts the signature, not the (unauthenticated) request.

Session-less.
  A dedicated public route re-materializes the bytes under the token's identity
  and streams them. The agent fetches with a plain GET — no KDCube session.

Secret from the descriptor.
  The signing secret is one descriptor key (conversations.file_download_secret in
  bundles.secrets.yaml). No environment variables, no fallbacks: absent secret ->
  no link, and the binary falls back to inline/metadata.

User-scoped materialization.
  The bytes are pulled through the requester's own identity, so an agent only ever
  reaches files from conversations it may see.
```

In-app readers are unaffected: a native `react.pull(conv:fi:...)` still streams raw
bytes. The URL encoding is specifically for the agent-over-JSON boundary.

A hosted agent running INSIDE a chat turn never relays this link itself: its
tool gate delivers the file to the user as a chat file card (the object ref,
resolved at click time under the user's own session) and the model-visible
result carries a delivery note in place of the URL. The `url` encoding remains
the contract for turn-less clients — machinery that fetches, where a model
would have to re-type a signed token by hand. Caller-side behavior:
[Connect An MCP Service To A KDCube Agent](consume-mcp-service-README.md).

Connected-provider namespaces use the same response shape with a different
byte source. A signed Mail, Slack, or Sheets URL is normally a live KDCube
delivery proxy:

```text
GET signed KDCube URL
  -> verify exact ref + bound user/tenant/project + expiry
  -> resolve current connected-account credential server-side
  -> enforce current provider consent
  -> fetch current provider data and stream it
```

It is not a previously hosted immutable artifact. The provider credential
never appears in the URL or response. KDCube checks the external client's
delegated grant before minting the URL; the URL is then a short-lived bearer
capability for that exact ref and bound identity. Each GET re-checks the
connected provider account and claim, so provider-consent revocation applies
immediately. Revoking only the outer delegated grant blocks a fresh URL but
does not revoke one already issued before its expiry. A second fetch may
observe newer provider state. A harness client that needs a stable copy uses
`react.pull`; KDCube streams the complete representation into the turn
workspace and returns a `conv:fi:` ref.

The complete-data invariant applies to structured objects too. Normal MCP
results may be compact, but the authorized remainder must be reachable through
`next_cursor`, explicit provider ranges/parts, or signed complete delivery.
The external client decides what to save and how much to place in model
context. External responses must not prescribe ReAct-only tools.

## The Read Story

`object.get conv:conversation:<id>` is not a raw dump. It fetches the rich per-turn
record and distills a compact, time-ordered timeline the agent can scan:

```text
{ conversation_id, user_id, title, turn_count,
  turns: [ { turn_id, events: [ ... ] } ] }

event types (interleaved by time, heavy bodies dropped):
  user.message | user.attachment | assistant.thinking |
  assistant.message | assistant.file | artifacts | sources
```

Nuances:

```text
Produced files and attachments surface as conv:fi: refs.
  assistant.file and user.attachment events carry a conversation-scoped
  conv:fi:conv_<id>.turn_<id>.<...> ref the agent can hand to object.get.

turn_id once per turn.
  Events are grouped under their turn; the id is not repeated on every event.

Drop the weight.
  Source bodies collapse to sid/title/url; artifact bodies to name/title/format.
  The agent gets the map, then pulls only what it needs.
```

## Identity And Scope

A named-service provider receives **identity** plus request-local authority
admitted for this invocation. The operation boundary and the realm each enforce
the dimensions they can observe.

```text
Default scope is the caller's own data (mode=self).
Selected-user (mode=user, user_id=...) requires the effective Card claim
  conversations:read:any_user. The provider checks that claim because user_id
  is inside the decoded provider request, then maps it onto the realm's scope.
```

Grant hints in the schema (`conversations:read`, `conversations:read:any_user`)
tell the consent boundary what a call can demand. The boundary admits the
operation and binds effective Card claims; the provider enforces the
payload-dependent selected-user claim.

## Batch Get

`object.get` accepts several refs in one call (a framework-level fan-out shared by
all providers). After a search returns a handful of turn/file refs, the agent reads
them in one round trip instead of N.

## Query shape for `conv`

The `conv` search runs the same engine as the in-app `react.memsearch`, and
the same rules apply to the query an external agent sends. The lexical arm ANDs
every unquoted word, a `"quoted phrase"` must occur verbatim, `OR` separates
alternatives, `-word` excludes, the fuzzy arm averages over the query tokens,
and a same-day turn is lifted about 2x over a month-old one at equal match. So
`query` is a compact bag of content words as they would appear in the stored
text (names, file names, identifiers, the user's wording), one topic per call;
time words go to `from`/`to`, never into `query`; `"last time we worked on the
excel file"` finds nothing lexically, `excel forecast` or
`"Forecast-Q2-2026.xlsx"` finds the turn. The full guide is returned to the
client in two places it already reads: `provider.about` and the `object.search`
schema view (`schema.search.query_guide`), both rendered from
`CONVERSATION_QUERY_GUIDE` in `sdk/solutions/conversation/instructions.py`, the
same text the native tool spec carries.

## Scenarios

Recall a discussion and show the chart the user made:

```text
1. search  namespace=conv, query="the revenue chart", scope=user
     -> conv:turn:... hits with snippets + conversation_id
2. get     conv:conversation:<id>
     -> timeline; an assistant.file event carries
        conv:fi:conv_<id>.turn_<id>.files/chart.png
3. get     conv:fi:conv_<id>.turn_<id>.files/chart.png
     -> { encoding: "url", url, expires_at, mime: image/png }
4. agent GETs the url over HTTP -> raw PNG, never in context
```

Read a spreadsheet the user uploaded last week:

```text
1. search  namespace=conv, targets=[attachment], from/to = last week
     -> hit snippet path conv:fi:conv_<id>.turn_<id>.user.attachments/report.xlsx
2. get     that conv:fi: ref
     -> binary -> { encoding: "url", ... } ; agent fetches the bytes to analyze
   (a .csv or .md attachment would come back encoding=text, inline)
```

Overview without a query (temporal catalog):

```text
1. search  namespace=conv, no query, from/to set (or targets=[summary])
     -> turns / working summaries in that window, deterministic
2. get     the conversation ref that looks relevant
```

Work with a connected Slack workspace:

```text
1. list    namespace=slack
     -> slack:<account_id> account refs visible to the current KDCube user
2. call    operation=object.list, namespace=slack,
           params={filters: {kind: "channels", account_id: "<account_id>"}}
     -> slack:<account_id>:channel:<channel_id> refs
3. search  namespace=slack, query="customer onboarding"
     -> Slack message/file hits visible to a connected Slack account
4. get     slack:<account_id>:channel:<channel_id>
     -> recent channel history with file refs
5. call    operation=object.action, action=post_message,
           object_ref=slack:<account_id>:channel:<channel_id>,
           payload={text: "..."}
     -> posted message metadata
```

Find and update a connected spreadsheet:

```text
1. search  namespace=sheets, query="quarterly plan"
     -> sheets:google:<account_id>:spreadsheet:<spreadsheet_id>
2. get     that spreadsheet ref
     -> title, tabs, named ranges, and stable tab refs
3. get     the spreadsheet ref with
           filters_json='{"ranges":["Plan!A1:D20"]}'
     -> bounded cell values
4. action  action=append_rows, object_ref=<spreadsheet ref>,
           payload_json='{"range":"Plan!A:D","rows":[[...]]}'
     -> affected range and row counts
```

The namespace is `sheets`, not `google_sheets`: object kinds and refs stay
provider-neutral while the ref records which connected provider/account owns
the object. The [Google Services recipe](../connections/integrations/google-service-README.md)
contains the complete Sheets descriptor and consent setup.

For integration namespaces, authorization has two gates that share one claim
vocabulary:

```text
Door admission (delegated grant):
  named_services:use
  admits the agent to the KDCube named-services boundary.

Per-operation claim — the REAL Slack claim, at BOTH gates:
  slack:search / slack:history / slack:files:read / slack:post / ...
  granted to the agent AND approved on the connected account.
  One vocabulary, checked twice (Slack is a single provider).
```

## The Consent-Error Story

Two consents chain in front of an integration namespace, and each denies with a
structured error naming its own fix.

The FIRST boundary is the caller's own per-agent grant, and it stays live past
the first approval: with no grant on this surface the connection refuses to bind
(the consent-gated stub raises the demand), and once the agent holds SOME
grants, the door checks each operation and denies precisely:

```text
error = delegated_consent_required
  namespace / tool / operation
  required_grants / missing_grants / available_grants   exact, per operation
  code = connections.consent_needed                     (kdcube-agent:* callers)
  consent          the full grant block: kind delegated_agent_grant,
                   agent_client_id, resource, claims (= missing_grants), and
                   the one-click delegated_agent_grant_create action
  next_step        Connection Hub grant extension for agent callers;
                   reconnect/incremental consent for external OAuth clients
```

A hosted in-turn agent's tool wrap raises that block as the standard scoped
chat demand. The user approves exactly the missing claims, and the approval
merges into the agent's existing grant record. For the hosted named-services
bridge, connect time should ask only for the MCP door grant
(`named_services:use`). Namespace grants and provider-backed claims are not
connection scopes; the bridge derives them from the catalog and names the
specific missing grant for the operation that was attempted.

The SECOND boundary: if the agent has the MCP grant but the user-to-provider
side cannot satisfy the call, integration namespaces answer with one structured
consent error instead of guessing, retrying, or a vague server error:

```text
error.code = needs_connected_account_consent            (status 403)
error.details:
  reason               connect_required | claim_upgrade_required |
                       reconnect_required | account_required |
                       agent_grant_required
  retry_hint           true -> the same call succeeds after the user acts
                       (for account_required: when resent with account_id)
  provider_id / connector_app_id / claims / account_id
  candidates           labeled account summaries
                       [{account_id, label, email, workspace, status, claims}]
  connection_hub_url   open this to connect / approve / reconnect
  consent              the full Connection Hub consent block (action_label, ...)
```

How the agent acts on `reason`:

```text
connect_required        no eligible account — the user connects the provider at
                        connection_hub_url
claim_upgrade_required  an account exists — the user approves the listed claims
reconnect_required      the stored credential no longer works — the user
                        reconnects that account
account_required        several accounts match — resend the SAME call with
                        account_id set to one of candidates[].account_id
agent_grant_required    the account is connected and holds the claim, but THIS
                        caller has no per-account binding for it
                        (default-closed) — connection_hub_url opens the
                        CALLER'S grant card (Delegated by KDCube), where the
                        user ticks the claim for the account of their choice;
                        never a reconnect, never the provider-connect tab
```

Account selection is explicit across integration namespaces. `mail` and
`slack` use `object.list` to return connected accounts with labels, approved
claims, and credential status. `sheets.object.list` instead lists recently
modified spreadsheets. If several Google accounts are eligible, a Sheets
list/search returns `account_required` with labeled candidates; retry the same
call with one `account_id`. Every returned Sheets ref then embeds that account.
No action silently chooses between accounts.

## Agent-Friendly Checklist

```text
[ ] schema teaches the namespace: object kinds, self-describing filters, scopes,
    grant hints, and how to read files.
[ ] capabilities reflect what is actually wired (no operation that 501s).
[ ] search hits are lean (ref/title/body/score), titled from real text, with
    text-bearing snippets.
[ ] every emitted ref is self-contained (resolvable with no ambient session).
[ ] text inlines; binaries return a short-lived download URL; only small
    binaries use base64; large structured data has cursor/range or signed
    complete delivery.
[ ] the provider takes identity and defers authorization to the realm.
[ ] refs round-trip: a ref from search/get is valid input to the next get.
```

## Pitfalls

```text
- Returning binaries as base64 in the tool result: it blows the model's context.
  Hand back a short-lived download url instead.
- Emitting refs that need server-side ambient state to resolve. Scope them.
- Blank or id-only titles, and empty snippets: the agent cannot choose a hit.
- Advertising internal ranking/pagination knobs the agent cannot set sanely.
- Putting the full object envelope on every search hit. Keep hits lean; save the
  envelope for object.get.
- Sourcing the download-link secret from anywhere but the descriptor.
- Returning a compact result with no continuation, complete delivery, or
  materialization path. Context safety must not make authorized data
  unreachable.
```

## Related Docs

- [Named-Service App](../components/named-service-README.md)
- [Delegate A KDCube Service To An External Client](../connections/delegate-kdcube-service-to-external-client-README.md)
- [Slack Integration](../connections/integrations/slack-README.md)
- [Google Services (Gmail, Sheets)](../connections/integrations/google-service-README.md)
- [Protect A Bundle MCP With Managed Credentials](../connections/protect-bundle-mcp-with-managed-credentials-README.md)
- [Namespace Service Providers](../../sdk/namespace-services/providers-README.md)
