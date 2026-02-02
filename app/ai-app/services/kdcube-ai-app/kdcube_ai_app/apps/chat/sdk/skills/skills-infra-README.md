# Skills Infrastructure

This document explains how skills are configured, loaded, and made available in
both the ReAct runtime and isolated execution environments.

## Core components

- `SkillsSubsystem` (`kdcube_ai_app/apps/chat/sdk/skills/skills_registry.py`)
  - Owns the skills descriptor and resolves skills from:
    - built-in skills root: `kdcube_ai_app/apps/chat/sdk/skills/skills/`
    - optional custom skills root from the descriptor
  - Provides runtime helpers (gallery text, short ids, instruction blocks)
  - Exports a portable descriptor for isolated runtimes

## Skills descriptor

The descriptor is a JSON-serializable dict with:

```json
{
  "custom_skills_root": "/abs/or/bundle/relative/path",
  "agents_config": {
    "solver.react.decision": { "enabled": ["public.pptx-press"] },
    "answer.generator.strong": { "disabled": ["public.pdf-press"] }
  }
}
```

If `custom_skills_root` is relative, it is resolved against the bundle root.
If omitted and the bundle has `skills/` under its root, the bundle can pass that path.

## How it is wired

1) Bundle provides descriptor (plugin side)
   - Example: `bundle_root/skills_descriptor.py`
   - The orchestrator passes this descriptor into `SolverSystem`.

2) SolverSystem owns a `SkillsSubsystem`
   - `SolverSystem.__init__` builds `SkillsSubsystem(descriptor=..., bundle_root=...)`
   - The subsystem is stored on the solver instance and set active via context var.

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

## Notes

- Skills are injected into generators; they do not modify tool behavior.
- Filtering by `agents_config` is applied per consumer (e.g. `solver.react.decision`).
