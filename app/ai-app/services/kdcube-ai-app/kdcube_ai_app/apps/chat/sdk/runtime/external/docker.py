# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/docker/docker.py

import asyncio
import datetime as _dt
import json
import os
import pathlib
import time
from typing import Dict, Any, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.external.detect_aws_env import check_and_apply_cloud_environment
from kdcube_ai_app.apps.chat.sdk.runtime.external.service_discovery import CONTAINER_BUNDLES_ROOT, _path, \
    _translate_container_path_to_host, _is_running_in_docker, _resolve_redis_url_for_container
from kdcube_ai_app.apps.chat.sdk.runtime.isolated.environment import filter_host_environment
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.config import get_settings

_DEFAULT_IMAGE = os.environ.get("PY_CODE_EXEC_IMAGE", "py-code-exec:latest")
_DEFAULT_TIMEOUT_S = int(os.environ.get("PY_CODE_EXEC_TIMEOUT", "600"))  # 10min default

def _build_docker_argv(
        *,
        image: str,
        host_workdir: pathlib.Path,
        host_outdir: pathlib.Path,
        extra_env: Dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        bundle_root: pathlib.Path | None = None,
        bundle_id: str | None = None,
        network_mode: str | None = None,
) -> list[str]:
    """
    Build a `docker run` invocation that:
      - mounts host_workdir to /workspace/work
      - mounts host_outdir to /workspace/out
      - sets WORKDIR=/workspace/work, OUTPUT_DIR=/workspace/out
      - passes through extra_env as -e KEY=VAL
      - uses `image` as the container image
    """
    argv: list[str] = ["docker", "run", "--rm"]
    # CRITICAL: Allow network namespace creation (unshare) for isolation
    argv += ["--cap-add=SYS_ADMIN"]
    argv += ["--network", network_mode]

    # Optional extra args (e.g. --cpus, --memory) if you ever need them
    if extra_args:
        argv.extend(extra_args)

    # Bind mounts: host workdir/outdir -> /workspace/{work,out} in container
    argv += [
        # root FS read-only, only explicit mounts writable
        "--read-only",

        # main workspace, with two subdirs:
        "-v",
        f"{_path(host_workdir)}:/workspace/work:rw",
        "-v",
        f"{_path(host_outdir)}:/workspace/out:rw",

        # tmpfs for /tmp so Python etc. can still write temp files
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",

        # runtime dirs
        "-e",
        "WORKDIR=/workspace/work",
        "-e",
        "OUTPUT_DIR=/workspace/out",
    ]

    # Mount bundle root if provided
    if bundle_root is not None:
        subpath = f"{CONTAINER_BUNDLES_ROOT}/{bundle_id}" if bundle_id else CONTAINER_BUNDLES_ROOT
        argv += [
            "-v",
            f"{_path(bundle_root)}:{subpath}:ro",  # ← :ro is important
            "-e",
            f"BUNDLE_ROOT={subpath}",
        ]
        if bundle_id:
            argv += ["-e", f"BUNDLE_ID={bundle_id}"]

    # Propagate selected host env if you want (keys can be tuned)
    # For now, only pass explicit extra_env to keep it deterministic.
    for k, v in (extra_env or {}).items():
        # Avoid overriding WORKDIR/OUTPUT_DIR accidentally
        if k in {"WORKDIR", "OUTPUT_DIR"}:
            continue
        argv += ["-e", f"{k}={v}"]

    # Finally the image (command/entrypoint is defined in the Dockerfile)
    argv.append(image)
    return argv

