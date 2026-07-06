---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
title: "How To Construct A ReAct Agent"
summary: "The full story of creating and customizing a ReAct agent from app code and config: the construction pipeline, the per-agent config surface, system instructions vs ANNOUNCE, the per-user selection layer (tools/skills/model), and the prompt-cache consequences of each customization."
status: current
tags: ["sdk", "agents", "react", "how-to", "configuration", "per-user-selection", "supported-models", "composer-menu"]
updated_at: 2026-07-06
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

A ReAct agent is assembled fresh for every turn from three inputs: **app code**
(the workflow that calls `build_react`), **app config** (the per-agent blocks in
`bundles.yaml`), and — since the per-user selection layer — **the user's own
saved choices**. This article walks the whole pipeline: how the instance is
constructed, which config keys shape it, where a customization belongs
(instructions vs ANNOUNCE), how users narrow it, and what each customization
costs in prompt cache.

## 1. The construction pipeline

The reference implementation is the versatile app's react node
(`examples/bundles/versatile@2026-03-31-13-36/agents/main.py`); every app
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
  │      → tools whose connected-account claims are unmet DROP for this turn;
  │        the facts flow to ANNOUNCE via runtime_ctx.inactive_tools
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

Steps 3 and 4 are the two per-turn narrowing passes. Both use the same pure
narrower (`runtime/agent_inventory.py`), both fail OPEN (any error keeps the
configured set), and both feed the ANNOUNCE surface rather than rewriting the
cached instruction text.

## 2. The per-agent config surface

Two config roots shape one agent, both resolved through the same agent-key
chain — the agent's own key first, then `default_agent`, then `default` — so
every setting can be declared per agent or once as the default:

**`config.react.<agent-key>` — runtime behavior and teaching:**

| Key | Shapes |
| --- | --- |
| `additional_instructions` | Durable teaching text appended to the system instruction. |
| `instructions` (body/blocks via `build_react` args `instruction_body` / `instruction_blocks`) | Full replacement/extension of the instruction composition when the app builds them in code. |
| `supported_models` | The admin-allowed model list users pick from (rows: `model`, `provider`, `label` — the economics price-table naming, so every allowed model is one the platform accounts for). |
| `role_models` | Per-agent role→model mapping (overrides the app-level `config.role_models` for this agent's runs). |
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
| Belongs here | Teaching: house style, domain rules, tool guidance, the named-service roster, skill galleries | State: `[BUDGET]`, `[RUNTIME LIMITS]`, `[CONTEXT CAPS]`, `[USER MEMORY HOTSET]`, `[WORKSPACE]`, `[INACTIVE TOOLS THIS TURN]`, temporal ground truth |
| App hook | `additional_instructions` / `instruction_body` / `instruction_blocks` on `build_react` | `RuntimeCtx` fields the announce composer reads (e.g. `memory_hotset`, `inactive_tools`) |

The placement rule is the **lifecycle test**: if the content can change between
turns without the admin changing config — connected accounts, per-turn limits,
live events, user toggles — it belongs in ANNOUNCE, because putting it in the
instruction text would rewrite (and thus invalidate) the cached slice the
moment it changes. If it is stable teaching the agent should always know, it
belongs in the instructions, where caching makes it nearly free after the
first turn. Section semantics and examples are owned by
[ReAct Announce](../react-announce-README.md); the caching mechanics by
[Context Caching](../context-caching-README.md).

A worked example of the rule: when a tool's connected-account claims are unmet,
`apply_delegated_tool_claims` drops the tool for the turn and publishes the
provider/tool facts on `runtime_ctx.inactive_tools` — rendered as
`[INACTIVE TOOLS THIS TURN]` in ANNOUNCE. The instruction text stays
byte-identical whether or not the account is connected.

## 4. The per-user selection layer

On top of the admin-granted inventory, each signed-in user can narrow what THE
agent uses for THEM. The selection is stored per (user, app id, agent) and
applied in step 3 of the pipeline.

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
    "named_services": {"task": true},
    "skills": ["public.docx-press"]
  },
  "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
}
```

Semantics, enforced at both ends (clamp on write, `effective = configured −
disabled` on read):

- The user can only **remove**; nothing outside the configured inventory can
  ever be enabled. New config entries default ON for everyone.
- System tool groups (`io`, `context`) are locked on and immune.
- Python groups toggle whole or per tool; MCP servers toggle whole or per tool
  (per-tool when the listing is knowable); named-service namespaces toggle
  whole — a denied namespace also vanishes from the agent's namespace roster
  and from operation dispatch for the turn; denied skills disappear from every
  skill consumer, and a skill whose required tool was denied auto-hides.
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
- **Toggling tools or skills invalidates the cached prompt slice.** The tool
  catalog and skill gallery render inside the system prompt, so adding or
  removing a tool group, an MCP server, a namespace, or a skill changes that
  text — the next turn re-writes the cache for everything from that point.
  Same model, so the cost is one cold turn, then caching resumes.
- **Turn-local state is free.** Everything routed through ANNOUNCE (including
  the inactive-tools notice and the memory hotset) changes nothing in the
  cached slice — that is precisely why the lifecycle rule in section 3 exists.

Two platform behaviors build on this: the chat surface will warn the user
before a cache-destroying switch (model change, tool/skill toggles), and a
configurable **cold-cache policy** — letting an operator bound how often users
may force cold turns — is the planned next enforcement step.

## 6. How the chat component connects

The chat engine carries the agent identity and the selection UI end to end:

- `EngineConfig.agentId` (default `main`) rides every message target and event
  batch and scopes the selection operations — one chat instance drives one
  configured agent.
- The composer "+" menu is fed by `agent_capabilities` (lazy, on first open)
  and writes toggles through debounced `agent_selection_update` merge-writes.
  Sections: Model (radio pick with the configured default tagged), Skills,
  Tools (two-level per-tool rows), MCP servers, Services (namespaces), plus a
  host-gated Connection-Hub entry.
- Toggles apply **from the next message**: the backend reads the saved
  selection per turn, so there is no session invalidation — the next turn is
  simply built from the updated selection (with the cache cost from section 5
  when the toggle touched the cached slice).

The engine API detail (state branch, methods, flush-on-send) is owned by
[Chat Engine](../../../npm/components-core/chat-engine-README.md); the
end-to-end app wiring by the
[chat-with-react-agent recipe](../../../../recipes/components/chat-with-react-agent-README.md).
