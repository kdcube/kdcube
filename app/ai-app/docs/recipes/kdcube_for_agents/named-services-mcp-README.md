---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/named-services-mcp-README.md
title: "Make A Named Service Agent-Friendly (MCP)"
summary: "What a named-service namespace should implement so a generic external agent (Claude over MCP) can discover, search, read files, and act through object refs alone — using conv as the worked example and mail/slack as integration namespaces."
status: active
tags: ["recipes", "kdcube-for-agents", "named-services", "mcp", "conv", "mail", "slack", "search", "files", "schema", "agent"]
updated_at: 2026-07-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
---
# Make A Named Service Agent-Friendly (MCP)

A named service becomes an MCP surface: a generic external agent (for example
Claude) connects to it, discovers the namespaces on it, and works purely through
**object refs** such as `conv:conversation:<id>`, `conv:turn:<id>`, and
`conv:fi:<path>`. The agent carries no KDCube session, no ambient conversation,
and no prior knowledge of your realm.

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
integration namespaces such as `slack`.

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

The agent adds the MCP URL as a connector and completes KDCube consent:

```text
https://<runtime>/api/integrations/bundles/<tenant>/<project>/
  kdcube-services@1-0/public/mcp/named_services
```

Consent grants concrete tools/grants/identity-scope; the surface enforces them per
call. The full journey (probe → consent → delegated credential → enforcement)
lives in the connections recipe linked above. The rest of this recipe assumes the
agent is connected and asks: what should each operation return so the agent can
actually use it?

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
encoding = none    metadata only (too large, or no link configured).
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

A named-service provider receives **identity**, and lets the underlying realm
authorize — it makes no platform-role decisions of its own.

```text
Default scope is the caller's own data (mode=self).
Selected-user (mode=user, user_id=...) is an admin path the managed boundary
  grants via conversations:read:any_user.
The provider maps the request onto the realm's own scope; the realm decides.
```

Grant hints in the schema (`conversations:read`, `conversations:read:any_user`) are
advisory for the consent boundary. Enforcement is the boundary's, not the
provider's.

## Batch Get

`object.get` accepts several refs in one call (a framework-level fan-out shared by
all providers). After a search returns a handful of turn/file refs, the agent reads
them in one round trip instead of N.

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

For integration namespaces, there are two authorization layers:

```text
External MCP delegated grants:
  slack:read / slack:write
  authorize the agent to use the KDCube Slack namespace.

Connected-account provider claims:
  slack:search / slack:history / slack:files:read / slack:post / ...
  authorize KDCube to call Slack for the current platform user.
```

If the agent has the MCP grant but the user has not connected Slack, or the
connected account lacks the provider claim, the provider returns a Connection
Hub consent payload instead of guessing or retrying.

## Agent-Friendly Checklist

```text
[ ] schema teaches the namespace: object kinds, self-describing filters, scopes,
    grant hints, and how to read files.
[ ] capabilities reflect what is actually wired (no operation that 501s).
[ ] search hits are lean (ref/title/body/score), titled from real text, with
    text-bearing snippets.
[ ] every emitted ref is self-contained (resolvable with no ambient session).
[ ] text inlines; binaries return a short-lived download url; only small binaries
    base64; large-with-no-link return metadata.
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
```

## Related Docs

- [Named-Service App](../components/named-service-README.md)
- [Delegate A KDCube Service To An External Client](../connections/delegate-kdcube-service-to-external-client-README.md)
- [Slack Integration](../connections/integrations/slack-README.md)
- [Protect A Bundle MCP With Managed Credentials](../connections/protect-bundle-mcp-with-managed-credentials-README.md)
- [Namespace Service Providers](../../sdk/namespace-services/providers-README.md)
