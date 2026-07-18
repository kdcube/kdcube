---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
title: "Agents Acting On Behalf Of The User"
summary: "How an agent — hosted inside KDCube or connecting from outside — calls KDCube services as the signed-in user: every agent is a per-agent Delegated-By-KDCube client entity, consent is granted per agent in Connection Hub, the consented grant's bound token is reused each turn, and a missing grant surfaces as a one-click consent demand in chat."
status: active
tags: ["sdk", "connections", "delegated-credentials", "agents", "mcp", "consent", "connection-hub", "governance"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/consume-mcp-service-README.md
---
# Agents Acting On Behalf Of The User

A KDCube service that serves a user's own data — memories, tasks,
conversations — is reachable by more callers than the user's browser. An agent
hosted inside a KDCube app wants to read the user's memory mid-turn. Claude
Code, connected as an external MCP client, wants the same. A nightly script
holds a token the user minted for it. All three are the **same kind of
principal**: a *delegated client* acting under the user's authority, holding a
credential that is narrower than the user's session and revocable on its own.

```text
                          the user's authority
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          v                       v                       v
   hosted agent            external client          manual automation
   (an agent inside        (Claude Code, any        (a script holding a
   a KDCube app)           OAuth MCP connector)     minted token)
          |                       |                       |
   consent: in chat /      consent: OAuth          consent: created by
   Connection Hub,         authorize + consent     hand in Connection Hub
   PER AGENT               screen                  ("Create automation access")
          |                       |                       |
          +-----------------------+-----------------------+
                                  |
                                  v
              ONE grant registry: Connection Hub,
              "Delegated by KDCube" — list, inspect, revoke
```

The external-client journey is documented in
[Delegate A KDCube Service To An External Client](../../../../recipes/connections/delegate-kdcube-service-to-external-client-README.md),
the manual token in
[Create Delegated Automation Access](../../../../recipes/connections/create-delegated-automation-access-README.md).
This page owns the third position — the **hosted agent** — and the identity
model all three share.

## Every agent is a client entity, per agent

A hosted agent gets a deterministic delegated-client identity derived from the
app that defines it and its agent id:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
    delegated_client_id_for_agent,
)

client_id = delegated_client_id_for_agent(application, agent_id)
# "kdcube-agent:<application>:<agent_id>"
```

This identity is the consent boundary. The user grants access to *this agent of
this app* — the same class of entity as Claude Code in the Connection Hub
registry, listed and revocable next to it. Granting one agent grants nothing to
its siblings; the raw user session never crosses into the agent, because the
session is `(user, everything)` and cannot express a per-agent decision.

## The grant: one deduplicated record per (user, agent, resources)

Consent creates a delegated-access grant keyed to the agent's client id,
through the same `AutomationAccessService.create_access` machinery every
Delegated-By-KDCube grant uses:

```python
await automation_access.create_access(
    user,
    label="lg-react (memories)",
    resource_grants={"https://…/user-memories@…/public/mcp/memories": ["memories:read"]},
    client_id="kdcube-agent:<app>:<agent>",   # the agent identity
)
```

With a `client_id`, the record is **deduplicated** — one record per
(grantor, client, resources), so re-consent updates it instead of accumulating
rows — and marked `source: agent`. The grant binds a credential envelope to the
issued token; a KDCube `@mcp` guard authorizes exactly by that bound record, so
the token passes wherever a Connection Hub delegated credential passes. The
grant appears in the "Delegated by KDCube" tab with an *agent* badge and is
revoked there like any other.

Users never call `create_access` directly for an agent. Two surfaces do:

- **Connection Hub REST** — the `delegated_agent_grant_create` operation
  (client_id + resource + claims). It accepts only the `kdcube-agent:` client
  family; other client kinds consent through their own flows.
- **The chat consent banner** — see the demand chain below.

## Per-turn use: reuse the consented token

Each turn, the agent's tooling asks for the token the user's grant already
bound — it mints nothing on its own. The read is a `connections` named-service
operation, so the consuming app talks to a library client and the Connection
Hub app owns the store and config:

```python
# consuming app, per turn (identity rides the named-service call context)
token = await connections_client.agent_grant_token(client_id, resource)
# -> the bound bearer, or None while consent is pending
```

For MCP tool binding the SDK closes the loop in one hook:
`resolve_mcp_server_map(connections, user_sub=…, bearer_provider=…)` injects
the consented bearer into each `delegated: true` connection; a `None` from the
provider **drops the connection** — the agent never makes an unauthenticated or
session-backed call. `agent_bearer_provider(service, client_id)` builds the
hook directly over an `AutomationAccessService` when the caller lives in the
Connection Hub app itself.

```text
turn starts
  |
  v
