# SPDX-License-Identifier: MIT

import unittest
import pathlib
import tempfile

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    SkillsSubsystem,
    build_skills_instruction_block,
    build_skill_short_id_map,
    get_skill,
    import_skillset,
    resolve_skill_ref,
    set_active_skills_subsystem,
    skills_for_consumer,
    skills_gallery_text,
)


class SkillsRegistryTests(unittest.TestCase):
    def test_resolve_skill_ref(self):
        short_map = build_skill_short_id_map(consumer="solver.react.decision")
        self.assertTrue(short_map, "Expected at least one skill in the gallery")
        short_id, full_id = next(iter(short_map.items()))

        self.assertEqual(resolve_skill_ref(short_id, short_id_map=short_map), full_id)
        self.assertEqual(resolve_skill_ref(f"skills.{full_id}", short_id_map=short_map), full_id)
        self.assertEqual(resolve_skill_ref(full_id, short_id_map=short_map), full_id)

    def test_solution_task_skills_are_builtin(self):
        self.assertIsNotNone(get_skill("automation.automations"))
        self.assertIsNotNone(get_skill("automation.job"))

    def test_hidden_disclosure_skill_is_loadable_but_not_advertised(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = pathlib.Path(tmp) / "skills"
            skill_dir = skills_root / "product" / "hidden-memory"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: hidden-memory
description: Hidden operational memory guidance.
namespace: product
agent_disclosure: hidden
---
# Hidden Memory

Use memory tools carefully.
""",
                encoding="utf-8",
            )

            subsystem = SkillsSubsystem(
                descriptor={"custom_skills_root": str(skills_root), "agents_config": {}}
            )
            set_active_skills_subsystem(subsystem)
            try:
                self.assertIsNotNone(get_skill("product.hidden-memory"))
                self.assertNotIn(
                    "product.hidden-memory",
                    build_skill_short_id_map(consumer="solver.react.v2.decision.v2.strong").values(),
                )
                self.assertNotIn(
                    "product.hidden-memory",
                    skills_gallery_text(consumer="solver.react.v2.decision.v2.strong"),
                )

                block = build_skills_instruction_block(["product.hidden-memory"])
                self.assertIn("Disclosure rule:", block)
                self.assertIn("Use memory tools carefully.", block)
                self.assertNotIn("Skill: hidden-memory", block)
                self.assertNotIn("product.hidden-memory", block)
            finally:
                set_active_skills_subsystem(SkillsSubsystem())

    def test_required_tool_skills_are_filtered_by_available_tool_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_root = pathlib.Path(tmp) / "skills"
            memory_dir = skills_root / "product" / "memory-guidance"
            parent_dir = skills_root / "product" / "assistant-guidance"
            memory_dir.mkdir(parents=True)
            parent_dir.mkdir(parents=True)
            (memory_dir / "SKILL.md").write_text(
                """---
name: memory-guidance
description: Durable memory guidance.
namespace: product
agent_disclosure: hidden
---
# Memory Guidance

Use durable memory only through the declared tools.
""",
                encoding="utf-8",
            )
            (memory_dir / "tools.yaml").write_text(
                """tools:
  - id: memory.search_memory
    role: read
    required: true
  - id: memory.record_memory
    role: write
    required: true
""",
                encoding="utf-8",
            )
            (parent_dir / "SKILL.md").write_text(
                """---
name: assistant-guidance
description: Visible assistant guidance.
namespace: product
import:
  - product.memory-guidance
---
# Assistant Guidance

Visible instructions.
""",
                encoding="utf-8",
            )

            subsystem = SkillsSubsystem(
                descriptor={"custom_skills_root": str(skills_root), "agents_config": {}}
            )
            set_active_skills_subsystem(subsystem)
            try:
                empty_catalog = []
                memory_catalog = [
                    {"id": "memory.search_memory"},
                    {"id": "memory.record_memory"},
                ]

                self.assertNotIn(
                    "product.memory-guidance",
                    [f"{s.namespace}.{s.id}" for s in skills_for_consumer(
                        "solver.react.v2.decision.v2.strong",
                        tool_catalog=empty_catalog,
                    )],
                )
                self.assertNotIn(
                    "product.memory-guidance",
                    build_skills_instruction_block(
                        ["product.assistant-guidance"],
                        tool_catalog=empty_catalog,
                    ),
                )
                self.assertEqual(
                    import_skillset(
                        ["product.assistant-guidance"],
                        tool_catalog=empty_catalog,
                    ),
                    ["product.assistant-guidance"],
                )

                self.assertIn(
                    "product.memory-guidance",
                    import_skillset(
                        ["product.assistant-guidance"],
                        tool_catalog=memory_catalog,
                    ),
                )
                self.assertIn(
                    "Use durable memory only through the declared tools.",
                    build_skills_instruction_block(
                        ["product.assistant-guidance"],
                        tool_catalog=memory_catalog,
                    ),
                )
            finally:
                set_active_skills_subsystem(SkillsSubsystem())


if __name__ == "__main__":
    unittest.main()
