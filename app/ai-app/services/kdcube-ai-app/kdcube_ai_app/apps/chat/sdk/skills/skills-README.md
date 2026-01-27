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
    internal/
      <skill_id>/
        SKILL.md
        compact.md
        tools.yaml
    custom/
      <skill_id>/
        SKILL.md
        compact.md
        tools.yaml

Each skill folder contains:
- SKILL.md: metadata + full instruction body
- compact.md: optional compact instruction (not used by default)
- tools.yaml: tool ids and why they matter for this skill


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

For infrastructure details and how descriptors are wired into runtime, see:
  skills-infra-README.md


## Skill discovery and selection

1) Skills are loaded from:
   - built-in skills directory
   - optional CUSTOM_SKILLS_ROOT (for custom namespace)

2) Skill catalogs are shown to decision/coordinator in system instruction.

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
                 |  Decision / Coordinator       |
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
