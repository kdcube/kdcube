---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
title: "Conversation-Scoped Agent Capabilities"
summary: "How users control what an agent may use in each conversation: the admin inventory as ceiling, an optional user baseline for future conversations, conversation-scoped selections, explicit Save changes, runtime narrowing, and realm-described service cards."
status: current
tags: ["sdk", "solutions", "user-settings", "capabilities", "agent-selection", "named-services", "picker", "widget"]
updated_at: 2026-07-12
keywords:
  [
    "agent_capabilities",
    "agent_selection_update",
    "capability picker",
    "capabilities widget",
    "per-user tools",
    "conversation-scoped agent settings",
    "Save changes",
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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/work-with-subagents-README.md
---
# Conversation-Scoped Agent Capabilities

An agent's configuration grants it tools, skills, services, and models. Each
user then decides which of those the agent may use in **this conversation**.
The composer picker edits a local draft and persists it only when the user
presses **Save changes**; the runtime enforces that exact conversation row on
every turn. A dedicated widget manages the user baseline from which new
conversations start. This doc owns that surface end to end.

## The model: ceiling → pick → enforcement

```text
ADMIN INVENTORY (bundles.yaml)          the ceiling
  surfaces.as_consumer.agents.<id>      tools/skills/MCP/namespaces
  supported_models                      the model list users may pick from
        |
        v  agent_capabilities (read: inventory + saved selection + coverage)
CONVERSATION SELECTION (deny-list)      narrows, never widens
  user baseline (or application fallback) -> materialized once
  picker toggles -> local draft -> explicit Save changes
  -> agent_selection_update(conversation_id)
  clamped on write to the live inventory; system tools stay locked on
  persisted per (user, app, conversation, agent) in user_bundle_props
        |
        v  applied at turn start (apply_user_agent_selection)
RUNTIME NARROWING                       denied = uncallable
  denied tool groups/tools    -> removed from the turn's tool config
  denied namespaces           -> gone from roster and dispatch
  denied operations/actions   -> rejected at named-service dispatch
  model pick                  -> overlays the strong decision role
```

Two operations carry the whole flow, both on the agent's app:
`agent_capabilities` (POST, read: the pickable inventory, the conversation's
saved selection, consent coverage) and `agent_selection_update` (POST,
explicit merge-write of the draft). In chat, both carry `conversation_id`.
Storage shape, default/materialization rules, merge/clamp semantics, and the pending
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
| Subagents | `subagents: true` | Delegation leaves the agent's turns: the spawner is not installed, `react.delegate` is absent from the catalog, and the delegation guidance is absent from the instructions. |

Deny keys clamp on write against the live inventory: operations clamp to the
configuration's allowed set, actions to the realm's declared action names,
the subagents toggle to the ability actually being offered — a stored
selection never references anything outside the grant.

Most of the pickable inventory comes from what the admin granted under
`surfaces.as_consumer.agents.<id>`. Subagent delegation joins it from the
react agent config: `react: agents: <id>: subagents: true` puts the ability
in the inventory, default ON for users (absent = not offered; see
[Work With Subagents](../../agents/react/work-with-subagents-README.md)).
The catalog exposes it as the `subagents` entry with its picker copy —
helpers can raise the quality of hard tasks, and each helper runs on
additional model calls billed to the user's account, so the paying user
decides — the same principle as the model pick, where the admin declares the
allowed list and the user picks their own price/quality point.

## The picker: one body, three shells

The capability picker is one component
(`useCapabilityPickerBody` in `@kdcube/components-react/chat`) rendered into
three presentations. Interaction state (toggles, spotlight, the confirm
picker) lives above the shells, so switching mid-interaction keeps it.

| Shell | Where | When it is the right form |
| --- | --- | --- |
| Composer popover | The chat composer's "+" button | Draft and save the current conversation's model and capabilities. |
| Expanded modal | The popover's expand affordance (canvas-modal shell, Esc/backdrop/collapse) | The same current-conversation draft with room for service-card descriptions. |
| `capabilities` widget | Served full-page by the agent's app; mountable on any scene | Uses the `conversation_id` supplied by a chat-originated `capabilities.open`; when independently mounted with no id, manages the user baseline for future conversations. |

The two chat shells share one draft. Closing and reopening the picker in the
same conversation keeps it; switching conversations drops unsaved edits.
**Save changes** sends one partial patch bound to the current conversation id.
A save already in flight is reconciled only if that conversation is still
open, so a late response cannot overwrite another conversation's picker.

The served widget follows the standard widget contract (auth + CONFIG
handshake; see
[App Widget Integration](../../bundle/bundle-widget-integration-README.md)),
registers as `@ui_widget(alias="capabilities")` with its build mapping under
`config.ui.widgets.capabilities`, and takes the agent from widget config
(`?agent=` scene param or the handshake's `agentId`), defaulting to the
app's default agent. A `capabilities.open` command may also supply the active
`conversation_id`; the widget forwards it on both read and update operations.

That is how the baseline row is populated: only an independently mounted,
unscoped capabilities read or update reaches `agent_selection:<agent_id>`.
Chat-originated windows carry the conversation id and never write it. If the
row has never been written, a new conversation starts from the application's
configured inventory and configured model. Hosts must present the unscoped
widget as **defaults for future conversations**; opening it as though it were
the current conversation editor would silently change scope.

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
| `presentation.operations` / `presentation.actions` (name → label + description) | Group summaries and detail rows: the human names ("Send email") title each entry and join into its group's summary line; the grammar token rides the detail row as a mono hint. |
| `connected_accounts` (provider_id, connector_app_id, claims, `claims_by_operation`, provider_label, claim_labels) | Per-entry "via your connected Google account · send mail" lines and the consent chips. |

The card presents the realm's entries as three human capability groups —
Read / Create & update / Actions — classified from the realm's own entries
(named actions always land in Actions; operation tokens classify by verb).
Each group is one toggle, summarized by its entries' human labels; expanding
a group's details reveals the per-entry rows, each still a toggle (the
namespace-operation/action granularity above). Entries the admin excluded
collapse to one quiet expandable line per service; its tooltip names the
admin fix path (`namespaces.<ns>.allowed`). An INTENTIONAL exclusion with a
declared note (`namespaces.<ns>.excluded.<op>.reason` in the consumer
descriptor) renders that reason on its row instead of the admin sentence —
"Reading rides the context tools — the agent pulls task refs directly" —
with no admin tooltip: the capability is served through another path by
design, so no fix is pending. The same declaration's `agent_hint` drives the
in-turn reroute on the dispatch denial (fix actor `agent`, see
[Named-Service Providers](../../namespace-services/providers-README.md)).
A namespace whose realm is unresolvable expands to exactly "This service
hasn't described itself yet." — the honest state; the UI invents no copy.

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
