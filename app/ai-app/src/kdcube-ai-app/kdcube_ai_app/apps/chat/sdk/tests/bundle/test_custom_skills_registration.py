# SPDX-License-Identifier: MIT

"""Custom skills registration tests.

Test that custom skills register correctly with the Skills Subsystem.
Config-first bundles declare skills under
`surfaces.as_consumer.agents.<agent>.skills`; legacy bundles may still expose a
skills_descriptor module. Bundles without either declaration are skipped.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_custom_skills_registration.py -v
  pytest test_custom_skills_registration.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pathlib
import pytest


def _template_bundle_config(bundle_dir):
    template_path = bundle_dir / "config" / "bundles.template.yaml"
    if not template_path.exists():
        return None
    import yaml
    template = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    items = ((template.get("bundles") or {}).get("items") or [])
    for item in items:
        if item.get("id") == bundle_dir.name:
            return item.get("config") or {}
    return None


def _load_config_skills(bundle_dir):
    config = _template_bundle_config(bundle_dir)
    agents = (((config or {}).get("surfaces") or {}).get("as_consumer") or {}).get("agents") or {}
    if not isinstance(agents, dict):
        return None
    for agent in agents.values():
        if not isinstance(agent, dict):
            continue
        skills = agent.get("skills")
        if isinstance(skills, dict):
            return skills
    return None


def _load_legacy_skills_descriptor(bundle_dir):
    """Return the legacy skills_descriptor module for the bundle, or None."""
    try:
        if not (bundle_dir / "skills_descriptor.py").exists():
            return None

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "skills_descriptor", bundle_dir / "skills_descriptor.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        pytest.skip(f"Cannot load legacy skills_descriptor: {e}")


def _configured_custom_skills_root(bundle_dir):
    config_skills = _load_config_skills(bundle_dir)
    if isinstance(config_skills, dict):
        root = (
            config_skills.get("custom_root")
            if "custom_root" in config_skills
            else config_skills.get("custom_skills_root", config_skills.get("root"))
        )
        if root is False or root is None:
            return None
        path = pathlib.Path(str(root))
        return path if path.is_absolute() else (bundle_dir / path)

    mod = _load_legacy_skills_descriptor(bundle_dir)
    if mod is not None and hasattr(mod, "CUSTOM_SKILLS_ROOT"):
        return mod.CUSTOM_SKILLS_ROOT
    pytest.skip(f"Bundle '{bundle_dir}' has no config skills surface or legacy skills_descriptor.py")


def _configured_agents_config(bundle_dir) -> dict:
    config_skills = _load_config_skills(bundle_dir)
    if isinstance(config_skills, dict):
        consumers = config_skills.get("consumers") or {}
        assert isinstance(consumers, dict)
        return consumers

    mod = _load_legacy_skills_descriptor(bundle_dir)
    if mod is not None and hasattr(mod, "AGENTS_CONFIG"):
        return mod.AGENTS_CONFIG
    pytest.skip(f"Bundle '{bundle_dir}' has no skill visibility config")


class TestSkillsConfigurationStructure:
    """Verify bundle skill configuration has the expected structure."""

    def test_skill_config_defines_custom_skills_root(self, bundle, bundle_dir):
        """Skill config defines a bundle-local root when custom skills are declared."""
        root = _configured_custom_skills_root(bundle_dir)
        assert root is None or isinstance(root, pathlib.Path)

    def test_custom_skills_root_is_path_or_none(self, bundle, bundle_dir):
        """Configured custom skill root is a pathlib.Path or None."""
        root = _configured_custom_skills_root(bundle_dir)
        assert root is None or isinstance(root, pathlib.Path), (
            f"custom skill root must be Path or None, got {type(root)}"
        )

    def test_agents_config_is_dict(self, bundle, bundle_dir):
        """Skill visibility config is a dict when defined."""
        assert isinstance(_configured_agents_config(bundle_dir), dict)


class TestSkillsSubsystem:
    """Verify SkillsSubsystem loads and caches skill registry."""

    def test_skills_subsystem_can_be_created(self):
        """SkillsSubsystem initializes without errors with empty descriptor."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        subsystem = SkillsSubsystem(descriptor=None, bundle_root=None)
        assert subsystem is not None

    def test_get_skill_registry_returns_dict(self):
        """get_skill_registry() returns a dict (may be empty with no skills)."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        subsystem = SkillsSubsystem(descriptor=None, bundle_root=None)
        registry = subsystem.get_skill_registry()
        assert isinstance(registry, dict)

    def test_get_skill_registry_is_cached(self):
        """Two calls to get_skill_registry() return the same object."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        subsystem = SkillsSubsystem(descriptor=None, bundle_root=None)
        r1 = subsystem.get_skill_registry()
        r2 = subsystem.get_skill_registry()
        assert r1 is r2

    def test_clear_cache_resets_registry(self):
        """clear_cache() forces get_skill_registry() to re-load."""
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
        subsystem = SkillsSubsystem(descriptor=None, bundle_root=None)
        r1 = subsystem.get_skill_registry()
        subsystem.clear_cache()
        r2 = subsystem.get_skill_registry()
        assert r1 is not r2  # must be a fresh object after cache clear

    def test_bundle_skills_loaded_when_skills_dir_exists(self, bundle, bundle_dir):
        """When bundle has skills/ directory, at least one skill is registered."""
        skills_root = _configured_custom_skills_root(bundle_dir)
        if skills_root is None or not skills_root.exists():
            pytest.skip("Bundle has no skills/ directory")

        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem

        descriptor = {"custom_skills_root": str(skills_root)}
        subsystem = SkillsSubsystem(descriptor=descriptor, bundle_root=bundle_dir)
        registry = subsystem.get_skill_registry()
        assert len(registry) > 0, (
            f"Bundle has skills/ directory but SkillsSubsystem found 0 skills"
        )
