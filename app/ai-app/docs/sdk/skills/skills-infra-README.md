---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-infra-README.md
title: "Skills Infra"
summary: "Infrastructure wiring for core SDK skills, SDK solution skills, and bundle-local skills across React runtime and isolated execution."
tags: ["sdk", "skills", "infra", "runtime", "react", "isolated", "solutions"]
keywords: ["skills registry", "skill descriptors", "solution skills", "react instructions", "iso runtime", "skills loader"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/custom-skills-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/README.md
---
# Skills Infrastructure

This document explains how skills are configured, loaded, and made available in
both the ReAct runtime and isolated execution environments.

Scope:
- This document focuses on wiring and runtime transport.
- For skill format and runtime behavior, see [docs/sdk/skills/skills-README.md](skills-README.md).
- For bundle-local authoring and AGENTS_CONFIG examples, see [docs/sdk/skills/custom-skills-README.md](custom-skills-README.md).

## Core components

- `SkillsSubsystem` (`kdcube_ai_app/apps/chat/sdk/skills/skills_registry.py`)
  - Owns the skills descriptor and resolves skills from:
    - core SDK skills root: `kdcube_ai_app/apps/chat/sdk/skills/skills/`
    - SDK solution skills roots, currently including
      `kdcube_ai_app/apps/chat/sdk/solutions/tasks/skills/`
    - optional bundle-local custom skills root from the descriptor
  - Provides runtime helpers (gallery text, short ids, instruction blocks)
  - Loads per-skill metadata from `tools.yaml` and `sources.yaml`
  - Applies `tools.yaml` `required: true` gates against the active tool catalog
    when a runtime context supplies tool availability
  - Applies `agent_disclosure: hidden` for skills that are loadable but must
    not appear in user-facing skill catalogs or self-descriptions
  - Exports a portable descriptor for isolated runtimes

Discovery order is core SDK skills, SDK solution skills, then bundle-local
skills. The registry is last-one-wins by fully qualified skill id, so a bundle
can intentionally override an SDK skill by publishing the same id.

## Skills descriptor

The descriptor is a JSON-serializable dict with:

```json
{
  "custom_skills_root": "/abs/or/bundle/relative/path",
  "agents_config": {
    "solver.react.v2.decision.v2.strong": { "enabled": ["public.pptx-press"] },
    "answer.generator.strong": { "disabled": ["public.pdf-press"] }
  }
}
```

If `custom_skills_root` is relative, it is resolved against the bundle root.
If omitted and the bundle has `skills/` under its root, the bundle can pass that path.
SDK solution skill roots are not configured by the bundle descriptor; they are
part of the SDK registry wiring and are filtered with the same `agents_config`
rules as other skills.

Use `agents_config` to make skills unavailable to a consumer. Use
`agent_disclosure: hidden` in a skill's front matter only when the skill should
remain loadable by exact id/import but must be omitted from visible catalogs.
Hidden-disclosure skills are excluded from `SK1`, `SK2`, ... short-id mapping;
if explicitly loaded, their active instruction block uses a redacted heading and
a non-disclosure guard.

Use `tools.yaml` `required: true` when the skill depends on tools that may be
omitted for a specific bundle, agent, user policy, or `allowed_plugins` set. In
ReAct, the decision prompt builds the active tool catalog and passes it to the
skill registry; skills missing required tools are skipped from catalogs,
imports, short ids, and skill reads for that runtime context.

## How it is wired

1) Bundle provides descriptor (plugin side)
   - Example: `bundle_root/skills_descriptor.py`
   - The orchestrator passes this descriptor into `SolverSystem`.

2) SolverSystem owns a `SkillsSubsystem`
   - `SolverSystem.__init__` builds `SkillsSubsystem(descriptor=..., bundle_root=...)`
   - The subsystem is stored on the solver instance and set active via context var.
   - The subsystem discovers core SDK skills, SDK solution skills, and the
     bundle custom root.

3) ReAct session activation
   - In `ReactSolver.prepare_session`, the skills subsystem is set as the active
     subsystem for the turn (context var).
   - Decision and generator code resolve skills via the active subsystem.

4) Isolated execution (exec/codegen)
   - Runtime globals include `SKILLS_DESCRIPTOR` from the active subsystem.
   - `py_code_exec_entry.py` reads it and calls `set_skills_descriptor(...)`,
     recreating a `SkillsSubsystem` in the isolated process.
   - This works for both local subprocess isolation and Docker mode.

## Access patterns

- Host process:
  - `get_active_skills_subsystem()` to resolve skills
  - `build_skills_instruction_block(...)` for generator system prompts
- Isolated runtime:
  - `set_skills_descriptor(...)` from `SKILLS_DESCRIPTOR` in runtime globals

## Sources and citations

- Skills can ship `sources.yaml` alongside `SKILL.md`.
- When a skill is read via `react.read`, its sources are merged into the turn
  sources pool and any `[[S:...]]` tokens in the skill body are rewritten to the
  merged pool SIDs.

## Notes

- Skills are injected into generators; they do not modify tool behavior.
- Filtering by `agents_config` is applied per consumer (e.g. `solver.react.v2.decision.v2.strong`).
- Required-tool filtering is runtime-context-sensitive and only applies when a
  caller supplies the active tool catalog. Without a tool catalog, skills remain
  backward-compatible and are not filtered by tool refs.
- `agent_disclosure: hidden` is prompt-disclosure control only, not an
  authorization boundary.
- SDK solution skills behave like built-in skills for discovery and short-id
  mapping, but bundles must still enable the corresponding tools if the skill
  instructs the agent to call them.
