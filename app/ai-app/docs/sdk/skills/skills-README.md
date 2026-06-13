---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-README.md
title: "Skills"
summary: "Skills subsystem reference: format, discovery roots, per-consumer visibility filtering, and exact runtime enforcement paths."
tags: ["sdk", "skills", "subsystem", "runtime", "configuration", "agents_config", "react", "codegen", "citations", "solutions"]
keywords: ["skills_registry.py", "surfaces.as_consumer.agents.<agent>.skills", "custom_root", "consumers", "consumer id", "solution skills", "skills_for_consumer", "build_skill_short_id_map", "import_skillset", "build_skills_instruction_block", "react.read", "llm_generator.py", "codegen_tools.py"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/custom-skills-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-infra-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
---
# Skills Subsystem

This document describes how skills are defined, discovered, selected, and injected into generators.

We are compatible with the Anthropic skills format described here:
https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

Our SKILL.md frontmatter follows a compatible structure (name/description/version/category/tags/author/created) and then
the skill instruction body. We add extra fields (namespace, when_to_use, import/imports) to support routing and composition.


## What is a skill?

A skill is a reusable instruction bundle that improves generation quality for a specific format or task. It is not a tool.
Instead, it is injected into a generator's system instruction at the time the generator is called.

Examples:
- pdf-press: HTML/PDF layout rules for write_pdf.
- url-gen: strict rules for link sourcing and citations.

Skills are only applied to generators (react decision, codegen, or llm content generation). They do not change tool behavior.


## Skill Storage Layout

Core SDK skills live under:
  kdcube_ai_app/apps/chat/sdk/skills/skills/

SDK solution packages may also publish built-in skills under their own package
tree. Example:
  kdcube_ai_app/apps/chat/sdk/solutions/tasks/skills/

Bundle-local skills live under the bundle custom root, usually:
  <bundle_root>/skills/

Folder structure:
  skills/
    public/
      <skill_id>/
        SKILL.md
        compact.md            (optional, not used by default)
        tools.yaml
        sources.yaml          (optional)
    internal/
      <skill_id>/
        SKILL.md
        compact.md
        tools.yaml
        sources.yaml
  solutions/tasks/skills/
    task/
      tasks/
        SKILL.md
        tools.yaml
      job/
        SKILL.md
        tools.yaml

  <bundle_root>/skills/
    <namespace>/
      <skill_id>/
        SKILL.md
        compact.md            (optional)
        tools.yaml            (optional)
        sources.yaml          (optional)

Each skill folder contains:
- SKILL.md: metadata + full instruction body
- compact.md: optional compact instruction (not used by default)
- tools.yaml: tool ids and why they matter for this skill
- sources.yaml: canonical sources for citations used inside SKILL.md


## SKILL.md structure

SKILL.md uses YAML frontmatter, then the instruction body.

Example:

---
name: pdf-press
description: |
  Teaches agents how to generate HTML and Markdown that renders to PDF
  with proper layout, pagination, and professional styling.
version: 1.0.0
category: document-creation
tags:
  - pdf
  - html
  - css
author: kdcube
created: 2026-01-16
namespace: public
when_to_use:
  - Creating technical reports with multiple pages
  - Using write_pdf for HTML rendering
import:
  - internal.link-evidence
  - internal.sources-section
---

# PDF Authoring
... full instruction text ...

Notes:
- namespace defaults to "public" if omitted.
- import/imports can include other skills (public/internal/custom).
- when_to_use is used in the skill catalog display.
- agent_disclosure defaults to "normal"; set `agent_disclosure: hidden` for
  operational guidance that may be loaded by exact id but must not appear in
  skill catalogs or user-facing self-descriptions.
- Tool eligibility is declared in `tools.yaml`, not in `SKILL.md`.


## tools.yaml

tools.yaml is metadata for the skill and lists which tools are relevant and why.

Example:

tools:
  - id: generic_tools.write_pdf
    role: document rendering
    why: Renders HTML/Markdown/Mermaid to PDF.
  - id: llm_tools.generate_content_llm
    role: content generation
    why: Produces HTML/Markdown with correct layout and citations.
  - id: memory.search_memory
    role: durable memory read
    why: Read durable user memory for this guidance.
    required: true

`required: true` means the skill is eligible only when that tool id is present
in the active tool catalog for the current agent/turn. Without `required: true`,
the tool entry is advisory metadata for planners and UX only. This keeps old
skills backward-compatible while allowing stateful or subsystem-specific skills
to disappear automatically when their tools are not wired, disabled, or excluded
from `allowed_plugins` / `allowed_ids`.


## sources.yaml

sources.yaml defines canonical sources referenced inside the skill body.
Use this when SKILL.md includes links or factual claims that should resolve to
the system sources pool.

Format (canonical shape):

sources:
  - sid: 1
    url: https://example.com/
    title: Example
    text: Short summary

Authoring rules:
- In SKILL.md, add `[[S:<sid>]]` right after links or claims that use a source.
- Ranges are supported: `[[S:1-3]]` and comma lists `[[S:1,3]]`.
- When a skill is loaded via `react.read`, its sources are merged into the turn
  sources pool; SIDs may be remapped and citation tokens are rewritten to match
  the merged pool.

Optional fields supported by the canonical sources pool are preserved when
loading from `sources.yaml`. Common examples:
- `published_time_iso`, `modified_time_iso`, `fetched_time_iso`
- `author`, `authority`, `provider_rank`, `weighted_rank`
- `source_type`, `mime`, `size_bytes`
- `favicon_url` or `favicon` (for UI)

Notes:
- URLs are normalized on load.
- `local_path` is normalized to `physical_path`.
- Large `base64` values may be trimmed for safety.
- See [docs/citations-system.md](../../citations-system.md) for the full canonical sources shape.


## Namespaces

Namespaces control discoverability:
- public: visible in skill catalogs
- internal: not discoverable (used for internal behaviors)
- task: SDK tasks solution skills
- custom/product/etc.: loaded from a bundle-defined root (see `surfaces.as_consumer.agents.<agent>.skills`)

Skills are referenced by fully qualified id:
  <namespace>.<skill_id>

For convenience, callers may also use:
- skills.<namespace>.<skill_id>
- <namespace>.<skill_id>
- <skill_id> (defaults to public.<skill_id>)
- short ids (SK1, SK2, ...) from the skill catalog


## Skill Configuration

Bundles configure skills under the consuming agent, alongside tools:

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
                - public.url-gen
            answer.generator.strong:
              disabled:
                - public.pdf-press
                - public.*-press
```

It defines:
- `custom_root`: optional path for bundle-local skills
- `consumers`: per-skill-consumer filtering, with `enabled` and `disabled` lists

The SDK resolver converts this bundle-facing config into the internal
`SkillsSubsystem` payload:

```json
{
  "custom_skills_root": "/abs/bundle/skills",
  "agents_config": {
    "solver.react.v2.decision.v2.strong": {
      "enabled": ["public.url-gen"]
    }
  }
}
```

Filtering logic:
- If a consumer is present in `consumers`:
  - enabled list means only those skills are visible.
  - otherwise, disabled list removes those skills.
- You can use wildcard patterns like `public.*` or `public.*-press`.
- If a consumer is not present in `consumers`:
  - all skills are visible (no include_for enforcement).
- Matching is by exact consumer id.
- After visibility filtering, runtime tool eligibility is applied for skills
  whose `tools.yaml` contains `required: true` entries. If any required tool is
  missing from the active tool catalog, the skill is omitted from the visible
  catalog, short-id mapping, imports, and `react.read(sk:...)` load path for
  that runtime context.
- `"default"` key is not interpreted specially by the current runtime.
- To disable all skills for one consumer, set `disabled: ["*"]`.
- `enabled: []` does not disable all; it behaves like no enabled filter.
- Filtering is applied only when skill APIs are called with `consumer=<agent id>` and that id matches exactly.
- Solution skills can use `tools.yaml` `required: true` gates. The Tasks
  solution does this, so `task.*` skills are omitted automatically when task
  tools are absent. Use `disabled: ["task.*"]` only when policy should deny
  those skills even though the tools are available.

Resolution example:

consumers = {
  "solver.react.v2.decision.v2.strong": {"disabled": ["public.*"]},
  "answer.generator.strong": {"enabled": ["product.kdcube", "public.docx-press"]}
}

If registry contains:
- product.kdcube
- public.docx-press
- public.pdf-press
- custom.ops-runbook

Then:
- consumer=solver.react.v2.decision.v2.strong -> product.kdcube, custom.ops-runbook
- consumer=answer.generator.strong -> product.kdcube, public.docx-press
- consumer=answer.generator.regular -> all skills (no matching entry)

What this configuration achieves:
- Per-agent planning control: decide which skills appear in each agent catalog.
- Per-agent resolution control: SK short ids are built from each consumer's visible set.
- Per-agent instruction injection control: hidden skills for a consumer are not injected into that consumer's generator prompt.

`agent_disclosure: hidden` is separate from `consumers` visibility:
- `consumers` says whether a consumer may see/load a skill at all.
- `agent_disclosure: hidden` says an otherwise available skill must not be
  advertised. Hidden-disclosure skills are omitted from the catalog and short-id
  map, but can still be loaded by exact id or by imports.
- When a hidden-disclosure skill is loaded, the active instruction block uses a
  redacted heading and a non-disclosure rule instead of printing the skill name
  and fully qualified id.
- This is not an authorization boundary. Disable the skill in the configured
  consumer visibility rules
  when the agent must not be able to use it.

SDK mechanisms using this:
- ReAct decision catalog: `consumer="solver.react.v2.decision.v2.strong"` (planning catalog visibility).
- `react.read` short-id resolution: `consumer="solver.react.v2.decision.v2.strong"` (which SKx ids can be loaded).
- Generator role-based resolution/injection: `consumer=<role>` such as `answer.generator.strong`.

For authoring examples, see [docs/sdk/skills/custom-skills-README.md](custom-skills-README.md).
For runtime wiring, see [docs/sdk/skills/skills-infra-README.md](skills-infra-README.md).


## Skill discovery and selection

1) Skills are loaded from:
   - core SDK skills directory: `sdk/skills/skills`
   - SDK solution skill roots, for example `sdk/solutions/tasks/skills`
   - optional bundle `custom_root`

   Load order matters. Bundle-local skills are loaded last, so a bundle can
   override an SDK skill by publishing the same fully qualified skill id.

2) Skill catalogs are shown to decision and generator flows in system instruction.

3) The decision agent selects skills by short id (SK1, SK2, ...).

4) Skills are resolved and expanded with imports.
   The resolver:
   - de-duplicates
   - handles overlapping imports
   - avoids cycles

5) The resolved skill set is materialized into an [ACTIVE SKILLS] block and
   injected into the generator’s system instruction.

Important: skills are applied only to generators (decision, codegen, llm).
They are not passed to tools unless the tool itself is a generator.
The final answer generator is a generator and can receive skills (e.g., formatting or marketing guidance).

## Runtime usage and enforcement (exact code paths)

Consumer filtering core (`kdcube_ai_app/apps/chat/sdk/skills/skills_registry.py`):

```python
roots = [
    BUILTIN_SKILLS_ROOT / "skills",
    *SOLUTION_SKILLS_ROOTS,
    custom_skills_root,
]
```

```python
cfg = agents_config.get(consumer) or {}
enabled = cfg.get("enabled")
disabled = cfg.get("disabled")

...
if enabled_set is not None:
    # keep only enabled matches
    ...
if disabled_set is not None:
    # remove disabled matches
    ...
```

ReAct decision catalog usage:

```python
# kdcube_ai_app/apps/chat/sdk/solutions/react/v2/agents/decision.py
tool_block = build_instruction_catalog_block(
    consumer="solver.react.v2.decision.v2.strong",
    tool_catalog=tool_catalog,
    react_tools=get_react_tools_catalog(),
    include_skill_gallery=True,
)

# kdcube_ai_app/apps/chat/sdk/solutions/react/layout.py
skill_block = skills_gallery_text(
    consumer=consumer,
    tool_catalog=tools_list,
)
```

`react.read` resolution usage:

```python
# kdcube_ai_app/apps/chat/sdk/solutions/react/tools/read.py
short_map = build_skill_short_id_map(consumer="solver.react.v2.decision.v2.strong")
skill_ids = import_skillset(normalized_skills, short_id_map=short_map)
```

Tool-level resolution/injection pattern (example):

```python
# kdcube_ai_app/apps/chat/sdk/tools/codegen_tools.py
short_map = build_skill_short_id_map(consumer="solver.react.decision")
normalized_skills = import_skillset(skills_list, short_id_map=short_map)
```

LLM generator role selection:

```python
# kdcube_ai_app/apps/chat/sdk/tools/llm_generator.py
results, meta = await stream_with_channels(
    ...,
    role=("answer.generator.strong" if model_strength == "strong" else "answer.generator.regular"),
    ...
)
```

Active-skills block construction:

```python
# kdcube_ai_app/apps/chat/sdk/skills/skills_registry.py
normalized = import_skillset(skill_ids)
skills_block = build_skills_instruction_block(
    normalized,
    variant="full",
    header="ACTIVE SKILLS",
)
```


## When skills are applied

- React decision (planner + generator):
  - show_skills triggers [ACTIVE SKILLS] in the next decision round so the decision agent can both plan better and generate content/code directly when it chooses to do so.
- LLM tool (generate_content_llm):
  - passes resolved skill set into system instruction.
- Codegen tool:
  - passes resolved skill set into system instruction for the codegen agent.


## Import expansion (unique skill set)

Skills can import other skills. The resolver builds the unique transitive
closure and avoids duplicates or cycles.

Callers should use:
  import_skillset(skill_ids, short_id_map=...)


## Diagram (skills in the ecosystem)

          +--------------------+
          |  Skills Registry   |
          |  (SKILL.md, tools) |
          +---------+----------+
                    |
                    v
          +---------------------+        +--------------------+
          |  Skill Catalogs     |        |  agent skill config|
          |  (system prompt)    |        |  + consumers       |
          +----------+----------+        +---------+----------+
                     \                          /
                      \                        /
                       v                      v
                 +-------------------------------+
                 |  Decision / Generators        |
                 |  choose SK ids (SK1, SK2)     |
                 +---------------+---------------+
                                 |
                                 v
                   +---------------------------+
                   | import_skillset resolver |
                   | (imports, dedupe, cycle) |
                   +---------------+-----------+
                                   |
                                   v
                   +---------------------------+
                   | [ACTIVE SKILLS] injected |
                   | into generator system    |
                   +---------------+-----------+
                                   |
                +------------------+-------------------+
                |                                      |
        +---------------+                      +---------------+
        | LLM generator |                      | Codegen agent |
        | (tool.gen.*)  |                      | (codegen)     |
        +---------------+                      +---------------+
