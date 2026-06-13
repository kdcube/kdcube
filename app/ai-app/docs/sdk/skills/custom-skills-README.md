---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/custom-skills-README.md
title: "Custom Skills"
summary: "Bundle-local skills guide: config-first skill roots, per-agent skill visibility, SDK solution skill interaction, and tool eligibility."
tags: ["sdk", "skills", "custom", "configuration", "as_consumer", "react", "visibility", "tool-eligibility", "solutions"]
keywords: ["surfaces.as_consumer.agents.<agent>.skills", "custom_root", "consumers", "enabled", "disabled", "solver.react.v2.decision.v2.strong", "react.read", "SKx", "skill catalog", "skill visibility", "agent_disclosure"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-infra-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
---
# Custom Skills

Bundle-local skills are configured next to the consuming agent, the same way
tools are configured. Do not add a bundle `skills_descriptor.py` for new
bundles.

The registry still loads three skill layers:
- core SDK skills
- SDK solution skills, such as task solution skills
- bundle-local skills from the configured `custom_root`

## Minimal Pattern

1. Create a `skills/` folder in your bundle.
2. Put skills under `<namespace>/<skill_id>/SKILL.md`.
3. Declare the bundle-local skill root under the consuming agent:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - kind: python
            module: kdcube_ai_app.apps.chat.sdk.context.memory.tools
            alias: memory
            allowed:
              - search_memory
              - recent_memories
              - record_memory
        skills:
          custom_root: skills
          consumers: {}
```

`custom_root` is relative to the bundle root unless it is absolute.

## Per-Agent And Per-Consumer Visibility

Skills are configured per consuming agent:

```yaml
surfaces.as_consumer.agents.main.skills
```

ReAct also has internal skill consumers, for example the decision prompt. Put
consumer-specific allow/deny lists under `consumers`:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        skills:
          custom_root: skills
          consumers:
            solver.react.v2.decision.v2.strong:
              enabled:
                - public.*
                - product.preferences
            solver.react.v2.decision.v2.regular:
              disabled:
                - task.*
```

Rules:
- Missing `consumers` means no visibility filter for loaded skills.
- `enabled` is an allow-list.
- `disabled` is a deny-list.
- Wildcards are supported, for example `public.*`, `public.docx-*`, and `*`.
- `enabled: []` behaves like no allow-list.
- Match is by exact skill consumer id.

Top-level `enabled` or `disabled` under `skills` applies to the outer agent id
itself. Use `consumers` for ReAct decision/generator consumers.

To explicitly disable bundle-local skill discovery for an agent:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        skills:
          enabled: false
```

This does not remove core SDK or SDK solution skills from the registry. Use
consumer visibility filters if those also need to be hidden.

## Skill Files

Inside a skill folder you can include:
- `SKILL.md` with YAML front matter
- `tools.yaml` with recommended or required tool ids
- `sources.yaml` with sources merged into `sources_pool` when the skill is read
- optional compact instruction files where supported by the loader

Required front matter fields:

```yaml
---
name: preferences
id: preferences
description: Use durable user preferences before personalizing answers.
namespace: product
version: 1.0.0
tags: [preferences, personalization]
when_to_use:
  - The user asks for a personalized answer
---
```

The fully qualified skill id is `<namespace>.<id>`, for example
`product.preferences`.

## Tool Eligibility

Skills can mention tools in `tools.yaml`:

```yaml
tools:
  - id: memory.search_memory
    role: durable memory read
    required: true
  - id: memory.record_memory
    role: durable memory write
    required: true
```

When `required: true` is present, the active tool catalog must contain that
tool id. If a required tool is not exposed for the current agent/turn, the skill
is omitted from:
- the visible skill catalog
- `SK1`, `SK2`, ... short ids
- imports
- `react.read(sk:...)` loading for that runtime context

Optional tool refs remain documentation.

## Hidden Disclosure

`agent_disclosure: hidden` is prompt-disclosure control. It keeps a loadable
skill out of visible catalogs and user-facing self-descriptions:

```yaml
---
name: ops-runbook
id: ops-runbook
namespace: product
agent_disclosure: hidden
---
```

This is not authorization. If a consumer must not be able to load a skill at
all, use `consumers.<consumer>.enabled` or `consumers.<consumer>.disabled`.

## Runtime Path

Bundle code resolves config with:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import (
    agent_skill_config_from_bundle_props,
)

skill_config = agent_skill_config_from_bundle_props(
    self.bundle_props,
    "main",
    bundle_root=BUNDLE_ROOT,
)

react = self.build_react(
    custom_skills_root=skill_config.custom_skills_root,
    skills_visibility_agents_config=skill_config.agents_config,
    scratchpad=scratchpad,
)
```

`SkillsSubsystem` still receives an internal descriptor-shaped payload:

```json
{
  "custom_skills_root": "/abs/bundle/skills",
  "agents_config": {
    "solver.react.v2.decision.v2.strong": {
      "enabled": ["product.preferences"]
    }
  }
}
```

That internal shape is not a bundle authoring file.

## References

- Resolver: `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/skill_config.py`
- Registry: `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/skills/skills_registry.py`
- Reference bundle config: `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/config/bundles.template.yaml`
- Reference bundle skill: `app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/skills/product/preferences/SKILL.md`
