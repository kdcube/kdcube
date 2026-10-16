"""W389: the examples folder is located once, not resolved again on every registry read."""
from __future__ import annotations

from pathlib import Path

from kdcube_ai_app.infra.plugin import bundle_store


def test_the_examples_root_is_the_image_examples_folder():
    expected = (Path(bundle_store.__file__).resolve().parents[2] / bundle_store._EXAMPLES_REL_PATH).resolve()
    assert bundle_store._examples_root() == expected


def test_reading_the_examples_root_does_not_walk_the_filesystem(monkeypatch):
    def no_resolve(self, *args, **kwargs):
        raise AssertionError("Path.resolve on a registry read")

    monkeypatch.setattr(Path, "resolve", no_resolve)
    assert bundle_store._examples_root().is_absolute()
