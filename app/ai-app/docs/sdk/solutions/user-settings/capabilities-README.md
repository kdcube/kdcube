---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
title: "Per-User Agent Capabilities"
summary: "How users control what an agent may use for them: the admin inventory as the ceiling, per-user selection that narrows within it, runtime narrowing that makes denied capabilities uncallable, the capability picker's three shells (composer popover, expanded modal, served `capabilities` widget), and the service cards realms self-describe into."
status: current
tags: ["sdk", "solutions", "user-settings", "capabilities", "agent-selection", "named-services", "picker", "widget"]
updated_at: 2026-07-08
keywords:
  [
    "agent_capabilities",
    "agent_selection_update",
    "capability picker",
    "capabilities widget",
    "per-user tools",
    "namespace narrowing",
    "object.action",
    "service card",
    "realm presentation",
    "deny-list",
    "supported_models",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
---
# Per-User Agent Capabilities

An agent's configuration grants it tools, skills, services, and models. Each
user then decides which of those the agent may use **for them** — from the
chat composer or from a dedicated widget — and the runtime enforces that
choice on every turn. This doc owns that surface end to end: the model, the
selection granularity, the picker's three presentations, and the service
cards realms describe themselves into.

## The model: ceiling → pick → enforcement

```text
ADMIN INVENTORY (bundles.yaml)          the ceiling
  surfaces.as_consumer.agents.<id>      tools/skills/MCP/namespaces
  supported_models                      the model list users may pick from
        |
        v  agent_capabilities (read: inventory + saved selection + coverage)
USER SELECTION (deny-list)              narrows, never widens
  picker toggles -> optimistic flip -> debounced agent_selection_update
  clamped on write to the live inventory; system tools stay locked on
  persisted per (user, app, agent) in user_bundle_props
        |
        v  applied at turn start (apply_user_agent_selection)
RUNTIME NARROWING                       denied = uncallable
  denied tool groups/tools    -> removed from the turn's tool config
  denied namespaces           -> gone from roster and dispatch
  denied operations/actions   -> rejected at named-service dispatch
  model pick                  -> overlays the strong decision role
```

Two operations carry the whole flow, both on the agent's app:
`agent_capabilities` (POST, read: the pickable inventory, the caller's saved
selection, consent coverage) and `agent_selection_update` (POST, merge-write
of partial toggles). Storage shape, merge/clamp semantics, and the pending
cache-policy delta are owned by the
[User Settings Solution](user-settings-solution-README.md); the inventory's
config source is owned by
[How To Construct A ReAct Agent](../../agents/react/how/how-to-construct-react-agent-README.md).

## Selection granularity

| Level | Deny key | Effect |
| --- | --- | --- |
| Model | `model: {provider, model}` pick (not a denial) | Overlays the strong decision role; `null` returns to the configured default. Only `supported_models` entries are pickable. |
| Skill | `skills: [<id>]` | Skill drops from the agent's gallery. |
| Tool group | `tools: {<alias>: true}` | The whole group leaves the turn's tool config. System groups (`io_tools`, `ctx_tools`) stay locked on. |
| Individual tool | `tools: {<alias>: [<name>]}` | One tool leaves the group; siblings stay. |
| MCP server | `mcp: {<server_id>: true}` | Server and all its tools drop. |
| MCP tool | `mcp: {<server_id>: [<name>]}` | One listed tool drops (wildcard servers subtract via `denied_tools`). |
| Namespace | `named_services: {<ns>: true}` | The realm leaves the roster and dispatch entirely. |
| Namespace operation | `named_services: {<ns>: ["object.search", ...]}` | The operation is rejected at named-service dispatch for this user's turns. |
| Named action | `named_services: {<ns>: ["object.action.<name>"]}` | Exactly that action name is rejected at dispatch; sibling actions still ride `object.action`. |

Deny keys clamp on write against the live inventory: operations clamp to the
configuration's allowed set, actions to the realm's declared action names —
a stored selection never references anything outside the grant.

## The picker: one body, three shells

The capability picker is one component
(`useCapabilityPickerBody` in `@kdcube/components-react/chat`) rendered into
three presentations. Interaction state (toggles, spotlight, the confirm
picker) lives above the shells, so switching mid-interaction keeps it.

| Shell | Where | When it is the right form |
| --- | --- | --- |
| Composer popover | The chat composer's "+" button | Quick toggles while writing a message. |
| Expanded modal | The popover's expand affordance (canvas-modal shell, Esc/backdrop/collapse) | Reading service cards: descriptions wrap instead of ellipsizing. A consent-banner spotlight that targets a namespace or a long tool list opens this form directly. |
| `capabilities` widget | Served full-page by the agent's app; mountable on any scene | Managing capabilities as its own task, outside a conversation. |

The served widget follows the standard widget contract (auth + CONFIG
handshake; see
[App Widget Integration](../../bundle/bundle-widget-integration-README.md)),
registers as `@ui_widget(alias="capabilities")` with its build mapping under
`config.ui.widgets.capabilities`, and takes the agent from widget config
(`?agent=` scene param or the handshake's `agentId`), defaulting to the
app's default agent.

## Service cards: realms describe themselves

An expanded namespace renders as a service card built ONLY from the realm's
own self-description — the same contract the agent reads through
`provider.about`/`object.schema`. A realm author declares, in the provider
spec's metadata (see
[Named-Service Providers](../../namespace-services/providers-README.md)):

| Declaration | Renders as |
| --- | --- |
| `presentation.about` | The purpose line under the realm label ("Read, search, and send email from the mail accounts you connect."). |
| `presentation.third_party` | The dependency line ("Works with your Slack workspace through your connected Slack account."). |
| `object_kinds` (name → one-liner) | The compact "Objects: message · attachment · account" line, full descriptions in the tooltip. |
| `presentation.operations` / `presentation.actions` (name → label + description) | Entry rows titled by human name ("Send email"), the grammar token demoted to a mono hint, one-line description. |
| `connected_accounts` (provider_id, connector_app_id, claims, `claims_by_operation`, provider_label, claim_labels) | Per-entry "via your connected Google account · send mail" lines and the consent chips. |

Every entry row is a toggle (the namespace-operation/action granularity
above). A namespace whose realm is unresolvable expands to exactly
"This service hasn't described itself yet." — the honest state; the UI
invents no copy.

## Consent and cache cost (pointers)

- Coverage chips (CONNECTED / CONSENT) show each row's connected-account
  state read-only; the CONSENT chip seeds the consent plan with exactly the
  unmet claims — semantics owned by
  [Delegated Accounts](../connections/delegated-accounts/delegated-accounts-README.md).
- Effective claims recompute over the narrowed set: a user who denied
  `object.action.send` is never asked for the send claim — same doc.
- A toggle on a warm conversation may rebuild the prompt cache; the confirm
  picker and the user-held policy that govern this are owned by
  [ReAct Context Caching](../../agents/react/context-caching-README.md).
