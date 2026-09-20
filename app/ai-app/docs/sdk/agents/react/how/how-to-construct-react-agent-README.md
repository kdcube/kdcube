---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
title: "How To Construct A ReAct Agent"
summary: "The full story of creating and customizing a ReAct agent from app code and config: construction, per-agent configuration, live Card capability projection, user preferences, and prompt-cache consequences."
status: current
tags: ["sdk", "agents", "react", "how-to", "configuration", "per-user-selection", "control-card", "supported-models", "composer-menu"]
updated_at: 2026-09-20
keywords:
  [
    "build_react",
    "agent_tool_config_from_bundle_props",
    "agent_skill_config_from_bundle_props",
    "apply_user_agent_selection",
    "apply_delegated_tool_claims",
    "supported_models",
    "agent_capabilities",
    "agent_selection_update",
    "conversation scoped agent selection",
    "Save changes",
    "additional_instructions",
    "role_models",
    "prompt cache invalidation",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/chat-with-react-agent-README.md
---
# How To Construct A ReAct Agent

A ReAct runtime object is assembled fresh for every turn from four inputs:
**app code** (the workflow that calls `build_react`), **app config** (the
per-agent blocks in `bundles.yaml`), the user's **live Card capability
projection and scoped preferences**, and **durable turn state** (timeline,
workspace refs, memory, and runtime context). Fresh construction does not mean
stateless execution. This article walks the pipeline, configuration, selection,
consent boundary, and prompt-cache consequences.

## 1. The construction pipeline

The reference implementation is the workspace app's react node
(`examples/bundles/workspace@2026-03-31-13-36/agents/main.py`); every app
workflow that subclasses `BaseWorkflow` follows the same shape:

```text
turn arrives (BaseWorkflow.__init__ built runtime_ctx: tenant/project/user_id/
              conversation_id/turn_id/bundle_id/agent_id, iteration budget, …)
  │
  ├─ 1. agent_tool_config_from_bundle_props(bundle_props, agent_id)
  │      → AgentToolConfig: tool_specs, mcp_tool_specs, tool_runtime,
  │        tool_traits, allowed_plugins, allowed_tool_names_by_alias,
  │        tool_claim_policies          (from surfaces.as_consumer.agents.<id>.tools)
  ├─ 2. agent_skill_config_from_bundle_props(bundle_props, agent_id)
  │      → AgentSkillConfig: custom_skills_root, agents_config
  │                                     (from surfaces.as_consumer.agents.<id>.skills)
  ├─ 3. apply_user_agent_selection(tool_config, skill_config)
  │      → current descriptor Control Card ∩ resident user selection narrows
  │        both configs; scoped preference picks overlay the runtime context
  │        (capabilities fail closed; preferences use configured fallbacks)
  ├─ 4. apply_delegated_tool_claims(tool_config)
  │      → demand-driven consent: every claim-gated tool STAYS available; a
  │        tool attempt with unmet claims raises the ask (structured consent
  │        result + scoped chat banner). This hook announces satisfied
  │        demands via runtime_ctx.reactivated_tools
  ├─ 5. compose additional_instructions
  │      (config additional_instructions + memory teaching + named-service
  │       roster — the durable, cacheable teaching text)
  ├─ 6. build_react(mod_tools_spec, mcp_tools_spec, tools_runtime, tool_traits,
  │                 custom_skills_root, skills_visibility_agents_config,
  │                 additional_instructions, event_source_specs, scratchpad)
  │      → the runtime instance (v2/v3 per AI_REACT_AGENT_VERSION)
  └─ 7. react.run(allowed_plugins, allowed_tool_names_by_alias)
         → the loop; each decision round renders instructions + timeline
           + the uncached ANNOUNCE tail
```

Role→model resolution happens lazily per model call: the runtime binds
`runtime_ctx.agent_role_models` into the invocation's `role_models` overlay,
and the model router resolves `role → {provider, model}` with the overlay
beating app-level `role_models`. The full resolution chain (code defaults →
`bundles.yaml` → invocation overlay) is owned by
[Bundle Agent Integration §2A](../../../bundle/bundle-agent-integration-README.md#2a-model-selection-for-agent-roles).

Step 3 is the turn-start authority pass. It resolves the current descriptor
Control Card and resident agent Card, narrows tools, skills, named services,
targets, resources, and subagents from their positive live projection, then
applies validated model/instruction/presentation preferences. An unavailable
Card projection removes every selectable capability; a preference-store error
uses configured preference defaults without widening that projection. Step 4
is deliberately **not** another narrower. Connected-account claims remain
attached to selected tools and are enforced at the concrete tool attempt. The
turn-start hook only checks whether a claim demanded earlier in this
conversation has since become satisfied and, if so, publishes that transition
through ANNOUNCE.

## 2. The per-agent config surface

Two config roots shape one agent. ReAct behavior resolves through the agent
id, its filesystem-safe form, `default_agent`, `default`, and the ReAct root;
both direct per-agent blocks and `react.agents.<id>` are recognized. This lets
settings be declared per agent or once as a default:

**`config.react.<agent-key>` — runtime behavior and teaching:**

| Key | Shapes |
| --- | --- |
| `additional_instructions` | Durable teaching text appended to the system instruction. |
| `instructions` (body/blocks via `build_react` args `instruction_body` / `instruction_blocks`) | Full replacement/extension of the instruction composition when the app builds them in code. |
| `supported_models` | The admin-allowed model list users pick from (rows: `model`, `provider`, `label` — the economics price-table naming, so every allowed model is one the platform accounts for). |
| `role_models` | Per-agent role→model mapping (overrides the app-level `config.role_models` for this agent's runs). |
| `subagents` | Whether helper delegation is offered for this agent, plus visibility/model defaults. The user's conversation selection may turn an offered capability off. |
| `max_iterations` | Base decision/tool-round budget. |
| `render_thinking`, `debug_timeline`, `event_source_pipeline.enabled`, `story_snapshots.enabled` | Runtime switches. |

Resolution details, env fallbacks, and every `RuntimeCtx` field are owned by
[Runtime Configuration](../runtime-configuration-README.md).

**`surfaces.as_consumer.agents.<id>` — what the agent is connected to:**

| Key | Shapes |
| --- | --- |
| `tools` (list of connections) | Python tool groups (`module`/`ref`, `alias`, `allowed`, `tool_traits`, `runtime`, `tool_claims`), MCP servers (`server_id`, `allowed`), named-service namespaces (`namespaces.<ns>.allowed` operations). |
| `skills` | `custom_root` for app-local skills + per-consumer `enabled`/`disabled` visibility patterns. |
| `event_sources` | Named-service event/pull policies feeding the timeline. |

This is the agent's **inventory**: the administrator's grant of everything the
agent may use. Connection kinds and MCP wiring are owned by
[Bundle Agent Integration](../../../bundle/bundle-agent-integration-README.md).

Example (one agent, both roots):

```yaml
config:
  react:
    default_agent:
      max_iterations: 15
      additional_instructions: |
        [HOUSE STYLE]
        Cite sources as browsable URLs.
      supported_models:
        - model: claude-sonnet-4-6
          provider: anthropic
          label: Sonnet 4.6
        - model: claude-haiku-4-5-20251001
          provider: anthropic
          label: Haiku 4.5
  surfaces:
    as_consumer:
      default_agent: main
      agents:
        main:
          tools:
            - name: io
              kind: python
              module: kdcube_ai_app.apps.chat.sdk.tools.io_tools
              alias: io_tools
              allowed: [tool_call]
            - name: web
              kind: python
              module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
              alias: web_tools
              allowed: [web_search, web_fetch]
          skills:
            custom_root: skills
            consumers: {}
```

## 3. System instruction vs ANNOUNCE: where a customization belongs

The rendered context has two homes for app- and platform-supplied text, with
opposite lifecycles:

| | System instruction | ANNOUNCE |
| --- | --- | --- |
| Lifecycle | Durable across the conversation | Recomputed every decision round |
| Caching | Part of the big cached prompt slice | Never cached, by design |
| Belongs here | Teaching: house style, domain rules, tool catalog and traits, the named-service roster, skill galleries | State: `[BUDGET]`, `[RUNTIME LIMITS]`, `[CONTEXT CAPS]`, `[USER MEMORY HOTSET]`, `[WORKSPACE]`, `[CACHE]`, `[DELEGATION]`, connected-account reactivation, temporal ground truth |
| App hook | `additional_instructions` / `instruction_body` / `instruction_blocks` on `build_react` | `RuntimeCtx` and durable turn fields the announce composer reads (for example `memory_hotset`, `cold_turn_marker`, `reactivated_tools`) |

The placement rule is the **lifecycle test**: stable teaching belongs in the
instruction; current situational truth belongs in ANNOUNCE. A user capability
toggle is not merely situational prose: it changes the callable catalog, so it
must change the instruction and causes one governed cold turn. Budgets,
workspace state, live events, memory hotset, and consent reactivation can
change without changing the callable contract and belong in ANNOUNCE. Section
semantics and examples are owned by
[ReAct Announce](../react-announce-README.md); the caching mechanics by
[Context Caching](../context-caching-README.md).

A worked example of the rule: connected-account consent is demand-driven —
a tool invocation with unmet claims returns the structured consent envelope
to the agent (and raises the scoped chat banner); when the user approves
mid-conversation, `apply_delegated_tool_claims` publishes the transition on
`runtime_ctx.reactivated_tools` — rendered as `[CONNECTED ACCOUNTS UPDATE]`
in ANNOUNCE. The instruction text stays byte-identical whether or not the
account is connected.

## 4. Live capability projection and user preferences

On top of the descriptor inventory, each signed-in user has one stable
resident Card for an app/agent caller profile. KDCube also materializes a
stable credentialless Control Card from that app/agent descriptor and links it
to the resident Card. Step 3 uses their current intersection on every turn:

```text
effective capabilities = current descriptor Control ∩ resident Card selection
```

The resident Card stores a positive capability selection. A newly published
descriptor capability is offered but remains unselected; a removed capability
is denied by the next live intersection. Neither change creates a new resident
identity or requires a new hosted-agent credential. System tools remain
outside the selectable boundary.

Two operations on the SDK entrypoint base expose this contract to registered
users and above:

- `agent_capabilities` returns the descriptive catalog, scoped preference
  choices, the current Card projection, and `allowed_selected`,
  `allowed_unselected`, or `not_allowed` on each capability row.
- `agent_selection_update` replaces the visible positive Card selection and
  may update model, instruction, presentation, and cache preferences in the
  same save.

An unavailable Card projection fails closed for selectable capabilities. The
picker shows those rows as **Not permitted**, and the runtime removes them.
PostgreSQL stores the preference fields and only a compatibility seed for the
first Card migration. Preference failures fall back to configured model and
presentation behavior without granting any missing capability.

The complete capability families, Card bootstrap, three-state picker, wildcard
resource semantics, and descriptor-change rules are owned by
[Agent Capability Control And Selection](../../../solutions/user-settings/capabilities-README.md).

## 5. Cache implications — what a switch costs

Per-user customization interacts directly with prompt caching, and the costs
differ sharply by category:

- **Switching the model destroys the prompt cache completely.** Provider
  prompt caches are per model: the first turn on the newly picked model pays
  full input cost for the entire context (instructions, tool catalog, the
  whole visible timeline), exactly as a brand-new conversation would. The
  cache warms again from that turn on.
- **Toggling a capability colds the entire prompt for one turn.** The tool
  catalog, skill gallery, namespace teaching, and delegation guidance live in
  one cached system block before timeline messages. Changing a tool, skill,
  MCP server, namespace, operation/action, or subagent capability invalidates
  that block and every downstream history cache point for the next applicable
  turn. Same model, so caching can resume after that turn.
- **Turn-local state is free.** Everything routed through ANNOUNCE (including
  budget, workspace, memory hotset, delegation progress, and consent
  reactivation) changes nothing in the stable cached prefix — that is why the
  lifecycle rule in section 3 exists.

Two platform behaviors build on this, and both ship. First, the composer menu
states the mechanism before a costly change lands: picking a different model
shows an inline, non-blocking cost notice ("Switching the model starts a fresh
context cache — the next turn is billed at full input rates while the cache
rebuilds."), and the first tool/skill toggle per menu-open shows the milder
equivalent; both stay silent while the conversation has no turns yet, where
nothing is cached.

Second, the **cold-cache policy** — and because the user pays for the cache,
the user holds it. PostgreSQL stores the standing policy and deferred
model/instruction/presentation deltas; admin config supplies the default and
allowed set (`config.react.<agent>.cache.selection_change_policy`). Under
`confirm`, the decision moment is the policy picker. A resident Card
capability edit is authority and applies immediately; it cannot be pinned to
an older conversation snapshot. The next applicable warm turn records the
resulting cold turn. Preference deltas may wait for a different conversation
or a cold cache. At the runtime choke point, a change that lands on a warm
conversation emits an ANNOUNCE `[CACHE]` line plus `cache_cold_turn`
accounting metadata, so the rebuild premium is attributable within the turn's
spend. Preference failures use configured defaults; Card projection failure
closes selectable capabilities.

## 6. How the chat component connects

The chat engine carries the agent identity and the selection UI end to end:

- `EngineConfig.agentId` (default `main`) rides every message target and event
  batch and scopes the selection operations. The resident capability Card is
  scoped to the user/app/agent profile; `conversation_id` scopes preference
  choices and pending cache policy.
- The composer "+" menu is fed by `agent_capabilities` (lazy, on first open)
  and keeps toggles as a local draft. **Save changes** sends one
  `agent_selection_update`; sending a chat message does not save the draft
  implicitly.
  Sections: Model (radio pick with the configured default tagged), Skills,
  Tools (two-level per-tool rows), MCP servers, Services (namespaces), plus a
  Connection-Hub entry that renders only when opening it can actually happen —
  a host that acks the `connection_hub.settings` surface command owns the
  open, and without an ack the served connections widget opens directly.
- Saved capability toggles revise the resident Card immediately and affect the
  next turn's live projection. Saved preference changes follow their selected
  cache-policy timing. Switching conversations discards unsaved UI edits and
  loads the same live Card selection alongside that conversation's preference
  record. A chat-originated Capabilities window carries the conversation id so
  preference updates retain the right scope.

The engine API detail (state branch, draft/save methods, switch-race handling) is owned by
[Chat Engine](../../../npm/components-core/chat-engine-README.md); the
end-to-end app wiring by the
[chat-with-react-agent recipe](../../../../recipes/components/chat-with-react-agent-README.md).
