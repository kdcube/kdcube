---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
title: "How To Construct A ReAct Agent"
summary: "The full story of creating and customizing a ReAct agent from app code and config: construction, per-agent configuration, conversation-scoped model/capability selection, and prompt-cache consequences."
status: current
tags: ["sdk", "agents", "react", "how-to", "configuration", "per-user-selection", "supported-models", "composer-menu"]
updated_at: 2026-07-16
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
per-agent blocks in `bundles.yaml`), the **conversation-scoped user
selection**, and **durable turn state** (timeline, workspace refs, memory, and
runtime context). Fresh construction does not mean stateless execution. This
article walks the pipeline, configuration, selection, consent boundary, and
prompt-cache consequences.

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
  │      → the user's saved deny-list narrows both configs; the user's model
  │        pick lands on runtime_ctx.agent_role_models  (fail-open, per turn)
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

Step 3 is the turn-start narrowing pass: it removes user-disabled entries,
applies the validated model pick and subagent toggle, and governs when a
cache-colding selection change lands. Step 4 is deliberately **not** another
narrower. Connected-account claims remain attached to configured tools and are
enforced at the concrete tool attempt. The turn-start hook only checks whether
a claim demanded earlier in this conversation has since become satisfied and,
if so, publishes that transition through ANNOUNCE. Bookkeeping failure keeps
the configured set; it never bypasses the operation-level authorization fence.

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

## 4. The per-user selection layer

On top of the admin-granted inventory, each signed-in user can narrow what the
agent uses in one conversation. The effective selection is stored per
`(user, app id, conversation, agent)` and applied in step 3. An optional
user/app/agent baseline seeds a new conversation once; after materialization,
that conversation owns its durable selection.

**Two operations on the SDK entrypoint base** (available to every chat app,
declared for registered users and above):

- `agent_capabilities` — returns the pickable inventory (python tool groups
  with per-tool docs, MCP servers with per-tool entries when knowable,
  named-service namespaces, concrete skills with front-matter,
  `supported_models` + the configured `default_model`) plus the caller's
  current selection.
- `agent_selection_update` — merge-writes partial toggles, clamped against the
  live inventory on every write.

**The record is a deny-list plus one pick:**

```json
{
  "schema_version": 1,
  "disabled": {
    "tools": {"gmail": true, "web_tools": ["web_fetch"]},
    "mcp": {"knowledge": ["kb_fetch"]},
    "named_services": {
      "mail": ["object.action.send"],
      "task": ["object.delete"]
    },
    "skills": ["public.docx-press"],
    "subagents": true
  },
  "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
}
```

Semantics, enforced at both ends (clamp on write, `effective = configured −
disabled` on read):

- The user can only **remove**; nothing outside the configured inventory can
  ever be enabled. New config entries default ON for everyone.
- System tool groups (`io`, `context`) are locked on and immune.
- Python groups and MCP servers toggle whole or per tool. Named-service realms
  toggle whole, per generic operation, or per named action; full denial removes
  the realm from roster and dispatch, while entry denial is enforced at named-
  service dispatch. Denied skills disappear from every consumer. If subagents
  are offered, denying them prevents the spawner, `react.delegate`, and its
  instruction guidance from being installed.
- The `model` field is a **pick, not a denial**: one choice from
  `supported_models`, applied for the user's turns to the strong decision role
  (`solver.react.v2.decision.v2.strong`), overriding what `role_models`
  configures for it. Absent pick — and any pick no longer in the list — runs
  the configured default.
- The whole layer FAILS OPEN: a missing row, a store error, or a stale entry
  yields the configured behavior, never a broken agent.

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
the user holds it. Each user keeps a standing per-class policy
(`cache_policy: {model_switch, capability_toggle}` in the same selection
record) with values `accept`, `confirm` (the platform default), `defer_cold`,
or `defer_conversation`; admin config supplies the default and the allowed set
(`config.react.<agent>.cache.selection_change_policy`). Under `confirm`, the
decision moment IS the policy picker: making a costly change in a warm
conversation opens an inline choice — Apply now · Apply from next conversation
· Apply when cache is cold — with "remember my choice" persisting the standing
policy. Deferred choices park the change as a **pending delta** the runtime
promotes when its trigger fires (a different conversation, or the warmness
signal reading cold — where applying is free); the menu badges pending changes
until then. At the runtime choke point, each conversation keeps the
last-APPLIED selection snapshot: a change that lands on a warm conversation
emits a **cold-turn marker** — an ANNOUNCE `[CACHE]` line for the agent plus
`cache_cold_turn` metadata on the decision call's accounting, so the
cache-rebuild premium is attributable as one identifiable component within the
turn's spend sum (a turn's cost is always the sum of the spendings inside it).
Everything fails open to the configured behavior.

## 6. How the chat component connects

The chat engine carries the agent identity and the selection UI end to end:

- `EngineConfig.agentId` (default `main`) rides every message target and event
  batch and scopes the selection operations. The current `conversation_id`
  scopes the effective model/capability selection — one chat instance drives
  one configured agent in one active conversation.
- The composer "+" menu is fed by `agent_capabilities` (lazy, on first open)
  and keeps toggles as a local draft. **Save changes** sends one scoped
  `agent_selection_update` merge-write; sending a chat message does not save
  the draft implicitly.
  Sections: Model (radio pick with the configured default tagged), Skills,
  Tools (two-level per-tool rows), MCP servers, Services (namespaces), plus a
  Connection-Hub entry that renders only when opening it can actually happen —
  a host that acks the `connection_hub.settings` surface command owns the
  open, and without an ack the served connections widget opens directly.
- Saved toggles apply on the next eligible turn under the user's cache policy:
  now, in the next conversation, or when the current cache is already cold.
  The backend reads the conversation row per turn, so there is no session
  invalidation. Switching conversations discards unsaved edits and loads that
  conversation's selection. A chat-originated Capabilities window carries the
  same conversation id. Only an independently mounted, unscoped widget edits
  the baseline used to seed future conversations.

The engine API detail (state branch, draft/save methods, switch-race handling) is owned by
[Chat Engine](../../../npm/components-core/chat-engine-README.md); the
end-to-end app wiring by the
[chat-with-react-agent recipe](../../../../recipes/components/chat-with-react-agent-README.md).
