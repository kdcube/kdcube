# SPDX-License-Identifier: MIT
"""
Tests for the source-fingerprint accessors used by the static-UI route's
signature-aware short-circuit.

Covers:
 - `_compute_ui_build_signature` returns a stable fingerprint string
 - the fingerprint changes when a source file's mtime / size changes
 - generated `.js` / `.jsx` siblings of `.ts` / `.tsx` files are ignored
   (regression for the shadow-file fix)
 - `compute_ui_main_view_signature` and `compute_ui_widget_signature` honour
   `enabled=false`, missing `src_folder`, and unknown widget aliases by
   returning `None`
"""

from __future__ import annotations

import os
import pathlib
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


def _make_entrypoint(
    *,
    bundle_root: pathlib.Path,
    storage_root: pathlib.Path,
    bundle_props: dict | None = None,
) -> BaseEntrypoint:
    """
    Construct a BaseEntrypoint stub that doesn't run __init__.

    The signature accessors only need: `self.bundle_props`, `self.config`,
    `self.bundle_storage_root()`, `self._bundle_root()`. Everything else is
    irrelevant for these tests.
    """
    ep = BaseEntrypoint.__new__(BaseEntrypoint)
    ep.bundle_props = dict(bundle_props or {})
    ep.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle@test"))
    ep.bundle_storage_root = lambda: storage_root  # type: ignore[assignment]
    ep._bundle_root = lambda: str(bundle_root)     # type: ignore[assignment]
    return ep


def _touch(path: pathlib.Path, *, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _bump_mtime(path: pathlib.Path) -> None:
    """Force `st_mtime_ns` to differ even on filesystems with second
    resolution by writing fresh content."""
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(current + "\n// edited\n", encoding="utf-8")
    # Also explicitly bump in case the filesystem coalesces same-second
    # writes (e.g. some EFS configurations).
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_compute_ui_widget_signature_changes_when_source_mtime_changes(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"
    src = bundle_root / "ui" / "widgets" / "demo" / "src"
    _touch(src / "main.tsx", content="export const x = 1\n")
    _touch(src / "styles.css", content=".a{color:red}\n")

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "widgets": {
                    "demo": {
                        "enabled": True,
                        "src_folder": "ui/widgets/demo/src",
                        "build_command": "npm install --no-package-lock && npm run build",
                    }
                }
            }
        },
    )

    sig_before = ep.compute_ui_widget_signature("demo")
    assert isinstance(sig_before, str) and sig_before

    _bump_mtime(src / "styles.css")

    sig_after = ep.compute_ui_widget_signature("demo")
    assert isinstance(sig_after, str) and sig_after
    assert sig_after != sig_before


def test_compute_ui_widget_signature_ignores_generated_shadow_files(tmp_path):
    """Vite/tsc emit `.js` (and `.js.map`) siblings of `.ts`/`.tsx` files
    inside the source folder on some configurations. Those generated
    artifacts must not change the fingerprint — otherwise the build would
    loop forever (regression check for the shadow-file fix)."""
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"
    src = bundle_root / "ui" / "widgets" / "demo" / "src"
    _touch(src / "main.tsx", content="export const x = 1\n")

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "widgets": {
                    "demo": {
                        "enabled": True,
                        "src_folder": "ui/widgets/demo/src",
                        "build_command": "npm install --no-package-lock && npm run build",
                    }
                }
            }
        },
    )

    sig_before = ep.compute_ui_widget_signature("demo")

    # Emit a generated .js sibling and a .js.map for main.tsx.
    _touch(src / "main.js", content="var x=1;//# sourceMappingURL=main.js.map\n")
    _touch(src / "main.js.map", content="{}")

    sig_after = ep.compute_ui_widget_signature("demo")
    assert sig_after == sig_before


def test_compute_ui_widget_signature_returns_none_when_disabled(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"
    src = bundle_root / "ui" / "widgets" / "demo" / "src"
    _touch(src / "main.tsx", content="export const x = 1\n")

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "widgets": {
                    "demo": {
                        "enabled": False,
                        "src_folder": "ui/widgets/demo/src",
                        "build_command": "npm install --no-package-lock && npm run build",
                    }
                }
            }
        },
    )

    assert ep.compute_ui_widget_signature("demo") is None


def test_compute_ui_widget_signature_returns_none_when_missing_src_folder(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "widgets": {
                    "demo": {
                        "enabled": True,
                        # No src_folder — the build is unconfigured.
                        "build_command": "npm install --no-package-lock && npm run build",
                    }
                }
            }
        },
    )

    assert ep.compute_ui_widget_signature("demo") is None


def test_compute_ui_widget_signature_returns_none_for_unknown_alias(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"
    src = bundle_root / "ui" / "widgets" / "demo" / "src"
    _touch(src / "main.tsx", content="export const x = 1\n")

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "widgets": {
                    "demo": {
                        "enabled": True,
                        "src_folder": "ui/widgets/demo/src",
                        "build_command": "npm install --no-package-lock && npm run build",
                    }
                }
            }
        },
    )

    assert ep.compute_ui_widget_signature("unknown-widget") is None


def test_compute_ui_main_view_signature_changes_with_source(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"
    src = bundle_root / "ui" / "main" / "src"
    _touch(src / "App.tsx", content="export const App = () => null\n")

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={
            "ui": {
                "main_view": {
                    "enabled": True,
                    "src_folder": "ui/main/src",
                    "build_command": "npm install --no-package-lock && npm run build",
                }
            }
        },
    )

    sig_before = ep.compute_ui_main_view_signature()
    assert isinstance(sig_before, str) and sig_before

    _bump_mtime(src / "App.tsx")

    sig_after = ep.compute_ui_main_view_signature()
    assert isinstance(sig_after, str)
    assert sig_after != sig_before


def test_compute_ui_main_view_signature_returns_none_when_unconfigured(tmp_path):
    bundle_root = tmp_path / "bundle"
    storage_root = tmp_path / "storage"

    ep = _make_entrypoint(
        bundle_root=bundle_root,
        storage_root=storage_root,
        bundle_props={"ui": {}},
    )

    assert ep.compute_ui_main_view_signature() is None


def test_compute_ui_widget_signature_returns_none_when_storage_unavailable(tmp_path):
    bundle_root = tmp_path / "bundle"
    src = bundle_root / "ui" / "widgets" / "demo" / "src"
    _touch(src / "main.tsx", content="export const x = 1\n")

    ep = BaseEntrypoint.__new__(BaseEntrypoint)
    ep.bundle_props = {
        "ui": {
            "widgets": {
                "demo": {
                    "enabled": True,
                    "src_folder": "ui/widgets/demo/src",
                    "build_command": "npm install --no-package-lock && npm run build",
                }
            }
        }
    }
    ep.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle@test"))
    ep.bundle_storage_root = lambda: None       # type: ignore[assignment]
    ep._bundle_root = lambda: str(bundle_root)  # type: ignore[assignment]

    assert ep.compute_ui_widget_signature("demo") is None
