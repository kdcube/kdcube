# SPDX-License-Identifier: MIT

from __future__ import annotations

import pathlib

from kdcube_ai_app.apps.chat.sdk.runtime import bubblewrap


def test_bwrap_argv_uses_sanitized_etc_and_preserves_supervisor_socket(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()
    entry = workdir / "main.py"
    entry.write_text("print('ok')", encoding="utf-8")
    socket_path = tmp_path / "supervisor.sock"
    socket_path.write_text("", encoding="utf-8")

    argv = bubblewrap.mk_bwrap_argv(
        host_cwd=workdir,
        entry_path=entry,
        env={
            "PATH": "/opt/venv/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/app",
            "SUPERVISOR_SOCKET_PATH": str(socket_path),
            "SUPERVISOR_AUTH_TOKEN": "secret-token",
            "EXECUTION_ID": "exec-1",
        },
        net_enabled=False,
        host_outdir=outdir,
    )

    joined = " ".join(argv)
    assert "--unshare-net" in argv
    assert "--unshare-user" in argv
    assert "--clearenv" in argv
    assert "LD_LIBRARY_PATH" in argv
    library_path = argv[argv.index("LD_LIBRARY_PATH") + 1]
    assert "/usr/local/lib" in library_path
    assert "--uid" in argv
    assert "1001" in argv
    assert "--gid" in argv
    assert "1000" in argv
    assert "--proc" not in argv
    assert "/etc/passwd" in argv
    assert "--ro-bind /etc /etc" not in joined
    assert str(socket_path) in argv
    assert "SUPERVISOR_AUTH_TOKEN" in argv
    assert "secret-token" in argv
    assert str(entry) in argv
