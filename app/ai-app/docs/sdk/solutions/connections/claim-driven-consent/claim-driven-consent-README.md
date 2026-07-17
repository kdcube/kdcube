---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
title: "Claim-Driven Consent For Integrations"
summary: "One claim-first consent surface for EVERY integration an agent can use — delegated MCP, connected accounts, named-service realms. Each integration declares the raw claims it needs; a single resolver returns per-claim given/pending/unavailable from the two consent stores, rendered from the grant vocabulary so no service must author a friendly taxonomy; the mint gate refuses to act without consent. The service-defined Read/Actions grouping (Slack) is optional enrichment on top of the claim base."
status: active
tags: ["sdk", "connections", "consent", "claims", "delegated-mcp", "connected-accounts", "capabilities", "governance"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/hosted-agent-conversation-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/settle-your-solution-in-kdcube-README.md
---
# Claim-Driven Consent For Integrations

An agent's integrations — a delegated MCP tool that reads the user's own KDCube
memory, a connected-account tool that acts on the user's Slack, a named-service
realm — all rest on the same question: **does this user consent to the agent
using this, and on which claims?** MCP standardizes how a tool is called; it does
not decide who may cross the endpoint or whose data the call reaches. KDCube
answers that with one **claim-first** consent surface every integration shares.

The base is the **raw grant vocabulary** — claims like `memories:read`,
`slack:write`. Each integration declares the claims it needs; a single resolver
returns each claim's state; the capabilities picker shows it; the mint gate
refuses to act without it. A service that ALSO authored a friendly operation
taxonomy (Slack's Read/Actions groups) gets that as **enrichment** layered on
top — never a prerequisite, so an integration that declares only a bare claim
(the memory MCP) still shows and enforces consent.

## The states

For one user and a set of required claims, each claim resolves to:

| State | Meaning | Picker |
| --- | --- | --- |
| `given` | consented / connected — usable now | ✓ |
| `pending` | delegable/connectable to this user, not yet granted | offer a grant/connect action |
| `unavailable` | cannot be granted (role not permitted, provider not enabled) | shown, not actionable |

An integration's header rolls up: **pending if ANY required claim is pending.**

## The two consent stores

A claim's consent lives in one of two stores, selected by the claim's `source`:

- **`delegated_by_kdcube`** — the user's OWN KDCube resources (memory, tasks,
  conversations). Backed by `delegated_credentials/automation_access.py` (the
  Connection Hub "Delegated by KDCube" tab). Granted claims come from the user's
  approved delegations (`list_access`); the grant vocabulary (label, description,
  which roles may delegate it) comes from the deployment's
  `delegated_credentials.oauth.capabilities` config.
- **`connected_account`** — an EXTERNAL provider account (Slack, Gmail). Backed
  by the `delegated_to_kdcube` broker. A claim's state comes from the broker's
  per-claim resolution: a clean resolution is `given`; a `connect_required` /
  `claim_upgrade_required` / `account_required` / `reconnect_required` reason is
  `pending`.

## The SDK (framework-neutral, reusable)

`sdk/solutions/connections/`:

- **`consent_state.py`** — the resolver. `ClaimRequirement(claim, source,
  provider?, connector?)`, `ClaimConsent(claim, state, label, description,
  grant_action)`, `IntegrationConsent` (rolls up). `resolve_integration_consent(
  integration, requirements, *, user, delegated_reader, connected_reader)` reads
  the delegated granted-set once per user and resolves each claim via its store.
  Store readers are INJECTED protocols — the module has no Redis/store coupling
  and is unit-testable. `claim_requirements_from_connection(conn)` derives the
  requirements from a declared tool connection (a `delegated: true` `kind: mcp`
  connection's `scopes`; a tool's `connected_accounts` claims).
- **`consent_state_adapters.py`** — the store readers over the real stores.
  `DelegatedGrantStoreReader(service, capabilities, user_roles)` maps
  `AutomationAccessService.list_access` + the capabilities config;
  `ConnectedAccountStoreReader(resolve, labels)` maps broker reasons. Heavy store
  construction is passed in by the caller (the inventory), so the mapping stays
  testable.
- **`delegated_mcp.py`** — `resolve_mcp_server_map(connections, *, user_sub,
  bearer_provider)` binds each `delegated: true` connection with the bearer the
  user's PER-AGENT grant already bound (`agent_bearer_provider` builds the hook
  over the delegated-grant store; the agent identity is
  `delegated_client_id_for_agent(app, agent)`). A `None` bearer DROPS the
  connection (consent pending — surface it, do not act). A `consent_gate`
  (`async (scopes) -> bool`) guards the mint fallback the same way when no
  provider is wired. The full acting-as-the-user model lives in
  [Agents Acting On Behalf Of The User](../agent-acting-for-user/agent-acting-for-user-README.md).

## Declaring the claims an integration needs

One vocabulary, per integration kind:

```yaml
# delegated MCP — the agent acts as the user against a KDCube @mcp surface
- name: memories
  kind: mcp
  delegated: true
  scopes: [memories:read]          # -> delegated_by_kdcube claims

# a tool acting on the user's external account
- name: slack_post
  kind: python
  connected_accounts:
    - provider_id: slack
      connector_app_id: <app>
      claims: [slack:read, slack:write]   # -> connected_account claims
```

## The integration contract (how the surface is assembled)

1. **Inventory** — when it builds an agent's capability catalog, for each
   integration it derives the claim requirements
   (`claim_requirements_from_connection`), constructs the two store readers from
   the runtime context (Redis, tenant/project, the delegated config, the user's
   roles), and attaches `resolve_integration_consent(...).to_dict()` to the
   `agent_capabilities` catalog entry. This enrichment is FAIL-OPEN: any store
   error omits the consent block (the catalog is never broken by it).
2. **Picker** — renders each integration claim-first: a row per claim (the grant
   vocabulary's label + `given`/`pending`/`unavailable` + a grant action for
   pending), with the header state as the rollup. An integration that carried a
   granular operation taxonomy (`claims_by_operation` + friendly labels) is
   rendered with its Read/Actions grouping as enrichment; one that did not falls
   back to the raw-claim rows. Never blank. When the consent block is absent
   (fail-open omitted it), the integration renders as before — no regression.
3. **Binding gate** — the agent's MCP wiring binds a delegated connection only
   when its claims are `given`: the `bearer_provider` returns the consented
   grant's token, or nothing while consent pends — an unbound connection raises
   the reactive consent demand (banner + agent note) instead of a silent gap.
   The gate's failure posture (e.g. treat an unreadable consent store as
   pending) is the caller's choice and is logged.

## Governance, made concrete

The four decisions MCP does not make, now claim-anchored:

- **which agent** — the per-agent tool allow-list (admin ceiling ∩ user picker),
  and consent itself keyed to the per-agent client identity.
- **who crosses** — the per-user delegated bearer the consent grant bound.
- **whose data** — the token subject IS the signed-in user; the `@mcp` surface
  serves only their own resources.
- **who pays** — metered iff the tool runs a marked model call.

## What is reusable vs bundle-local

The whole consent surface (resolver, adapters, derivation, gate) is SDK — any
hosted agent (any framework) reuses it. A bundle declares its integrations'
claims and wires the gate; it writes no consent logic. Worked instance: the
`ported-langgraph-agents` `lg-react` agent's delegated `memories` MCP connection.
