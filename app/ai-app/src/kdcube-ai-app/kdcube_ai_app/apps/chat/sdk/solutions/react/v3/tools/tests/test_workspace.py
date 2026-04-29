# SPDX-License-Identifier: MIT

import base64
import json
import subprocess

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.react.v3.git_workspace as v3_git_workspace
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace import (
    GitWorkspaceCommandError,
    checkout_current_turn_git_workspace,
    _ensure_local_version_ref,
    _ensure_workspace_repo,
    ensure_current_turn_git_workspace,
    publish_current_turn_git_workspace,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import (
    build_exec_snapshot_workspace,
    rehost_files_from_timeline,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.checkout import handle_react_checkout
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
async def test_rehost_outputs_from_timeline_exact_file(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.outputs/test_results.txt","physical_path":"turn_prev/outputs/test_results.txt"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.outputs/test_results.txt",
                "text": "ok\n",
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
        paths=["turn_prev/outputs/test_results.txt"],
        outdir=tmp_path,
    )

    assert "turn_prev/outputs/test_results.txt" in res.get("rehosted", [])
    assert (tmp_path / "turn_prev" / "outputs" / "test_results.txt").read_text(encoding="utf-8") == "ok\n"


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
            "refs/heads/kdcube/demo-tenant/demo-project/admin-user/conversation-1",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
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
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-ref",
            "refs/heads/kdcube/demo-tenant/demo-project/other-user/conversation-2",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-ref",
            "refs/kdcube/demo-tenant/demo-project/other-user/conversation-2/versions/turn_other",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    return repo


def test_build_exec_snapshot_workspace_copies_git_turn_root_when_repo_file_is_referenced(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    turn_root = outdir / "turn_ctx"
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(turn_root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    (turn_root / "files" / "projectA" / "src" / "app.py").write_text("print('ctx')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(turn_root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "commit", "-m", "init"], check=True, capture_output=True)

    ws = build_exec_snapshot_workspace(
        workdir=workdir,
        outdir=outdir,
        timeline={},
        code='from pathlib import Path\napp = Path(OUTPUT_DIR) / "turn_ctx/files/projectA/src/app.py"\nprint(app.read_text())\n',
    )

    snap_out = ws["outdir"]
    assert (snap_out / "turn_ctx" / ".git").exists()
    assert (snap_out / "turn_ctx" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == "print('ctx')\n"


def test_ensure_current_turn_git_workspace_bootstraps_lineage_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
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

    turn_root = ensure_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)

    assert (turn_root / ".git").exists()
    assert not (turn_root / "files" / "projectA" / "src" / "app.py").exists()
    proc_show = subprocess.run(
        ["git", "-C", str(turn_root), "show", "workspace:files/projectA/src/app.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (proc_show.stdout or "") == "print('git')\n"
    proc = subprocess.run(
        ["git", "-C", str(turn_root), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (proc.stdout or "").strip() == ""
    proc_remote = subprocess.run(
        ["git", "-C", str(turn_root), "remote"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (proc_remote.stdout or "").strip() == ""
    proc_refs = subprocess.run(
        ["git", "-C", str(turn_root), "show-ref"],
        check=True,
        capture_output=True,
        text=True,
    )
    refs_output = proc_refs.stdout or ""
    assert "other-user" not in refs_output
    lineage_repo = (
        outdir.parent
        / ".react_workspace_git"
        / "demo-tenant__demo-project__admin-user__conversation-1"
        / "lineage.git"
    )
    proc_lineage_refs = subprocess.run(
        ["git", "-C", str(lineage_repo), "show-ref"],
        check=True,
        capture_output=True,
        text=True,
    )
    lineage_refs_output = proc_lineage_refs.stdout or ""
    assert "refs/heads/workspace" in lineage_refs_output
    assert "other-user" not in lineage_refs_output


def test_publish_current_turn_git_workspace_pushes_lineage_and_version_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    remote_repo = _init_git_workspace_repo(tmp_path)
    runtime = RuntimeCtx(
        turn_id="turn_ctx",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        tenant="demo-tenant",
        project="demo-project",
        user_id="admin-user",
        conversation_id="conversation-1",
        workspace_implementation="git",
        workspace_git_repo=str(remote_repo),
    )

    turn_root = ensure_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "src" / "new.py").write_text("print('new')\n", encoding="utf-8")

    result = publish_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)

    assert result["version_ref"].endswith("/versions/turn_ctx")
    assert result["committed"] is True
    show_branch = subprocess.run(
        [
            "git",
            "-C",
            str(remote_repo),
            "show",
            "refs/heads/kdcube/demo-tenant/demo-project/admin-user/conversation-1:files/projectA/src/new.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (show_branch.stdout or "") == "print('new')\n"
    show_version = subprocess.run(
        [
            "git",
            "-C",
            str(remote_repo),
            "show",
            "refs/kdcube/demo-tenant/demo-project/admin-user/conversation-1/versions/turn_ctx:files/projectA/src/new.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (show_version.stdout or "") == "print('new')\n"


def test_v3_ensure_workspace_repo_rewrites_ssh_origin_to_https_when_pat_is_configured(tmp_path, monkeypatch):
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeCtx(
        turn_id="turn_ctx",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        tenant="tenant-a",
        project="project-a",
        user_id="user-a",
        conversation_id="conversation-a",
        workspace_implementation="git",
        workspace_git_repo="git@github.com:org/workspace.git",
    )
    monkeypatch.setenv("GIT_HTTP_TOKEN", "pat-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    monkeypatch.setattr(v3_git_workspace, "_build_git_env", lambda: {})

    repo_root = v3_git_workspace._ensure_workspace_repo(runtime_ctx=runtime, outdir=outdir)

    remote_url = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_url == "https://github.com/org/workspace.git"


def test_publish_current_turn_git_workspace_skips_transient_and_ignored_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    remote_repo = _init_git_workspace_repo(tmp_path)
    runtime = RuntimeCtx(
        turn_id="turn_ctx",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        tenant="demo-tenant",
        project="demo-project",
        user_id="admin-user",
        conversation_id="conversation-1",
        workspace_implementation="git",
        workspace_git_repo=str(remote_repo),
    )

    turn_root = ensure_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)
    (turn_root / ".gitignore").write_text(".ignored.txt\n", encoding="utf-8")
    (turn_root / "files" / "demo_proj").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "demo_proj" / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (turn_root / "files" / "demo_proj" / ".ignored.txt").write_text("skip\n", encoding="utf-8")
    (turn_root / "files" / "demo_proj" / ".pytest_cache" / "v" / "cache").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "demo_proj" / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")

    result = publish_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)

    assert result["committed"] is True
    show_license = subprocess.run(
        [
            "git",
            "-C",
            str(remote_repo),
            "show",
            "refs/heads/kdcube/demo-tenant/demo-project/admin-user/conversation-1:files/demo_proj/LICENSE",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (show_license.stdout or "") == "MIT\n"
    ignored_missing = subprocess.run(
        [
            "git",
            "-C",
            str(remote_repo),
            "show",
            "refs/heads/kdcube/demo-tenant/demo-project/admin-user/conversation-1:files/demo_proj/.ignored.txt",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored_missing.returncode != 0
    cache_missing = subprocess.run(
        [
            "git",
            "-C",
            str(remote_repo),
            "show",
            "refs/heads/kdcube/demo-tenant/demo-project/admin-user/conversation-1:files/demo_proj/.pytest_cache/README.md",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert cache_missing.returncode != 0


def test_checkout_current_turn_git_workspace_materializes_requested_version(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
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

    turn_root = ensure_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)
    assert not (turn_root / "files" / "projectA" / "src" / "app.py").exists()

    result = checkout_current_turn_git_workspace(
        runtime_ctx=runtime,
        outdir=outdir,
        version_id="turn_prev",
    )

    assert result["checked_out_version"] == "turn_prev"
    assert (turn_root / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == "print('git')\n"
    assert (turn_root / "files" / "projectA" / "docs" / "readme.md").read_text(encoding="utf-8") == "# readme\n"


def test_stage_current_turn_text_workspace_surfaces_git_stderr(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2 import git_workspace as mod

    turn_root = tmp_path / "turn_root"
    turn_root.mkdir(parents=True, exist_ok=True)

    real_run = mod.subprocess.run

    def _fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and str(turn_root) in cmd and cmd[-4:] == ["ls-files", "-z", "--", "."]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"files/a.txt\0", stderr=b"")
        if cmd and cmd[0] == "git" and str(turn_root) in cmd and cmd[-5:] == ["add", "--sparse", "-u", "--", "."]:
            return subprocess.CompletedProcess(
                cmd,
                128,
                stdout="",
                stderr="fatal: not a git repository",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    with pytest.raises(GitWorkspaceCommandError) as exc_info:
        mod._stage_current_turn_text_workspace(turn_root=turn_root)

    msg = str(exc_info.value)
    assert "stage tracked workspace updates failed" in msg
    assert "fatal: not a git repository" in msg


@pytest.mark.asyncio
async def test_react_checkout_rejects_nonempty_current_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
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
    turn_root = ensure_current_turn_git_workspace(runtime_ctx=runtime, outdir=outdir)
    (turn_root / "files" / "projectA").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "scratch.md").write_text("dirty\n", encoding="utf-8")

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "replace",
                    "version": "turn_prev",
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_checkout(ctx_browser=ctx, state=state, tool_call_id="checkout1")

    assert state.get("retry_decision") is True
    assert any((b.get("text") or "").startswith("react.checkout.nonempty:") for b in ctx.timeline.blocks if b.get("type") == "react.notice")


@pytest.mark.asyncio
async def test_react_checkout_materializes_requested_paths_into_current_turn_custom(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/projectA/src/app.py","physical_path":"turn_prev/files/projectA/src/app.py"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/projectA/src/app.py",
                "text": "print(\"old\")\n",
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/projectA/docs/readme.md","physical_path":"turn_prev/files/projectA/docs/readme.md"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/projectA/docs/readme.md",
                "text": "# readme\n",
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg

    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "replace",
                    "paths": ["fi:turn_prev.files/projectA"],
                }
            }
        },
        "outdir": str(tmp_path),
    }

    await handle_react_checkout(ctx_browser=ctx, state=state, tool_call_id="checkout_custom")

    assert (tmp_path / "turn_ctx" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == 'print("old")\n'
    assert (tmp_path / "turn_ctx" / "files" / "projectA" / "docs" / "readme.md").read_text(encoding="utf-8") == "# readme\n"
    checkout_events = [b for b in ctx.timeline.blocks if b.get("type") == "react.workspace.checkout"]
    assert checkout_events


@pytest.mark.asyncio
async def test_react_checkout_materializes_requested_paths_into_current_turn_git(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
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

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "replace",
                    "paths": ["fi:turn_prev.files/projectA"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_checkout(ctx_browser=ctx, state=state, tool_call_id="checkout_git")

    assert (outdir / "turn_ctx" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == "print('git')\n"
    assert (outdir / "turn_ctx" / "files" / "projectA" / "docs" / "readme.md").read_text(encoding="utf-8") == "# readme\n"
    payload = next(
        json.loads(b["text"])
        for b in ctx.timeline.blocks
        if b.get("type") == "react.workspace.checkout"
    )
    assert payload["mode"] == "replace"
    assert payload["checked_out_from"] == ["fi:turn_prev.files/projectA"]


@pytest.mark.asyncio
async def test_react_checkout_overlay_overwrites_selected_file_without_clearing_workspace(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/projectA/src/app.py","physical_path":"turn_prev/files/projectA/src/app.py"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/projectA/src/app.py",
                "text": "print(\"old\")\n",
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg

    cfg.get_settings = lambda: _Settings()

    current_root = tmp_path / "turn_ctx" / "files" / "projectA" / "src"
    current_root.mkdir(parents=True, exist_ok=True)
    (current_root / "app.py").write_text('print("new")\n', encoding="utf-8")
    (current_root / "extra.py").write_text('print("keep")\n', encoding="utf-8")

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "overlay",
                    "paths": ["fi:turn_prev.files/projectA/src/app.py"],
                }
            }
        },
        "outdir": str(tmp_path),
    }

    await handle_react_checkout(ctx_browser=ctx, state=state, tool_call_id="checkout_overlay")

    assert (current_root / "app.py").read_text(encoding="utf-8") == 'print("old")\n'
    assert (current_root / "extra.py").read_text(encoding="utf-8") == 'print("keep")\n'
    payload = next(
        json.loads(b["text"])
        for b in ctx.timeline.blocks
        if b.get("type") == "react.workspace.checkout"
    )
    assert payload["mode"] == "overlay"
    assert payload["checked_out_from"] == ["fi:turn_prev.files/projectA/src/app.py"]


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
async def test_hydrate_workspace_paths_git_dedupes_version_fetch_per_turn(tmp_path, monkeypatch):
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

    import kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace as gw

    original = gw._ensure_local_version_ref
    calls: list[str] = []

    def _wrapped(*, repo_root, runtime_ctx, version_id):
        calls.append(version_id)
        return original(repo_root=repo_root, runtime_ctx=runtime_ctx, version_id=version_id)

    monkeypatch.setattr(gw, "_ensure_local_version_ref", _wrapped)

    result = await hydrate_workspace_paths(
        ctx_browser=ctx,
        paths=[
            "turn_prev/files/projectA/src/app.py",
            "turn_prev/files/projectA/docs/readme.md",
        ],
        outdir=outdir,
    )

    assert result["errors"] == []
    assert calls == ["turn_prev"]
    assert (outdir / "turn_prev" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == "print('git')\n"
    assert (outdir / "turn_prev" / "files" / "projectA" / "docs" / "readme.md").read_text(encoding="utf-8") == "# readme\n"


def test_ensure_local_version_ref_skips_refetch_when_local_ref_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_HTTP_TOKEN", "test-token")
    monkeypatch.setenv("GIT_HTTP_USER", "x-access-token")
    outdir = tmp_path / "out"
    outdir.mkdir(parents=True, exist_ok=True)
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

    repo_root = _ensure_workspace_repo(runtime_ctx=runtime, outdir=outdir)
    local_ref = _ensure_local_version_ref(repo_root=repo_root, runtime_ctx=runtime, version_id="turn_prev")

    real_run = subprocess.run
    fetch_calls: list[list[str]] = []

    def _wrapped_run(args, *run_args, **run_kwargs):
        cmd = list(args)
        if cmd[:4] == ["git", "-C", str(repo_root), "fetch"]:
            fetch_calls.append(cmd)
        return real_run(args, *run_args, **run_kwargs)

    monkeypatch.setattr(subprocess, "run", _wrapped_run)

    local_ref_again = _ensure_local_version_ref(repo_root=repo_root, runtime_ctx=runtime, version_id="turn_prev")

    assert local_ref_again == local_ref
    assert fetch_calls == []


@pytest.mark.asyncio
async def test_hydrate_workspace_paths_git_exact_binary_pull_requires_hosting_artifact(tmp_path, monkeypatch):
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

    assert result["missing"] == ["turn_prev/files/projectA/assets/logo.bin"]
    assert result["errors"] == []
    assert result["rehosted"] == []
    assert not (outdir / "turn_prev" / "files" / "projectA" / "assets" / "logo.bin").exists()


@pytest.mark.asyncio
async def test_hydrate_workspace_paths_git_exact_binary_pull_falls_back_to_hosting_artifact(tmp_path, monkeypatch):
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
        workspace_git_repo="",
    )
    ctx = FakeBrowser(runtime)
    png_b64 = base64.b64encode(b"\x89PNG\r\n").decode("ascii")
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": (
                    '{"artifact_path":"fi:turn_prev.files/dev-lifecycle.png",'
                    '"physical_path":"turn_prev/files/dev-lifecycle.png",'
                    '"mime":"image/png","hosted_uri":"hosted://artifact/dev-lifecycle.png"}'
                ),
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "image/png",
                "path": "fi:turn_prev.files/dev-lifecycle.png",
                "base64": png_b64,
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    result = await hydrate_workspace_paths(
        ctx_browser=ctx,
        paths=["turn_prev/files/dev-lifecycle.png"],
        outdir=outdir,
    )

    assert result["errors"] == []
    assert result["missing"] == []
    assert result["rehosted"] == ["turn_prev/files/dev-lifecycle.png"]
    assert (outdir / "turn_prev" / "files" / "dev-lifecycle.png").read_bytes() == b"\x89PNG\r\n"
