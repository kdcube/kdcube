# lg-react: Connecting Named Services — Two Config Shapes

lg-react reaches KDCube named services through the delegated MCP door served by
`kdcube-services@1-0` (`/public/mcp/named_services`). The door exposes generic
operation tools (`named_services_list`, `_capabilities`, `_schema`, `_search`,
`_get`, write/action/host-file); the delegated grant's scopes decide which
namespaces those tools may touch, and `named_services_list` is the runtime
source of truth for what a connection actually serves.

Two configuration shapes exist for the same door. Both are valid; they differ
in governance granularity, consent UX, and what the system prompt teaches.
Template blocks: `config/bundles.template.yaml`, MCP examples 2 and 3.

## Shape A — per-service connections

One scoped `kind: mcp` connection per service, each carrying only that
service's claims:

```yaml
- name: slack
  kind: mcp
  server_id: slack
  alias: slack
  url: https://<HOST>/api/integrations/bundles/<T>/<P>/kdcube-services@1-0/public/mcp/named_services
  resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  transport: streamable_http
  delegated: true
  scopes: [named_services:use]   # Slack's real claims consent per account, not in scopes
```

Properties:

- **Independent governance.** Each service is its own delegated grant: its own
  consent demand in chat, granted and revoked on its own, and its own toggle in
  the user capabilities picker (the deny-map keys on the connection alias).
- **One entry per service.** The connection list grows with each service, and
  every service costs the user a separate consent.
- **No namespace roster in the prompt** unless a `kind: named_service` entry
  additionally declares the consumed namespaces (see the roster entry below —
  it composes with either shape).

## Shape B — whole surface

One delegated connection to the door with the union of allowed claims, plus a
companion `kind: named_service` entry declaring the consumed namespaces:

```yaml
- name: named_services
  kind: mcp
  server_id: named_services
  alias: named_services
  url: https://<HOST>/api/integrations/bundles/<T>/<P>/kdcube-services@1-0/public/mcp/named_services
  resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  transport: streamable_http
  delegated: true
  scopes: [named_services:use, conv:read]   # conv is internal (namespace claim); Slack's real claims consent per account

- name: named_services_roster
  kind: named_service
  alias: named_services
  namespaces:
    slack: { allowed: [provider.about, object.schema, object.search, object.action] }
    conv:  { allowed: [provider.about, object.search, object.get] }
```

Properties:

- **One consent covers the surface.** The grant keys on the connection's
  `resource`; the user grants this agent the door once. Per call, the `@mcp`
  guard still enforces the scope claims (`selected_tool_grants` on the door's
  auth config), so `scopes` remains the fine-grained ceiling.
- **One picker toggle.** The whole named-services capability opts in/out as a
  unit; per-namespace narrowing moves into `scopes` (admin) rather than the
  picker (user).
- **The prompt teaches the realms.** The roster entry binds NO tools in this
  bundle (`select_bound_tools` reads only `kind: python`; `mcp_connections`
  only `kind: mcp`) — it feeds the SDK instruction mechanism: the bridge
  teaching block appears with the per-namespace roster and discovery intros,
  and declaring `conv` additionally summons the `[CONVERSATION RECOVERY]`
  block (see
  [lg-react-system-prompt.md](lg-react-system-prompt.md), blocks 6–7).

## Choosing

- Services that must be granted, revoked, or user-toggled independently →
  Shape A for those services.
- A broad "the agent may work the named-service surface" posture, with the
  prompt teaching the realms and admin-side scope control → Shape B.
- The shapes compose: a whole-surface connection can coexist with a dedicated
  per-service connection to a DIFFERENT door (the deployment currently keeps
  `memories` on user-memories' own MCP surface next to the Shape-B door).

## Keeping the three declarations honest

Three lists describe the same intent and must stay consistent:

1. `scopes` on the MCP connection — what the guard enforces per call.
2. `namespaces` on the roster entry — what the prompt tells the agent it has.
3. What the door actually serves — deployment fact; read it with
   `named_services_list` after connecting.

A namespace in the roster the grant does not cover surfaces as a missing-grant
error with the Connection Hub link (the door's error contract explains it to
the agent). A namespace granted but not in the roster still works through
`named_services_list` discovery — the agent just is not pre-taught about it.
