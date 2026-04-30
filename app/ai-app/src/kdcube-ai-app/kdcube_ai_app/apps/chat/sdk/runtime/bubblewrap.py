# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/sdk/runtime/bubblewrap.py

import os
import pathlib
import shutil
import sys
import asyncio

def bwrap_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

def _split_paths(s: str | None) -> list[str]:
    if not s:
        return []
    return [p for p in s.split(os.pathsep) if p]

def _ensure_sanitized_etc(*, uid: int, gid: int) -> pathlib.Path:
    root = pathlib.Path("/tmp/kdcube-supervisor/sandbox-etc")
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root.parent, 0o700)
    os.chmod(root, 0o755)
    (root / "passwd").write_text(
        "root:x:0:0:root:/root:/usr/sbin/nologin\n"
        f"executor:x:{uid}:{gid}:executor:/workspace/out:/usr/sbin/nologin\n",
        encoding="utf-8",
    )
    (root / "group").write_text(
        "root:x:0:\n"
        f"executor:x:{gid}:executor\n",
        encoding="utf-8",
    )
    (root / "hosts").write_text(
        "127.0.0.1 localhost\n"
        "::1 localhost ip6-localhost ip6-loopback\n",
        encoding="utf-8",
    )
    (root / "resolv.conf").write_text("", encoding="utf-8")
    for child in root.iterdir():
        if child.is_file():
            os.chmod(child, 0o644)
    return root


def _add_if_exists(argv: list[str], flag: str, src: str, dest: str | None = None) -> None:
    if os.path.exists(src):
        argv += [flag, src, dest or src]


def _default_library_path() -> str:
    candidates = (
        "/usr/local/lib",
        "/usr/lib",
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/lib",
        "/lib/aarch64-linux-gnu",
        "/lib/x86_64-linux-gnu",
        "/opt/venv/lib",
    )
    return os.pathsep.join(path for path in candidates if os.path.isdir(path))


