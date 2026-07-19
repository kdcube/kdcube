---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/explore-how-agents-connect-to-kdcube-README.md
title: "Explore How Agents Connect To KDCube"
summary: "A quickstart tour of the four ways a caller reaches a KDCube app's own services on the user's behalf — a resident agent over the public MCP door, a resident agent over the in-process named-services network, an external app over the public MCP door, and a hand-provisioned automation — and the one governance model they share: a per-caller grant, a two-consent chain to connected accounts with per-account claims, and live edit or immediate revoke from Connection Hub."
status: active
tags: ["quickstart", "connections", "connection-hub", "delegated-credentials", "agents", "automation", "mcp", "named-services", "consent", "governance"]
updated_at: 2026-07-19
keywords: ["resident agent", "external app", "automation", "delegated MCP", "named-services network", "account_scope", "per-account claims", "two-consent chain", "card-authority", "revoke", "kdcube-agent"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
---
# Explore How Agents Connect To KDCube

A KDCube app serves data that belongs to a user — their memory, their mail and
Slack through named services, their knowledge, their conversations. More than the
user's browser reaches that data: an agent inside the app wants it mid-turn, an
external tool the user connected wants it, a nightly job holds a token for it.

There are **four ways to connect**, and they share **one governance model**. This
page is the map; the step-by-step wiring is
[Configuring Agent Access To Services And Accounts](../../sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md),
and the identity model behind it is
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

| Caller | Where it runs | Reaches KDCube services via | Permission is set by |
| --- | --- | --- | --- |
| **Resident agent · public MCP door** | inside a KDCube app | the app's public delegated MCP door (`/public/mcp/named_services`, `delegated: true`) | a one-click consent in chat, per agent |
| **Resident agent · named-services network** | inside a KDCube app | the in-process named-service tool layer (no HTTP hop) | a one-click consent in chat, per agent |
| **External app · public MCP door** | outside KDCube | the same public MCP door, over OAuth | an OAuth consent, then an editable card in Connection Hub |
| **Automation · hand-provisioned** | a script or scheduled job holding a minted token | the same delegated resources | an operator picks the grants when minting the token |

Every caller is the **same kind of principal** — a *delegated client* acting
under the user's authority, holding a credential narrower than the user's session
and revocable on its own. Each appears in Connection Hub under *Delegated by
KDCube*, listed and revocable next to the others.

## 1. Resident agent over the public MCP door

A resident agent (an agent an app hosts — for example a ReAct agent) declares a
delegated connection to its own app's named-services MCP door and calls it *as the
signed-in user*:

```yaml
- name: named_services
  kind: mcp
  delegated: true            # carries the agent's consented bearer, never the raw session
  url: https://<host>/api/integrations/bundles/<t>/<p>/kdcube-services@1-0/public/mcp/named_services
  scopes: [named_services:use, mail:read, slack:read]
```

The first time the agent needs the capability, a **consent card appears in chat**
naming exactly what it wants; one click grants *this agent* (its deterministic
`kdcube-agent:<app>:<agent>` identity), and every later turn reuses the bound
token. A pending grant simply drops the connection — the agent never calls
unauthenticated.

## 2. Resident agent over the named-services network

The same kind of resident agent can reach KDCube named services **in-process** —
the named-services network — instead of over the public MCP door, by declaring
`kind: named_service` tools. Nothing about governance changes: the same per-agent
grant is checked before each call, and the same one-click chat consent applies.
The only difference from mode 1 is the transport (an internal call rather than a
public HTTP door); the choice is a deployment one, covered in the configuration
guide's Surface B.

## 3. External app over the public MCP door

An app that lives **outside** KDCube — any OAuth MCP client the user chooses to
connect — reaches the same public MCP door. It consents through an OAuth screen
rather than a chat card, and its grant becomes an **editable card** in Connection
Hub: the user can widen or narrow what the app may do, in place, without the app
reconnecting. See
[Delegate A KDCube Service To An External Client](../connections/delegate-kdcube-service-to-external-client-README.md).

## 4. Automation the operator provisions by hand

For a script or a scheduled job — say an unattended content-publishing pipeline —
there is no interactive consent to click. Instead an operator **mints a
delegated-access token** in Connection Hub and **picks its resource grants
directly**, provisioning least privilege by construction. The token represents the
platform user the automation runs as; it is listed and revocable like every other
delegated client. The end-to-end steps are
[Create Delegated Automation Access](../connections/create-delegated-automation-access-README.md).

## The one governance model — two consents, per-account claims

Whichever way a caller connected, a call that reaches a connected provider account
crosses **two consents in order**:

1. **Is this caller granted?** — the per-caller grant above (`kdcube-agent:…`, an
   external app, or a minted automation token). Checked first; nothing proceeds
   without it.
2. **Does a connected account authorize the claim — and may this caller use that
   account for it?** — the account the user connected under *Delegated to KDCube*,
   refined by the caller's **per-account claims**.

Per-account claims are the precise part. A caller's grant can say *read + write
from account 1, read-only from account 2* — expressed on the grant, per account,
not inferred from what the account itself can do (an account's capability is
shared across everyone who uses it, so it cannot restrict one caller). Revoking
either consent stops the tool. Per-claim given/pending state across all of this is
the [Claim-Driven Consent](../../sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md)
surface.

## Live edit and immediate revoke

The grant **card is the authority**; the issued token is only a handle to it. So
governance is live for all four callers:

- **Edit** a card in Connection Hub — add or remove a claim, change which account
  and which permissions on it — and the change applies on the caller's **very next
  call**, on the credential it already holds. No reconnect, no re-mint.
- **Revoke** a card and the caller **drops immediately** — a resident agent's tool
  falls back to its consent-gated stub, an external app's calls stop, an
  automation's token stops resolving.

That is the whole point of routing every caller — resident agent, external app, or
automation — through one registry: *what can this thing do for me right now* always
has a readable, per-caller answer, and one click changes it.

## Where to go next

- **Wire it** — [Configuring Agent Access To Services And Accounts](../../sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md)
  (publish the provider, connect the agent, bind the accounts).
- **Understand the identity model** — [Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).
- **Provision an automation** — [Create Delegated Automation Access](../connections/create-delegated-automation-access-README.md).
- **See the whole Connection Hub** — [Connection Hub Solution](../../sdk/solutions/connections/connection-hub-solution-README.md).
