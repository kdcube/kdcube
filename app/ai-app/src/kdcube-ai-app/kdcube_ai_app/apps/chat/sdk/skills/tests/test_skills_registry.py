# SPDX-License-Identifier: MIT

import unittest

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    build_skill_short_id_map,
    get_skill,
    resolve_skill_ref,
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
        self.assertIsNotNone(get_skill("task.tasks"))
        self.assertIsNotNone(get_skill("task.job"))


if __name__ == "__main__":
    unittest.main()
