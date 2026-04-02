# SPDX-License-Identifier: MIT

import subprocess

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import (
    build_exec_snapshot_workspace,
    rehost_files_from_timeline,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.workspace import hydrate_workspace_paths


@pytest.mark.asyncio
async def test_rehost_files_from_timeline_base64(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/old.txt","physical_path":"turn_prev/files/old.txt"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/old.txt",
                "text": "hello",
                "turn_id": "turn_prev",
            },
        ]
    }
    class _Settings:
        STORAGE_PATH = str(tmp_path)
    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()
    res = await rehost_files_from_timeline(ctx_browser=ctx, paths=["turn_prev/files/old.txt"], outdir=tmp_path)
    assert "turn_prev/files/old.txt" in res.get("rehosted", [])
    assert (tmp_path / "turn_prev" / "files" / "old.txt").exists()


@pytest.mark.asyncio
async def test_rehost_files_from_timeline_expands_directory_prefix(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/user-prefs@2026-03-30/settings.json","physical_path":"turn_prev/files/user-prefs@2026-03-30/settings.json"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/user-prefs@2026-03-30/theme/colors.json","physical_path":"turn_prev/files/user-prefs@2026-03-30/theme/colors.json"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/other.txt","physical_path":"turn_prev/files/other.txt"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/user-prefs@2026-03-30/settings.json","physical_path":"turn_prev/files/user-prefs@2026-03-30/settings.json"}',
                "path": "fi:turn_prev.files/user-prefs@2026-03-30/settings.json",
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/user-prefs@2026-03-30/settings.json",
                "text": "{\"theme\": \"dark\"}",
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/user-prefs@2026-03-30/theme/colors.json","physical_path":"turn_prev/files/user-prefs@2026-03-30/theme/colors.json"}',
                "path": "fi:turn_prev.files/user-prefs@2026-03-30/theme/colors.json",
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "path": "fi:turn_prev.files/user-prefs@2026-03-30/theme/colors.json",
                "text": '{"primary": "#000"}',
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    res = await rehost_files_from_timeline(
        ctx_browser=ctx,
        paths=["turn_prev/files/user-prefs@2026-03-30"],
        outdir=tmp_path,
    )

    assert "turn_prev/files/user-prefs@2026-03-30/settings.json" in res.get("rehosted", [])
    assert "turn_prev/files/user-prefs@2026-03-30/theme/colors.json" in res.get("rehosted", [])
    assert "turn_prev/files/user-prefs@2026-03-30" not in res.get("missing", [])
    assert (tmp_path / "turn_prev" / "files" / "user-prefs@2026-03-30" / "settings.json").read_text(encoding="utf-8") == "{\"theme\": \"dark\"}"
    assert (tmp_path / "turn_prev" / "files" / "user-prefs@2026-03-30" / "theme" / "colors.json").read_text(encoding="utf-8") == '{"primary": "#000"}'


def test_build_exec_snapshot_workspace_copies_referenced_directory_tree(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    source_dir = outdir / "turn_prev" / "files" / "user-prefs@2026-03-30"
    (source_dir / "theme").mkdir(parents=True, exist_ok=True)
    (source_dir / "settings.json").write_text("{\"theme\": \"dark\"}", encoding="utf-8")
    (source_dir / "theme" / "colors.json").write_text("{\"primary\": \"#000\"}", encoding="utf-8")

    ws = build_exec_snapshot_workspace(
        workdir=workdir,
        outdir=outdir,
        timeline={},
        code='source_dir = Path(OUTPUT_DIR) / "turn_prev/files/user-prefs@2026-03-30"',
    )

    snap_out = ws["outdir"]
    assert (snap_out / "turn_prev" / "files" / "user-prefs@2026-03-30" / "settings.json").read_text(encoding="utf-8") == "{\"theme\": \"dark\"}"
    assert (snap_out / "turn_prev" / "files" / "user-prefs@2026-03-30" / "theme" / "colors.json").read_text(encoding="utf-8") == "{\"primary\": \"#000\"}"


def _init_git_workspace_repo(tmp_path):
    repo = tmp_path / "workspace-remote"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, capture_output=True)

    (repo / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (repo / "files" / "projectA" / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "files" / "projectA" / "assets").mkdir(parents=True, exist_ok=True)
    (repo / "files" / "projectA" / "src" / "app.py").write_text("print('git')\n", encoding="utf-8")
    (repo / "files" / "projectA" / "docs" / "readme.md").write_text("# readme\n", encoding="utf-8")
    (repo / "files" / "projectA" / "assets" / "logo.bin").write_bytes(b"\x00PNG")

    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-ref",
            "refs/kdcube/demo-tenant/demo-project/admin-user/conversation-1/versions/turn_prev",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    return repo


@pytest.mark.asyncio
async def test_hydrate_workspace_paths_git_folder_pull_materializes_text_only(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_ctx",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        tenant="demo-tenant",
        project="demo-project",
        user_id="admin-user",
        conversation_id="conversation-1",
        workspace_implementation="git",
        workspace_git_repo=str(_init_git_workspace_repo(tmp_path)),
    )
    ctx = FakeBrowser(runtime)

    result = await hydrate_workspace_paths(
        ctx_browser=ctx,
        paths=["turn_prev/files/projectA"],
        outdir=outdir,
    )

    assert "turn_prev/files/projectA/src/app.py" in result["rehosted"]
    assert "turn_prev/files/projectA/docs/readme.md" in result["rehosted"]
    assert "turn_prev/files/projectA" not in result["missing"]
    assert not (outdir / "turn_prev" / "files" / "projectA" / "assets" / "logo.bin").exists()
    assert (outdir / "turn_prev" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == "print('git')\n"


@pytest.mark.asyncio
async def test_hydrate_workspace_paths_git_exact_binary_pull_materializes_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_ctx",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        tenant="demo-tenant",
        project="demo-project",
        user_id="admin-user",
        conversation_id="conversation-1",
        workspace_implementation="git",
        workspace_git_repo=str(_init_git_workspace_repo(tmp_path)),
    )
    ctx = FakeBrowser(runtime)

    result = await hydrate_workspace_paths(
        ctx_browser=ctx,
        paths=["turn_prev/files/projectA/assets/logo.bin"],
        outdir=outdir,
    )

    assert result["missing"] == []
    assert result["errors"] == []
    assert result["rehosted"] == ["turn_prev/files/projectA/assets/logo.bin"]
    assert (outdir / "turn_prev" / "files" / "projectA" / "assets" / "logo.bin").read_bytes() == b"\x00PNG"
