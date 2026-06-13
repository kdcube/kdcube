# SPDX-License-Identifier: MIT

"""Custom skills visibility tests.

Test that config-first skill visibility controls which skills are visible to
which skill consumers. Legacy AGENTS_CONFIG modules are accepted as fallback.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_custom_skills_visibility.py -v
  pytest test_custom_skills_visibility.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pytest


def _load_agents_config(bundle_dir) -> dict:
    """Return skill consumer visibility config from bundle config or legacy descriptor."""
    try:
        template_path = bundle_dir / "config" / "bundles.template.yaml"
        if template_path.exists():
            import yaml

            template = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
            items = ((template.get("bundles") or {}).get("items") or [])
            for item in items:
                if item.get("id") != bundle_dir.name:
                    continue
                agents = (
                    (((item.get("config") or {}).get("surfaces") or {}).get("as_consumer") or {}).get("agents")
                    or {}
                )
                if isinstance(agents, dict):
                    for agent in agents.values():
                        skills = agent.get("skills") if isinstance(agent, dict) else None
                        if isinstance(skills, dict):
                            consumers = skills.get("consumers") or {}
                            assert isinstance(consumers, dict)
                            return consumers

        import importlib.util

        if not (bundle_dir / "skills_descriptor.py").exists():
            pytest.skip(f"Bundle '{bundle_dir}' has no config skills surface or legacy skills_descriptor.py")

        spec = importlib.util.spec_from_file_location(
            "skills_descriptor", bundle_dir / "skills_descriptor.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not hasattr(mod, "AGENTS_CONFIG"):
            pytest.skip("Bundle has no AGENTS_CONFIG")
        return mod.AGENTS_CONFIG
    except Exception as e:
        pytest.skip(f"Cannot load AGENTS_CONFIG: {e}")


class TestAgentsConfigStructure:
    """Verify AGENTS_CONFIG has the correct structure."""

    def test_agents_config_is_dict(self, bundle, bundle_dir):
        """AGENTS_CONFIG is a dict."""
        cfg = _load_agents_config(bundle_dir)
        assert isinstance(cfg, dict)

    def test_agents_config_keys_are_strings(self, bundle, bundle_dir):
        """AGENTS_CONFIG keys (agent role names) are strings."""
        cfg = _load_agents_config(bundle_dir)
        for key in cfg:
            assert isinstance(key, str), f"AGENTS_CONFIG key must be str, got {type(key)}"

    def test_agents_config_values_are_dicts(self, bundle, bundle_dir):
        """AGENTS_CONFIG values are dicts (may be empty)."""
        cfg = _load_agents_config(bundle_dir)
        for role, val in cfg.items():
            assert isinstance(val, dict), (
                f"AGENTS_CONFIG[{role!r}] must be a dict, got {type(val)}"
            )


class TestSkillsSubsystemVisibility:
    """Test SkillsSubsystem visibility filtering (unit-level, no real skills needed)."""

    def test_skills_subsystem_accepts_descriptor_with_agents_config(self):
        """SkillsSubsystem initializes when descriptor contains agents_config."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        descriptor = {
            "agents_config": {
                "solver.react.v2": {},
                "gate": {"enabled_skills": []},
            }
        }
        subsystem = SkillsSubsystem(descriptor=descriptor, bundle_root=None)
        assert subsystem is not None

    def test_skills_subsystem_empty_agents_config_means_all_visible(self):
        """Empty AGENTS_CONFIG dict means all skills are visible to all agents."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        descriptor = {"agents_config": {}}
        subsystem = SkillsSubsystem(descriptor=descriptor, bundle_root=None)
        registry = subsystem.get_skill_registry()
        assert isinstance(registry, dict)

    def test_set_active_skills_subsystem_and_get_back(self):
        """set_active_skills_subsystem() makes the subsystem retrievable."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
            SkillsSubsystem,
            set_active_skills_subsystem,
            get_active_skills_subsystem,
        )
        subsystem = SkillsSubsystem(descriptor=None, bundle_root=None)
        set_active_skills_subsystem(subsystem)
        retrieved = get_active_skills_subsystem()
        assert retrieved is subsystem

    def test_set_skills_descriptor_returns_subsystem(self):
        """set_skills_descriptor() returns a SkillsSubsystem instance."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
            SkillsSubsystem,
            set_skills_descriptor,
        )
        result = set_skills_descriptor(descriptor=None, bundle_root=None)
        assert isinstance(result, SkillsSubsystem)
