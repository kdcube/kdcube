---
id: ks:docs/sdk/skills/skills-README.md
title: "Skills"
summary: "Skills subsystem reference: format, discovery, per-consumer visibility filtering, and exact runtime enforcement paths."
tags: ["sdk", "skills", "subsystem", "runtime", "descriptor", "agents_config", "react", "codegen", "citations"]
keywords: ["skills_registry.py", "skills_descriptor.py", "AGENTS_CONFIG", "consumer id", "skills_for_consumer", "build_skill_short_id_map", "import_skillset", "build_skills_instruction_block", "react.read", "llm_generator.py", "codegen_tools.py"]
see_also:
  - ks:docs/sdk/skills/custom-skills-README.md
  - ks:docs/sdk/skills/skills-infra-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
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


## Skill storage layout

Skills live under:
  kdcube_ai_app/apps/chat/sdk/skills/skills/

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
    custom/
      <skill_id>/
        SKILL.md
        compact.md
        tools.yaml
        sources.yaml

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
- See `docs/citations-system.md` for the full canonical sources shape.


## Namespaces

Namespaces control discoverability:
- public: visible in skill catalogs
- internal: not discoverable (used for internal behaviors)
- custom: loaded from a bundle-defined root (see SkillsSubsystem descriptor)

Skills are referenced by fully qualified id:
  <namespace>.<skill_id>

For convenience, callers may also use:
- skills.<namespace>.<skill_id>
- <namespace>.<skill_id>
- <skill_id> (defaults to public.<skill_id>)
- short ids (SK1, SK2, ...) from the skill catalog


## Skills descriptor (bundle plugin)

Each bundle can provide a skills descriptor (analogous to tools_descriptor for tools).
It defines:
- custom_skills_root: optional path for custom namespace skills
- agents_config: per-consumer filtering (enabled/disabled lists)

Example:

custom_skills_root = "/opt/custom_skills"

agents_config = {
  "solver.react.decision": {
    "enabled": ["public.url-gen"]
  },
  "answer.generator.strong": {
    "disabled": ["public.pdf-press", "public.*-press"]
  }
}

Filtering logic:
- If a consumer is present in AGENTS_CONFIG:
  - enabled list means only those skills are visible.
  - otherwise, disabled list removes those skills.
- You can use wildcard patterns like `public.*` or `public.*-press`.
- If a consumer is not present in AGENTS_CONFIG:
  - all skills are visible (no include_for enforcement).
- Matching is by exact consumer id.
- `"default"` key is not interpreted specially by the current runtime.
- To disable all skills for one consumer, set `disabled: ["*"]`.
- `enabled: []` does not disable all; it behaves like no enabled filter.
- Filtering is applied only when skill APIs are called with `consumer=<agent id>` and that id matches exactly.

Resolution example:

agents_config = {
  "solver.react.decision.v2": {"disabled": ["public.*"]},
  "answer.generator.strong": {"enabled": ["product.kdcube", "public.docx-press"]}
}

If registry contains:
- product.kdcube
- public.docx-press
- public.pdf-press
- custom.ops-runbook

Then:
- consumer=solver.react.decision.v2 -> product.kdcube, custom.ops-runbook
- consumer=answer.generator.strong -> product.kdcube, public.docx-press
- consumer=answer.generator.regular -> all skills (no matching entry)

What this configuration achieves:
- Per-agent planning control: decide which skills appear in each agent catalog.
- Per-agent resolution control: SK short ids are built from each consumer's visible set.
- Per-agent instruction injection control: hidden skills for a consumer are not injected into that consumer's generator prompt.

SDK mechanisms using this:
- ReAct decision catalog: `consumer="solver.react.decision.v2"` (planning catalog visibility).
- `react.read` short-id resolution: `consumer="solver.react.decision.v2"` (which SKx ids can be loaded).
- Generator role-based resolution/injection: `consumer=<role>` such as `answer.generator.strong`.

For authoring examples, see `docs/sdk/skills/custom-skills-README.md`.
For runtime wiring, see `docs/sdk/skills/skills-infra-README.md`.


## Skill discovery and selection

1) Skills are loaded from:
   - built-in skills directory
   - optional CUSTOM_SKILLS_ROOT (for custom namespace)

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
    consumer="solver.react.decision.v2",
    tool_catalog=tool_catalog,
    react_tools=get_react_tools_catalog(),
    include_skill_gallery=True,
)

# kdcube_ai_app/apps/chat/sdk/solutions/react/v2/layout.py
skill_block = skills_gallery_text(
    consumer=consumer,
    tool_catalog=tools_list,
)
```

`react.read` resolution usage:

```python
# kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/read.py
short_map = build_skill_short_id_map(consumer="solver.react.decision.v2")
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
          |  Skill Catalogs     |        |  skills descriptor |
          |  (system prompt)    |        |  agents_config     |
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
