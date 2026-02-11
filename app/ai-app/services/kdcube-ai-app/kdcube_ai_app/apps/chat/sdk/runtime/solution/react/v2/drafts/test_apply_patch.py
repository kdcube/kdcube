# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.drafts.tools import apply_patch, PatchError


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_apply_patch_multi_file(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "out"
    base.mkdir(parents=True, exist_ok=True)

    (base / "alpha.txt").write_text("hello\n", encoding="utf-8")
    (base / "delete.txt").write_text("bye\n", encoding="utf-8")
    (base / "move.txt").write_text("old\n", encoding="utf-8")

    patch_text = """*** Begin Patch
*** Add File: new.txt
+line1
+line2
*** Update File: alpha.txt
@@
-hello
+hello world
*** Delete File: delete.txt
*** Update File: move.txt
*** Move to: moved.txt
@@
-old
+new
*** End Patch"""

    result = apply_patch(patch_text, base)

    assert _read_text(base / "new.txt") == "line1\nline2\n"
    assert _read_text(base / "alpha.txt") == "hello world\n"
    assert not (base / "delete.txt").exists()
    assert not (base / "move.txt").exists()
    assert _read_text(base / "moved.txt") == "new\n"

    assert result.summary.added == ["new.txt"]
    assert "alpha.txt" in result.summary.modified
    assert "moved.txt" in result.summary.modified
    assert result.summary.deleted == ["delete.txt"]


def test_apply_patch_requires_markers(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "out"
    base.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PatchError):
        apply_patch("hello", base)
