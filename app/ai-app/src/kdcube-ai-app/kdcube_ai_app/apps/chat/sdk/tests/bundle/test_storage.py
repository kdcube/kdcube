# SPDX-License-Identifier: MIT

"""Storage & Props tests for bundles (Type 8).

Test that bundle_storage_root(), storage paths, and on_bundle_load() work correctly.
Tests work with any bundle selected by folder.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_storage.py -v
  pytest test_storage.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pathlib
import asyncio
import pytest


class TestBundleStorageRoot:
    """Test that bundle_storage_root() resolves correctly."""

    def test_bundle_storage_root_returns_path_or_none(self, bundle):
        """bundle_storage_root() returns a Path or None (never raises)."""
        result = bundle.bundle_storage_root()
        assert result is None or isinstance(result, pathlib.Path)

    def test_resolve_bundle_storage_root_returns_path(self):
        """resolve_bundle_storage_root() always returns a Path."""
        from kdcube_ai_app.infra.plugin.bundle_storage import resolve_bundle_storage_root
        root = resolve_bundle_storage_root()
        assert isinstance(root, pathlib.Path)

    def test_bundle_storage_dir_includes_bundle_id(self):
        """bundle_storage_dir() path includes the bundle_id segment."""
        from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
        path = bundle_storage_dir(bundle_id="my-bundle", ensure=False)
        assert "my-bundle" in str(path)

    def test_bundle_storage_dir_includes_tenant_when_provided(self):
        """bundle_storage_dir() path includes tenant segment when passed."""
        from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
        path = bundle_storage_dir(bundle_id="b", tenant="acme", ensure=False)
        assert "acme" in str(path)

    def test_bundle_storage_dir_includes_project_when_provided(self):
        """bundle_storage_dir() path includes project segment when passed."""
        from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
        path = bundle_storage_dir(bundle_id="b", tenant="t", project="main", ensure=False)
        assert "main" in str(path)

    def test_bundle_storage_dir_tenant_project_bundle_all_in_path(self):
        """Full path contains tenant / project / bundle_id in order."""
        from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
        path = bundle_storage_dir(
            bundle_id="my-bundle", tenant="acme", project="main", ensure=False
        )
        s = str(path)
        assert "acme" in s
        assert "main" in s
        assert "my-bundle" in s

    def test_bundle_storage_dir_is_absolute(self):
        """bundle_storage_dir() returns an absolute path."""
        from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
        path = bundle_storage_dir(bundle_id="b", ensure=False)
        assert path.is_absolute()

    def test_storage_for_spec_returns_none_when_spec_is_none(self):
        """storage_for_spec(spec=None) returns None gracefully."""
        from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
        result = storage_for_spec(spec=None)
        assert result is None

    def test_storage_for_spec_returns_none_when_spec_has_no_id(self):
        """storage_for_spec returns None when spec has empty bundle id."""
        from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
        from unittest.mock import MagicMock
        spec = MagicMock()
        spec.id = ""
        result = storage_for_spec(spec=spec)
        assert result is None

    def test_storage_for_spec_returns_path_for_valid_spec(self):
        """storage_for_spec returns a Path for a spec with a valid bundle id."""
        from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
        from unittest.mock import MagicMock
        spec = MagicMock()
        spec.id = "test-bundle"
        spec.git_commit = None
        spec.ref = None
        spec.version = None
        result = storage_for_spec(spec=spec, ensure=False)
        assert isinstance(result, pathlib.Path)
        assert "test-bundle" in str(result)

    def test_storage_for_spec_ignores_versioned_spec_fields_for_primary_root(self, monkeypatch, tmp_path):
        """Primary bundle storage is stable by bundle id, not by git/ref/version."""
        from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
        from unittest.mock import MagicMock

        monkeypatch.setenv("BUNDLE_STORAGE_ROOT", str(tmp_path))
        spec = MagicMock()
        spec.id = "test-bundle"
        spec.git_commit = "abc123"
        spec.ref = "main"
        spec.version = "deadbeef"

        result = storage_for_spec(
            spec=spec,
            tenant="tenant-a",
            project="project-a",
            ensure=False,
        )

        assert result == (tmp_path / "tenant-a" / "project-a" / "test-bundle").resolve()


class TestOnBundleLoad:
    """Test that on_bundle_load() is implemented correctly."""

    def test_on_bundle_load_method_exists(self, bundle):
        """Bundle has on_bundle_load() method."""
        assert hasattr(bundle, "on_bundle_load")
        assert callable(bundle.on_bundle_load)

    def test_on_bundle_load_does_not_raise_with_no_args(self, bundle):
        """on_bundle_load() called with no extra kwargs does not raise."""
        try:
            asyncio.run(bundle.on_bundle_load())
        except Exception as exc:
            pytest.fail(f"on_bundle_load() raised unexpectedly: {exc}")
