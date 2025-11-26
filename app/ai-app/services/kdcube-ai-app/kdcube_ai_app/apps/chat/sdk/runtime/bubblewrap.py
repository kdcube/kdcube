# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/sdk/runtime/bubblewrap.py

import shutil, sys, os, pathlib, asyncio

def bwrap_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

def _split_paths(s: str | None) -> list[str]:
    if not s:
        return []
    return [p for p in s.split(os.pathsep) if p]

def mk_bwrap_argv(*,
                  host_cwd: pathlib.Path,
                  entry_filename: str,
                  env: dict,
                  net_enabled: bool,
                  host_outdir: pathlib.Path) -> list[str]:
    argv = ["bwrap"]

    # Network isolation
    if not net_enabled:
        argv += ["--unshare-net"]

    # Minimal system; make it platform-aware-ish and safe
    # /dev always exists on both macOS and Linux
    argv += ["--dev-bind", "/dev", "/dev"]

    # /proc is a synthetic mount; bubblewrap creates it (do NOT check os.path.exists)
    argv += ["--proc", "/proc"]

    # Build list of host paths to ro-bind, then only bind ones that exist
    system_ro = ["/usr", "/bin", "/sbin", "/etc"]

    # Linux-specific library dirs
    if sys.platform.startswith("linux"):
        system_ro += ["/lib", "/lib64"]

    # Optional: on macOS, /System is important, but since we won't use bwrap there,
    # it's mostly academic. If you ever did, you'd add:
    # if sys.platform == "darwin":
    #     system_ro.append("/System")

    for path in system_ro:
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]

    # /tmp as tmpfs inside sandbox
    argv += ["--tmpfs", "/tmp"]

    # Workdir: rw at /workspace
    argv += ["--bind", str(host_cwd), "/workspace", "--chdir", "/workspace"]

    # OUTPUT_DIR: rw at its absolute path
    if host_outdir and host_outdir.is_absolute():
        argv += ["--bind", str(host_outdir), str(host_outdir)]

    # PYTHONPATH entries ro-bound at same paths
    for p in _split_paths(env.get("PYTHONPATH")):
        if not p:
            continue
        if not p.startswith("/"):
            continue
        if not os.path.exists(p):
            continue

        # Skip host_cwd and /workspace (we already bind host_cwd -> /workspace)
        if os.path.abspath(p) in (
                os.path.abspath(str(host_cwd)),
                "/workspace",
        ):
            continue

        argv += ["--ro-bind", p, p]

    # Finally: run Python on main.py inside /workspace
    argv += [sys.executable, "-u", entry_filename]
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
        entry_filename=str(entry_path.name),
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

