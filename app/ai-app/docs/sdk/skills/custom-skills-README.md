---
id: ks:docs/sdk/skills/custom-skills-README.md
title: "Custom Skills"
summary: "Bundle-local skills guide: descriptor setup, AGENTS_CONFIG visibility filters, and where consumer-scoped filtering is enforced."
tags: ["sdk", "skills", "custom", "descriptor", "agents_config", "react", "llm-generator", "visibility", "control"]
keywords: ["skills_descriptor.py", "CUSTOM_SKILLS_ROOT", "AGENTS_CONFIG", "consumer id", "solver.react.decision.v2", "react.read", "SKx", "llm_generator.py", "skill catalog", "skill visibility", "skill visibility control", "concerns separation"]
see_also:
  - ks:docs/sdk/skills/skills-README.md
  - ks:docs/sdk/skills/skills-infra-README.md
  - ks:docs/sdk/agents/react/source-pool-README.md
---
# Custom Skills (Bundle‑Local)

This is a quick guide to defining **bundle‑local skills**.

For full details, see:
- `docs/sdk/skills/skills-README.md`
- `docs/sdk/skills/skills-infra-README.md`

---

## Minimal pattern

1. Create a skills folder in your bundle.
2. Add a `skills_descriptor.py` to register skills.

Example bundle:
- `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`

Files:
- `skills/product`
- `skills_descriptor.py`

---

## What a skill can include

Inside a skill folder you can include:
- `SKILL.md` (required) with front‑matter fields:
  - `name`, `id`, `description`, `version`, `namespace`, `tags`, `when_to_use`, `imports`
- `compact.md` (optional): compact instruction variant
- `sources.yaml` (optional): sources injected into `sources_pool` when the skill is loaded
- `tools.yaml` (optional): recommended tools for the skill

Both `SKILL.md` and `skill.yml` are supported.

---

## Descriptor example

```python
# skills_descriptor.py
import pathlib

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# Bundle-local skills root (expected layout: <root>/<namespace>/<skill_id>/SKILL.md)
CUSTOM_SKILLS_ROOT = BUNDLE_ROOT / "skills"

AGENTS_CONFIG = {
    # Exact consumer id match
    "solver.react.decision.v2": {
        # Explicit allow-list (fully qualified skill ids)
        "enabled": [
            "product.kdcube",
        ]
    },

    # Agent-specific overrides (use the agent id)
    "answer.generator.strong": {
        # Example: disable a namespace for this agent only
        "disabled": ["public.*"],
    },
}
```

### Important: `CUSTOM_SKILLS_ROOT = None` does not currently disable bundle-local skills

Today, the runtime auto-detects `<bundle_root>/skills` when `custom_skills_root` is
missing/falsy. That means setting `CUSTOM_SKILLS_ROOT = None` in
`skills_descriptor.py` does **not** reliably switch off bundle-local skills.

Practical options today:
- Remove or rename the bundle `skills/` folder.
- Set `CUSTOM_SKILLS_ROOT` to a truthy non-existent path (prevents auto-discovery fallback).
- Use `AGENTS_CONFIG` to hide skills from specific agents (visibility filter, not registry disable).

---

## Sources + tools metadata

Skills can include:
- `sources.yaml` — sources added to the **sources_pool** when the skill is loaded (via `react.read`).
- `tools.yaml` — tools recommended for this skill (used by planners/UX).

When a skill is loaded with `react.read("sk:<skill>")`, its sources are merged into
`sources_pool` and can be referenced as `so:sources_pool[...]`. Citation tokens inside
skill text are rewritten to match the merged source IDs.

---

## AGENTS_CONFIG visibility filtering (per-agent)

`AGENTS_CONFIG` controls skill visibility by consumer id (agent role). This is
not a global disable of the skills registry.

Rules:
- Match is exact by consumer id (example: `solver.react.decision.v2`).
- Missing consumer entry means no filter for that consumer.
- `enabled` means allow-list.
- `disabled` means deny-list.
- Wildcards are supported (`public.*`, `public.docx-*`, `*`).
- `"default"` key is not special in current runtime.
- `enabled: []` does not disable all.

Quick disable patterns:
- One skill: `disabled: ["product.kdcube"]`
- Namespace: `disabled: ["public.*"]`
- All for one consumer: `disabled: ["*"]`

Resolution example:

```python
AGENTS_CONFIG = {
    "solver.react.decision.v2": {"disabled": ["public.*"]},
    "answer.generator.strong": {"enabled": ["product.kdcube", "public.docx-press"]},
}
```

With registry:
- `product.kdcube`
- `public.docx-press`
- `public.pdf-press`
- `custom.ops-runbook`

Result:
- `consumer="solver.react.decision.v2"` -> `product.kdcube`, `custom.ops-runbook`
- `consumer="answer.generator.strong"` -> `product.kdcube`, `public.docx-press`
- `consumer="answer.generator.regular"` -> all skills

Where this is applied:
- ReAct decision catalog (`consumer="solver.react.decision.v2"`).
- `react.read` skill resolution (`SKx` / `sk:<id>`) via short-id map.
- Generator role consumers (example `answer.generator.strong`) when tools
  resolve/inject skills.

For exact code paths and full runtime flow, see:
- `docs/sdk/skills/skills-README.md` (section: runtime usage and enforcement)
- `docs/sdk/skills/skills-infra-README.md` (runtime wiring)

---

## References (code)

- Example bundle: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`
- Descriptor: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44/skills_descriptor.py`
- Example skill: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44/skills/product`
