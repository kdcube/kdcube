---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/components/chat-with-react-agent-README.md
title: "Recipe: Chat With A ReAct Agent"
summary: "End-to-end steps: declare a ReAct agent with per-agent config (tools/skills inventory, instructions, supported_models), wire the chat component to it, and let users customize the agent through the composer menu."
status: current
tags: ["recipes", "components", "chat", "react", "agent", "supported-models", "composer-menu"]
updated_at: 2026-07-06
keywords:
  [
    "chat with react agent",
    "agent inventory config",
    "supported_models recipe",
    "composer plus menu",
    "agent_capabilities",
    "per-user selection",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/chat-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
---
# Recipe: Chat With A ReAct Agent

Steps to ship a chat surface backed by an app-declared ReAct agent that users
can customize. This recipe is the wiring; the model behind every step — the
construction pipeline, selection semantics, and cache costs — is owned by
[How To Construct A ReAct Agent](../../sdk/agents/react/how/how-to-construct-react-agent-README.md).
Mounting the chat COMPONENT itself (scene boundary, context drag, event
profile) is the [Chat Widget recipe](./chat-README.md).

## 1. Declare the agent's inventory (what the admin grants)

`config/bundles.template.yaml`, under the app's `config:`:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - name: io                       # system group — always on
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.io_tools
            alias: io_tools
            allowed: [tool_call]
          - name: context                  # system group — always on
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.ctx_tools
            alias: ctx_tools
            allowed: [merge_sources, fetch_ctx]
          - name: web
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
            alias: web_tools
            allowed: [web_search, web_fetch]
          - name: knowledge                # MCP server, whole-catalog allow
            kind: mcp
            server_id: knowledge
            alias: knowledge
            allowed: ["*"]
          - name: services                 # named-service namespaces
            kind: named_service
            alias: named_services
            namespaces:
              mem:
                allowed: [provider.about, object.list, object.search]
        skills:
          custom_root: skills
          consumers: {}
```

## 2. Declare the agent's react block (behavior + the model list)

```yaml
react:
  default_agent:                # or the agent key, e.g. main
    max_iterations: 15
    additional_instructions: |
      [HOUSE STYLE]
      Cite sources as browsable URLs.
    supported_models:           # omit to keep the model choice invisible
      - model: claude-sonnet-4-6
        provider: anthropic
        label: Sonnet 4.6
      - model: claude-haiku-4-5-20251001
        provider: anthropic
        label: Haiku 4.5
role_models:
  solver.react.v2.decision.v2.strong:
    provider: anthropic
    model: claude-sonnet-4-6    # the configured default the picker tags
```

Copy `supported_models` rows from the deployment's economics price table so
every allowed model is one the platform accounts for.

## 3. Build the agent in the workflow

```python
tool_config = agent_tool_config_from_bundle_props(self.bundle_props, client_id, bundle_root=BUNDLE_ROOT)
skill_config = agent_skill_config_from_bundle_props(self.bundle_props, client_id, bundle_root=BUNDLE_ROOT)
# Per-user selection: deny-list narrowing + the model pick (fail-open).
tool_config, skill_config = await self.apply_user_agent_selection(tool_config, skill_config)
# Connected-account claims: unmet-claim tools drop for this turn (ANNOUNCE informs).
tool_config = await self.apply_delegated_tool_claims(tool_config)

react = self.build_react(
    mod_tools_spec=tool_config.tool_specs,
    mcp_tools_spec=tool_config.mcp_tool_specs,
    tools_runtime=tool_config.tool_runtime,
    tool_traits=tool_config.tool_traits,
    custom_skills_root=skill_config.custom_skills_root,
    skills_visibility_agents_config=skill_config.agents_config,
    additional_instructions=additional_instructions,
    scratchpad=scratchpad,
)
sr = await react.run(
    allowed_plugins=tool_config.allowed_plugins,
    allowed_tool_names_by_alias=tool_config.allowed_tool_names_by_alias,
)
```

The two selection operations (`agent_capabilities`, `agent_selection_update`)
already ship on the SDK entrypoint base — no app code needed for them.

## 4. Wire the chat component to the agent

```ts
const engine = createChatEngine({
  connection: { baseUrl, tenant, project, bundleId },
  agentId: 'main',        // the agent key from step 1; default 'main'
})
```

The packaged chat UI (`@kdcube/components-react/chat` `<Chat/>`, or the SDK
chat widget) renders the composer "+" menu automatically for signed-in users.

## 5. What the user now sees

- The "+" menu lists Model (when step 2 declared `supported_models`, with the
  configured default tagged), Skills, Tools with per-tool rows, MCP servers,
  and Services — everything from step 1's inventory, system groups excluded
  from toggling.
- Toggles and the model pick save as the user clicks and apply from the next
  message.
- Verify end to end: toggle a tool group off, send a message, and confirm the
  agent's tool catalog for that turn excludes the group (the selection is also
  logged as `[agent_selection.applied]` in the app logs).