def mk_bwrap_argv(*,
                  host_cwd: pathlib.Path,
                  entry_path: pathlib.Path,
                  env: dict,
                  net_enabled: bool,
                  host_outdir: pathlib.Path,
                  uid: int | None = None,
                  gid: int | None = None) -> list[str]:
    uid = int(os.environ.get("EXECUTOR_UID", "1001")) if uid is None else uid
    gid = int(os.environ.get("EXECUTOR_GID", "1000")) if gid is None else gid
    argv = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--uid", str(uid),
        "--gid", str(gid),
        "--clearenv",
        "--setenv", "PATH", env.get("PATH", "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"),
        "--setenv", "PYTHONPATH", env.get("PYTHONPATH", ""),
        "--setenv", "LD_LIBRARY_PATH", env.get("LD_LIBRARY_PATH", _default_library_path()),
        "--setenv", "HOME", env.get("HOME", "/workspace/out"),
        "--setenv", "LANG", env.get("LANG", "C.UTF-8"),
        "--setenv", "LC_ALL", env.get("LC_ALL", "C.UTF-8"),
        "--setenv", "TZ", env.get("TZ", "UTC"),
        "--setenv", "PYTHONUNBUFFERED", env.get("PYTHONUNBUFFERED", "1"),
        "--setenv", "WORKDIR", str(host_cwd),
        "--setenv", "OUTPUT_DIR", str(host_outdir),
        "--setenv", "AGENT_IO_CONTEXT", env.get("AGENT_IO_CONTEXT", "limited"),
        "--setenv", "EXECUTION_SANDBOX", env.get("EXECUTION_SANDBOX", "docker"),
        "--setenv", "EXECUTION_MODE", env.get("EXECUTION_MODE", "TOOL"),
        "--setenv", "EXECUTION_ID", env.get("EXECUTION_ID", ""),
        "--setenv", "EXEC_NO_UNEXPECTED_EXIT", env.get("EXEC_NO_UNEXPECTED_EXIT", "1"),
        "--setenv", "RESULT_FILENAME", env.get("RESULT_FILENAME", "result.json"),
        "--setenv", "LOG_DIR", env.get("LOG_DIR", str(host_outdir / "logs")),
        "--setenv", "LOG_FILE_PREFIX", env.get("LOG_FILE_PREFIX", "executor"),
        "--setenv", "LOG_LEVEL", env.get("LOG_LEVEL", "INFO"),
        "--setenv", "LOG_FORMAT", env.get("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        "--setenv", "LOG_MAX_MB", env.get("LOG_MAX_MB", "10"),
        "--setenv", "LOG_BACKUP_COUNT", env.get("LOG_BACKUP_COUNT", "3"),
        "--setenv", "EXEC_USER_LOG_MODE", env.get("EXEC_USER_LOG_MODE", "include_logging"),
        "--setenv", "RUNTIME_TOOL_MODULES", env.get("RUNTIME_TOOL_MODULES", "[]"),
        "--setenv", "RUNTIME_SHUTDOWN_MODULES", env.get("RUNTIME_SHUTDOWN_MODULES", "[]"),
        "--setenv", "SUPERVISOR_AUTH_TOKEN", env.get("SUPERVISOR_AUTH_TOKEN", ""),
        "--setenv", "MPLCONFIGDIR", env.get("MPLCONFIGDIR", str(host_outdir / ".mplconfig")),
        "--setenv", "XDG_CACHE_HOME", env.get("XDG_CACHE_HOME", str(host_outdir)),
        "--setenv", "XDG_CONFIG_HOME", env.get("XDG_CONFIG_HOME", str(host_outdir)),
        "--setenv", "FONTCONFIG_PATH", env.get("FONTCONFIG_PATH", str(host_outdir / ".fontconfig")),
        "--setenv", "MPLBACKEND", env.get("MPLBACKEND", "Agg"),
        "--setenv", "PLAYWRIGHT_BROWSERS_PATH", env.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright"),
        "--setenv", "SSL_CERT_DIR", env.get("SSL_CERT_DIR", "/etc/ssl/certs"),
        "--setenv", "SSL_CERT_FILE", env.get("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt"),
        "--setenv", "REQUESTS_CA_BUNDLE", env.get("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt"),
    ]

    # Network isolation
    if not net_enabled:
        argv += ["--unshare-net"]

    argv += [
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/etc",
        "--dir", "/proc",
    ]

    sanitized_etc = _ensure_sanitized_etc(uid=uid, gid=gid)
    for name in ("passwd", "group", "hosts", "resolv.conf"):
        argv += ["--ro-bind", str(sanitized_etc / name), f"/etc/{name}"]
    if os.path.exists("/etc/ssl/certs"):
        argv += ["--dir", "/etc/ssl", "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs"]

    # Keep only runtime dependencies and the explicit workspace/output mounts visible.
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt/venv", "/opt/app", "/opt/ms-playwright"):
        _add_if_exists(argv, "--ro-bind", path)

    # Safe non-secret system config needed by common rendering/font libraries.
    if os.path.exists("/etc/fonts"):
        argv += ["--dir", "/etc/fonts"]
        argv += ["--ro-bind", "/etc/fonts", "/etc/fonts"]

    argv += [
        "--bind", str(host_cwd), str(host_cwd),
        "--bind", str(host_outdir), str(host_outdir),
    ]

    socket_path = env.get("SUPERVISOR_SOCKET_PATH")
    if socket_path and os.path.exists(socket_path):
        argv += ["--bind", socket_path, socket_path]
        argv += ["--setenv", "SUPERVISOR_SOCKET_PATH", socket_path]

    # PYTHONPATH entries ro-bound at same paths when they are outside the app/runtime roots.
    for p in _split_paths(env.get("PYTHONPATH")):
        if not p:
            continue
        if not p.startswith("/"):
            continue
        if not os.path.exists(p):
            continue
        if os.path.abspath(p) in {os.path.abspath(str(host_cwd)), "/workspace", "/opt/app"}:
            continue
        argv += ["--ro-bind", p, p]

    argv += ["--chdir", str(host_outdir)]
    argv += [sys.executable, "-u", str(entry_path)]
    return argv


async def run_proc(entry_path: pathlib.Path, *, cwd: pathlib.Path, env: dict, outdir: pathlib.Path, sandbox_net: bool = True):
    """
    Environment toggles (per-call via env, or global via os.environ):
      - SANDBOX_FS: "1" (default) -> enable FS sandbox if bwrap present
                     "0"          -> disable sandbox
      - SANDBOX_NET: "1" (default) -> network allowed
                      "0"          -> network disabled (--unshare-net)
    """
    # Child env as seen inside sandbox
    env_sb = dict(env)

    # Inside sandbox, WORKDIR is /workspace; rewrite it
    env_sb["WORKDIR"] = "/workspace"

    # Rewrite PYTHONPATH entries that equal the host workdir -> /workspace
    py_paths = _split_paths(env_sb.get("PYTHONPATH"))
    if py_paths:
        host_cwd_abs = os.path.abspath(str(cwd))
        new_paths: list[str] = []
        for p in py_paths:
            if os.path.abspath(p) == host_cwd_abs:
                new_paths.append("/workspace")
            else:
                new_paths.append(p)
        env_sb["PYTHONPATH"] = os.pathsep.join(new_paths)

    argv = mk_bwrap_argv(
        host_cwd=cwd,
        entry_path=entry_path,
        env=env_sb,
        net_enabled=sandbox_net,
        host_outdir=outdir,
    )

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),  # launcher cwd; bwrap itself will chdir to /workspace
        env=env_sb,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    return proc