async def run_py_in_docker(
        *,
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        runtime_globals: Dict[str, Any],
        tool_module_names: list[str],
        logger: Optional[AgentLogger] = None,
        image: Optional[str] = None,
        timeout_s: Optional[int] = None,
        extra_env: Optional[Dict[str, str]] = None,
        bundle_root: Optional[pathlib.Path] = None,
        extra_docker_args: Optional[list[str]] = None,
        network_mode: Optional[str] = None
) -> Dict[str, Any]:
    """
    High-level helper used by iso_runtime / ReAct execution:

    - `workdir` & `outdir` are host paths that you already prepared
      (main.py, etc.). They will be bind-mounted into the container.
    - `runtime_globals` is exactly the dict you currently pass as `globals`
      to _InProcessRuntime.execute_py_code:
        { "CONTRACT": ..., "COMM_SPEC": ..., "PORTABLE_SPEC_JSON": ...,
          "TOOL_ALIAS_MAP": ..., "TOOL_MODULE_FILES": ..., "SANDBOX_FS": ..., ... }
    - `tool_module_names` is the list of tool module names
      (e.g. from ToolSubsystem.tool_modules_tuple_list(): [name for name, _ in ...])

    Inside the container, the entrypoint will:
      - read RUNTIME_GLOBALS_JSON & RUNTIME_TOOL_MODULES from env
      - call iso_runtime.run_py_code() which recreates the previous behavior
        (header injection, bootstrap, writing result.json, runtime logs, etc.)
    """
    log = logger or AgentLogger("docker.exec")

    base_env = filter_host_environment(os.environ.copy())
    # Add any extra_env passed in
    if extra_env:
        base_env.update(extra_env)

    redis_url = base_env.get("REDIS_URL") or get_settings().REDIS_URL
    resolved_redis_url = _resolve_redis_url_for_container(redis_url, logger=log)
    base_env["REDIS_URL"] = resolved_redis_url

    # Auto-detect cloud environment and get credentials if needed
    check_and_apply_cloud_environment(base_env, log)

    workdir = workdir.resolve()
    outdir = outdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    exec_id = (extra_env or {}).get("EXECUTION_ID") or runtime_globals.get("EXECUTION_ID") or runtime_globals.get("RESULT_FILENAME")
    if not exec_id:
        exec_id = f"run-{int(time.time() * 1000)}"
    base_env["EXECUTION_ID"] = exec_id

    # This will be the *directory name* under /bundles in the container
    bundle_dir: Optional[str] = None

    # --- Rewrite TOOL_MODULE_FILES for container paths if bundle_root is provided ---
    if bundle_root is not None:
        bundle_root = bundle_root.resolve()
        host_bundle_root = str(bundle_root)

        # Runtime globals is what iso_runtime expects
        tg = dict(runtime_globals)  # shallow copy
        tmf = dict(tg.get("TOOL_MODULE_FILES") or {})

        bundle_spec = tg.get("BUNDLE_SPEC") or {}
        module_name = bundle_spec.get("module") or ""
        module_first_segment = module_name.split(".", 1)[0] if module_name else None

        # 🔹 Single source of truth for container dir name under /bundles
        #    Prefer first segment of module name; fall back to id; then to directory name.
        bundle_dir = module_first_segment or bundle_spec.get("id") or bundle_root.name

        # This is where the bundle_root will appear *inside the container*
        container_bundle_root = f"{CONTAINER_BUNDLES_ROOT}/{bundle_dir}"

        rewritten: Dict[str, Optional[str]] = {}
        for alias, path in tmf.items():
            if not path:
                rewritten[alias] = None
                continue
            p = str(path)
            if p.startswith(host_bundle_root):
                rel = os.path.relpath(p, host_bundle_root)
                rewritten[alias] = f"{container_bundle_root}/{rel}"
            else:
                # Paths outside the bundle root (e.g. site-packages) are importable by name
                rewritten[alias] = None

        tg["TOOL_MODULE_FILES"] = rewritten
        tg["BUNDLE_ID"] = bundle_spec.get("id") or bundle_dir
        tg["BUNDLE_DIR"] = bundle_dir
        tg["BUNDLE_ROOT_CONTAINER"] = container_bundle_root
        skills_desc = tg.get("SKILLS_DESCRIPTOR") or {}
        if isinstance(skills_desc, dict):
            csr = skills_desc.get("custom_skills_root")
            if isinstance(csr, str) and csr.startswith(host_bundle_root):
                rel = os.path.relpath(csr, host_bundle_root)
                skills_desc = dict(skills_desc)
                skills_desc["custom_skills_root"] = f"{container_bundle_root}/{rel}"
                tg["SKILLS_DESCRIPTOR"] = skills_desc

        runtime_globals = tg

    base_env["RUNTIME_GLOBALS_JSON"] = json.dumps(runtime_globals, ensure_ascii=False)
    base_env["RUNTIME_TOOL_MODULES"] = json.dumps(tool_module_names, ensure_ascii=False)
    base_env["WORKDIR"]               = "/workspace/work"
    base_env["OUTPUT_DIR"]            = "/workspace/out"
    base_env["LOG_DIR"]               = "/workspace/out/logs"
    base_env["LOG_FILE_PREFIX"]       = "supervisor"

    img = image or _DEFAULT_IMAGE
    to = timeout_s or _DEFAULT_TIMEOUT_S

    # Translate paths for Docker-in-Docker
    host_workdir = _translate_container_path_to_host(workdir)
    host_outdir = _translate_container_path_to_host(outdir)

    if bundle_root is not None:
        bundle_root = _translate_container_path_to_host(bundle_root)

    if _is_running_in_docker():
        log.log(f"[docker.exec] Running in Docker-in-Docker mode", level="INFO")
    log.log(f"[docker.exec] Container paths: workdir={workdir}, outdir={outdir}")
    log.log(f"[docker.exec] Host paths: workdir={host_workdir}, outdir={host_outdir}")

    argv = _build_docker_argv(
        image=img,
        host_workdir=host_workdir,
        host_outdir=host_outdir,
        extra_env=base_env,
        extra_args=extra_docker_args or [],
        bundle_root=bundle_root,
        bundle_id=bundle_dir,
        network_mode=network_mode or "host",
    )

    log.log(f"[docker.exec] Running: {' '.join(argv)}")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    out: bytes = b""
    err: bytes = b""
    timed_out = False

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=to)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            out2, err2 = await asyncio.wait_for(proc.communicate(), timeout=5)
            out += out2
            err += err2
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out2, err2 = await proc.communicate()
            out += out2
            err += err2

    # Persist Docker-level stdout/err under logs/
    try:
        log_dir = outdir / "logs"
        out_path = log_dir / "docker.out.log"
        err_path = log_dir / "docker.err.log"
        errlog_path = log_dir / "errors.log"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        header = f"\n===== EXECUTION {exec_id} START {ts} =====\n".encode("utf-8")
        with open(out_path, "ab") as f:
            f.write(header)
            if out:
                f.write(out)
                if not out.endswith(b"\n"):
                    f.write(b"\n")
        with open(err_path, "ab") as f:
            f.write(header)
            if err:
                f.write(err)
                if not err.endswith(b"\n"):
                    f.write(b"\n")
        if timed_out or proc.returncode != 0:
            reason = "timeout" if timed_out else f"returncode={proc.returncode}"
            err_txt = err.decode("utf-8", errors="ignore")
            tail = err_txt[-4000:] if err_txt else ""
            with open(errlog_path, "ab") as f:
                f.write(header)
                f.write(f"[docker] {reason}\n".encode("utf-8"))
                if tail:
                    f.write(tail.encode("utf-8", errors="ignore"))
                    if not tail.endswith("\n"):
                        f.write(b"\n")
    except Exception:
        # Best-effort only
        pass

    if timed_out:
        log.log(f"[docker.exec] Timeout after {to}s", level="ERROR")
        return {"error": "timeout", "seconds": to}

    rc = proc.returncode
    ok = (rc == 0)
    if not ok:
        log.log(f"[docker.exec] Container exited with {rc}", level="ERROR")

    return {"ok": ok, "returncode": rc}