bearer_provider(conn, user) ── connections NS: agent_grant.get_token
  |                                  |
  | token                            | none (consent pending)
  v                                  v
tool binds, @mcp guard passes   connection dropped -> consent demand
```

## The consent demand chain (reactive)

When the user has not yet granted the agent, the KDCube `@mcp` surface answers
with its ordinary 403 — the surface speaks no bespoke consent protocol. The
**client-side consent middleware** `solutions/connections/mcp_consent.py` is
the recommended wrapper for every KDCube-MCP caller. It turns that denial into
three coordinated things:

1. **A chat consent banner.** `MCPConsentRequired.chat_event_payload()` emits
   the same nested consent-event shape connected-account tools raise, carrying
   the claims and a one-click **grant action**
   (`{operation: delegated_agent_grant_create, payload: {client_id, resource,
   claims}}`). The chat UI renders "Grant access"; the action deep-links the
   Connection Hub "Delegated by KDCube" tab pre-loaded with the pending
   request, where one click creates the grant.
2. **An agent-explainable block.** The exception's message tells the model what
   is blocked, which claims to ask the user for, and that retrying before the
   grant is pointless. The hosting app also appends a `[Pending consent]` note
   to the agent's system prompt, so the agent tells the user it needs approval
   rather than reporting the capability as missing.
3. **A typed signal for the harness.** `is_kdcube_mcp_consent_denial(denial)`
   classifies the 401/403 (guard reasons like `authority_mismatch`);
   `raise_for_mcp_consent(...)` raises when it matches and stays silent
   otherwise. For LangChain-style loading, where the denial surfaces at tool
   *load* (loading connects to the server),
   `frameworks/langchain/mcp.load_mcp_tools_from_server_map(..., error_sink=…)`
   plus `load_error_looks_like_denial(error)` capture it out of the wrapped
   exception group.

```text
turn 1: user asks about memory
  agent's memory tool absent (no grant) -> consent demand bubbles in chat
  agent: "I need your approval for memories:read"
  user: clicks Grant access -> hub tab -> Grant  (one grant, THIS agent)
turn 2: bearer_provider returns the bound token
  memory tools bind -> the agent reads the user's own memory
later: user revokes in the same tab -> the tool drops back to pending
```

The proactive counterpart — showing given/pending consent per claim in the
capabilities picker before any call is attempted — is the claim-driven consent
surface; see
[Claim-Driven Consent](../claim-driven-consent/claim-driven-consent-README.md).

## Governance summary

- **Which agent** — consent is keyed to `kdcube-agent:<app>:<agent>`; each
  agent is granted, listed, and revoked individually.
- **Who crosses** — the bound delegated credential, never the user's session;
  the `@mcp` guard authorizes by the grant record, so an unbound token fails
  closed.
- **Whose data** — the credential's subject derives from the consenting user;
  the service serves that user's own resources under the granted claims.
- **What exactly** — `resource_grants` scopes claims to concrete resources; the
  connection's declared resource (its `resource`, falling back to `url`) must
  byte-match the deployment's configured delegated-resource id — commonly a
  wildcard pattern the guard matches request URLs against — so the grant's
  creation, validation, and per-turn lookup all agree on one key.
- **For how long** — grants carry a TTL (agent grants default to the delegated
  session ceiling) and re-consent refreshes the same record.
