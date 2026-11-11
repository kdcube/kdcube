# SPDX-License-Identifier: MIT
"""io_tools writes and reads only inside the turn output directory.

``tool_call(filename=...)`` and ``save_ret(filename=...)`` take a caller-supplied
relative name; ``save_ret`` also reads the tool-call records listed in
``raw_files``. Each of those joins is checked against the output directory by
path components, so ``../x``, an absolute path, a symlink and a sibling
directory sharing the output dir's name prefix all stay out.
"""
from __future__ import annotations

import asyncio
import json
import os.path
import pathlib

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools import io_tools as io_mod


def _read_value(where: str = "a") -> dict:
    return {"value": 1, "where": where}


@pytest.fixture
def outdir(tmp_path):
    token = OUTDIR_CV.set(str(tmp_path / "turn" / "out"))
    try:
        od = io_mod._outdir()
        yield od
    finally:
        OUTDIR_CV.reset(token)


def _files_outside(od: pathlib.Path, base: pathlib.Path) -> list[str]:
    return sorted(
        str(p.relative_to(base)) for p in base.rglob("*")
        if p.is_file() and not p.resolve().is_relative_to(od.resolve())
    )


def _call(**kw):
    return asyncio.run(io_mod.AgentIO().tool_call(
        fn=_read_value, params={"where": "b"}, call_reason="x", tool_id="demo.read_value", **kw,
    ))


@pytest.mark.parametrize("rel", ["../escaped.json", "../../escaped.json", "/etc/passwd", ".", ""])
def test_escaping_relpaths_are_rejected(outdir, rel):
    assert io_mod._contained_output_relpath(outdir, rel) is None


def test_a_sibling_directory_sharing_the_prefix_is_rejected(outdir):
    sibling = outdir.parent / (outdir.name + "-evil")
    sibling.mkdir()
    rel = f"../{sibling.name}/loot.json"
    assert os.path.normpath(outdir / rel).startswith(str(outdir))
    assert io_mod._contained_output_relpath(outdir, rel) is None


def test_a_symlink_out_of_the_output_dir_is_rejected(outdir, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (outdir / "link").symlink_to(target)
    assert io_mod._contained_output_relpath(outdir, "link/x.json") is None


@pytest.mark.parametrize("rel,expected", [
    ("call.json", "call.json"),
    ("calls/call.json", "calls/call.json"),
    ("./calls/../call.json", "call.json"),
])
def test_contained_relpaths_are_kept(outdir, rel, expected):
    assert io_mod._contained_output_relpath(outdir, rel) == expected


def test_tool_call_cannot_write_outside_the_output_dir(outdir, tmp_path, caplog):
    with caplog.at_level("WARNING"):
        result = _call(filename="../escaped.json")
    assert result == {"value": 1, "where": "b"}, "the tool's result survives the refusal"
    assert _files_outside(outdir, tmp_path) == []
    assert "REFUSED" in caplog.text and "../escaped.json" in caplog.text


def test_a_refused_filename_is_persisted_and_indexed_under_a_reserved_name(outdir):
    _call(filename="../escaped.json")
    index = io_mod.read_index(outdir)
    names = index.get("demo.read_value") or []
    assert names and all(".." not in name for name in names)
    payload = json.loads((outdir / names[-1]).read_text(encoding="utf-8"))
    assert payload["ret"] == {"value": 1, "where": "b"}


def test_a_nested_filename_is_written(outdir):
    assert _call(filename="calls/call.json") == {"value": 1, "where": "b"}
    payload = json.loads((outdir / "calls" / "call.json").read_text(encoding="utf-8"))
    assert payload["ret"] == {"value": 1, "where": "b"}


def test_save_tool_call_cannot_write_outside_the_output_dir(outdir, tmp_path):
    rel = asyncio.run(io_mod.AgentIO().save_tool_call(
        tool_id="demo.read_value", description="", data={"v": 1}, filename="../../escaped.json",
    ))
    assert ".." not in rel and (outdir / rel).is_file()
    assert _files_outside(outdir, tmp_path) == []


def test_save_ret_cannot_write_outside_the_output_dir(outdir, tmp_path, caplog):
    with caplog.at_level("WARNING"):
        rel = asyncio.run(io_mod.AgentIO().save_ret(data=json.dumps({"ok": True}), filename="../result.json"))
    assert rel == "result.json"
    assert (outdir / "result.json").is_file()
    assert _files_outside(outdir, tmp_path) == []
    assert "REFUSED save_ret" in caplog.text


def test_save_ret_does_not_promote_a_record_outside_the_output_dir(outdir, tmp_path):
    secret = tmp_path / "turn" / "secret.json"
    secret.write_text(json.dumps({"description": "", "in": {"params": {}}, "ret": {"secret": "s3cr3t"}}), encoding="utf-8")
    asyncio.run(io_mod.AgentIO().save_ret(
        data=json.dumps({"ok": True, "raw_files": {"demo.read_value": ["../secret.json"]}}),
    ))
    assert "s3cr3t" not in (outdir / "result.json").read_text(encoding="utf-8")
