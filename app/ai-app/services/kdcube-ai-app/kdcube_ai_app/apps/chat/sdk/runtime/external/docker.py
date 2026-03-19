# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/docker/docker.py

import asyncio
import re
import datetime as _dt
import os
import pathlib
import time
from typing import Dict, Any, Optional

from dotenv import find_dotenv, load_dotenv

from kdcube_ai_app.apps.chat.sdk.runtime.external.detect_aws_env import check_and_apply_cloud_environment
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import build_external_exec_env
from kdcube_ai_app.apps.chat.sdk.runtime.external.service_discovery import CONTAINER_BUNDLES_ROOT, _path, \
    _translate_container_path_to_host, _is_running_in_docker, _resolve_redis_url_for_container
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.infra.config import (
    build_external_runtime_base_env,
    prepare_external_runtime_globals,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings

_DEFAULT_IMAGE = os.environ.get("PY_CODE_EXEC_IMAGE", "py-code-exec:latest")
_DEFAULT_TIMEOUT_S = int(os.environ.get("PY_CODE_EXEC_TIMEOUT", "600"))  # 10min default

_PROC_VISIBLE_ROOTS = (
    "/exec-workspace",
    "/bundles",
    "/bundle-storage",
    "/kdcube-storage",
    "/tmp",
)


def _log_path_translation_context(log: AgentLogger) -> None:
    host_bundles = os.environ.get("HOST_BUNDLES_PATH")
    host_exec_workspace = os.environ.get("HOST_EXEC_WORKSPACE_PATH")
    host_bundle_storage = os.environ.get("HOST_BUNDLE_STORAGE_PATH")
    log.log(
        "[docker.exec] path translation env "
        f"HOST_BUNDLES_PATH={host_bundles or '<unset>'} "
        f"HOST_EXEC_WORKSPACE_PATH={host_exec_workspace or '<unset>'} "
        f"HOST_BUNDLE_STORAGE_PATH={host_bundle_storage or '<unset>'}",
        level="INFO",
    )
    if not host_bundles:
        log.log(
            "[docker.exec] HOST_BUNDLES_PATH is unset; bundle mounts assume the host sees the same /bundles path as proc. "
            "That is often false for Docker-in-Docker on ECS.",
            level="WARNING",
        )
    if not host_exec_workspace:
        log.log(
            "[docker.exec] HOST_EXEC_WORKSPACE_PATH is unset; workdir/outdir mounts assume the host sees the same /exec-workspace path as proc. "
            "That is often false for Docker-in-Docker on ECS.",
            level="WARNING",
        )


def _resolved_path(path: pathlib.Path | str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    try:
        return p.resolve()
    except Exception:
        return p


def _is_relative_to(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _iter_locally_visible_mount_roots() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for raw_root in _PROC_VISIBLE_ROOTS:
        root = _resolved_path(raw_root)
        if root.exists():
            roots.append(root)

    for env_name in (
        "HOST_KDCUBE_STORAGE_PATH",
        "HOST_BUNDLES_PATH",
        "HOST_BUNDLE_STORAGE_PATH",
        "HOST_EXEC_WORKSPACE_PATH",
    ):
        raw_value = os.environ.get(env_name)
        if not raw_value:
            continue
        root = _resolved_path(raw_value)
        if root.exists():
            roots.append(root)

    unique_roots: list[pathlib.Path] = []
    seen: set[str] = set()
    for root in roots:
        marker = str(root)
        if marker in seen:
            continue
        seen.add(marker)
        unique_roots.append(root)
    return unique_roots


def _can_preflight_translated_host_path(path: pathlib.Path) -> bool:
    if not _is_running_in_docker():
        return True

    resolved = _resolved_path(path)
    if resolved.exists():
        return True

    for root in _iter_locally_visible_mount_roots():
        if _is_relative_to(resolved, root):
            return True
    return False


def _bundle_tool_mount_checks(
        *,
        tool_module_files: Dict[str, Any],
        proc_bundle_root: pathlib.Path | None,
        checked_bundle_root: pathlib.Path | None,
        checked_bundle_label: str,
) -> list[str]:
    problems: list[str] = []
    if proc_bundle_root is None or checked_bundle_root is None:
        return problems
    proc_bundle_root = _resolved_path(proc_bundle_root)
    checked_bundle_root = _resolved_path(checked_bundle_root)
    for alias, raw_path in (tool_module_files or {}).items():
        if not raw_path or not isinstance(raw_path, str):
            continue
        try:
            tool_path = _resolved_path(raw_path)
            rel = tool_path.relative_to(proc_bundle_root)
        except Exception:
            continue
        checked_tool_path = checked_bundle_root / rel
        if not checked_tool_path.exists():
            problems.append(
                f"{checked_bundle_label} for tool alias '{alias}' does not exist: {checked_tool_path}"
            )
    return problems


def _docker_mount_preflight(
        *,
        log: AgentLogger,
        proc_workdir: pathlib.Path,
        proc_outdir: pathlib.Path,
        host_workdir: pathlib.Path,
        host_outdir: pathlib.Path,
        proc_bundle_root: pathlib.Path | None,
        host_bundle_root: pathlib.Path | None,
        raw_tool_module_files: Dict[str, Any],
) -> list[str]:
    problems: list[str] = []

    proc_workdir = _resolved_path(proc_workdir)
    proc_outdir = _resolved_path(proc_outdir)
    host_workdir = _resolved_path(host_workdir)
    host_outdir = _resolved_path(host_outdir)
    proc_bundle_root = _resolved_path(proc_bundle_root) if proc_bundle_root is not None else None
    host_bundle_root = _resolved_path(host_bundle_root) if host_bundle_root is not None else None

    if not proc_workdir.exists():
        problems.append(f"proc workdir does not exist: {proc_workdir}")
    main_py = proc_workdir / "main.py"
    if not main_py.exists():
        problems.append(f"proc workdir does not contain main.py: {main_py}")
    if not proc_outdir.exists():
        problems.append(f"proc outdir does not exist: {proc_outdir}")
    if proc_bundle_root is not None:
        if not proc_bundle_root.exists():
            problems.append(f"proc bundle root does not exist: {proc_bundle_root}")
        else:
            problems.extend(
                _bundle_tool_mount_checks(
                    tool_module_files=raw_tool_module_files,
                    proc_bundle_root=proc_bundle_root,
                    checked_bundle_root=proc_bundle_root,
                    checked_bundle_label="proc bundle path",
                )
            )

    if host_workdir != proc_workdir:
        if _can_preflight_translated_host_path(host_workdir):
            if not host_workdir.exists():
                problems.append(f"translated host workdir does not exist: {host_workdir}")
            host_main_py = host_workdir / "main.py"
            if not host_main_py.exists():
                problems.append(f"translated host workdir does not contain main.py: {host_main_py}")
        else:
            log.log(
                f"[docker.exec] translated host workdir is not locally visible from proc; "
                f"skipping local existence check: {host_workdir}",
                level="INFO",
            )

    if host_outdir != proc_outdir:
        if _can_preflight_translated_host_path(host_outdir):
            if not host_outdir.exists():
                problems.append(f"translated host outdir does not exist: {host_outdir}")
        else:
            log.log(
                f"[docker.exec] translated host outdir is not locally visible from proc; "
                f"skipping local existence check: {host_outdir}",
                level="INFO",
            )

    if host_bundle_root is not None and proc_bundle_root is not None and host_bundle_root != proc_bundle_root:
        if _can_preflight_translated_host_path(host_bundle_root):
            if not host_bundle_root.exists():
                problems.append(f"translated host bundle root does not exist: {host_bundle_root}")
            else:
                problems.extend(
                    _bundle_tool_mount_checks(
                        tool_module_files=raw_tool_module_files,
                        proc_bundle_root=proc_bundle_root,
                        checked_bundle_root=host_bundle_root,
                        checked_bundle_label="translated host bundle path",
                    )
                )
        else:
            log.log(
                f"[docker.exec] translated host bundle root is not locally visible from proc; "
                f"skipping local existence check: {host_bundle_root}",
                level="INFO",
            )
    return problems

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
        ]

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

    base_env = build_external_runtime_base_env(os.environ)
    # Add any extra_env passed in
    if extra_env:
        base_env.update(extra_env)

    # Mark executor Redis clients for easy attribution in CLIENT LIST
    base_env.setdefault("REDIS_CLIENT_NAME", "exec")

    redis_url = base_env.get("REDIS_URL") or get_settings().REDIS_URL
    resolved_redis_url = _resolve_redis_url_for_container(redis_url, logger=log)
    base_env["REDIS_URL"] = resolved_redis_url

    # Auto-detect cloud environment and get credentials if needed
    check_and_apply_cloud_environment(base_env, log)

    workdir = workdir.resolve()
    outdir = outdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    raw_tool_module_files = dict(runtime_globals.get("TOOL_MODULE_FILES") or {})
    proc_bundle_root: pathlib.Path | None = bundle_root.resolve() if bundle_root is not None else None

    exec_id = (extra_env or {}).get("EXECUTION_ID") or runtime_globals.get("EXECUTION_ID") or runtime_globals.get("RESULT_FILENAME")
    if not exec_id:
        exec_id = f"run-{int(time.time() * 1000)}"
    base_env["EXECUTION_ID"] = exec_id

    # This will be the *directory name* under /bundles in the container
    bundle_dir: Optional[str] = None

    bundle_spec = dict(runtime_globals.get("BUNDLE_SPEC") or {})
    if bundle_root is not None:
        bundle_root = bundle_root.resolve()
        module_name = bundle_spec.get("module") or ""
        module_first_segment = module_name.split(".", 1)[0] if module_name else None

        # 🔹 Single source of truth for container dir name under /bundles
        #    Prefer first segment of module name; fall back to id; then to directory name.
        bundle_dir = module_first_segment or bundle_spec.get("id") or bundle_root.name
    elif bundle_spec:
        module_name = bundle_spec.get("module") or ""
        module_first_segment = module_name.split(".", 1)[0] if module_name else None
        bundle_dir = module_first_segment or bundle_spec.get("id")

    container_bundle_root = f"{CONTAINER_BUNDLES_ROOT}/{bundle_dir}" if bundle_dir else None
    runtime_globals = prepare_external_runtime_globals(
        runtime_globals,
        host_bundle_root=bundle_root,
        bundle_root=container_bundle_root,
        bundle_dir=bundle_dir,
        bundle_id=(bundle_spec.get("id") if isinstance(bundle_spec, dict) else None) or bundle_dir,
    )
    base_env = build_external_exec_env(
        base_env=base_env,
        runtime_globals=runtime_globals,
        tool_module_names=tool_module_names,
        exec_id=exec_id,
        sandbox=base_env.get("EXECUTION_SANDBOX") or "docker",
        log_file_prefix="supervisor",
        bundle_root=container_bundle_root,
        bundle_id=bundle_dir,
    )

    img = image or _DEFAULT_IMAGE
    to = timeout_s or _DEFAULT_TIMEOUT_S

    # Translate paths for Docker-in-Docker
    host_workdir = _translate_container_path_to_host(workdir)
    host_outdir = _translate_container_path_to_host(outdir)

    if bundle_root is not None:
        bundle_root = _translate_container_path_to_host(bundle_root)

    if _is_running_in_docker():
        log.log(f"[docker.exec] Running in Docker-in-Docker mode", level="INFO")
        _log_path_translation_context(log)
    log.log(f"[docker.exec] Container paths: workdir={workdir}, outdir={outdir}")
    log.log(f"[docker.exec] Host paths: workdir={host_workdir}, outdir={host_outdir}")
    if proc_bundle_root is not None:
        log.log(f"[docker.exec] Bundle paths: container={proc_bundle_root}, host={bundle_root}", level="INFO")

    preflight_problems = _docker_mount_preflight(
        log=log,
        proc_workdir=workdir,
        proc_outdir=outdir,
        host_workdir=host_workdir,
        host_outdir=host_outdir,
        proc_bundle_root=proc_bundle_root,
        host_bundle_root=bundle_root,
        raw_tool_module_files=raw_tool_module_files,
    )
    if preflight_problems:
        message = "; ".join(preflight_problems)
        log.log(f"[docker.exec] mount preflight failed: {message}", level="ERROR")
        return {
            "ok": False,
            "returncode": 127,
            "error": f"host_mount_error: {message}",
            "stderr_tail": "\n".join(preflight_problems),
            "error_summary": preflight_problems[0],
        }

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

    sanitized_argv = []
    skip_next = False
    for idx, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue

        sanitized_argv.append(arg)
        if arg == "-e" and idx + 1 < len(argv):
            env_val = argv[idx + 1]
            if "=" in env_val:
                key = env_val.split("=", 1)[0]
                sanitized_argv.append(f"{key}=******")
            else:
                sanitized_argv.append(env_val)
            skip_next = True

    log.log(f"[docker.exec] Running: {' '.join(sanitized_argv)}")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    out: bytes = b""
    err: bytes = b""
    timed_out = False
    stderr_tail = ""
    error_summary = ""

    async def _terminate_proc_for_shutdown() -> tuple[bytes, bytes]:
        try:
            proc.terminate()
        except ProcessLookupError:
            return b"", b""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return await proc.communicate()

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=to)
    except asyncio.CancelledError:
        log.log("[docker.exec] Cancelled while waiting for container; terminating child process", level="WARNING")
        out2, err2 = await _terminate_proc_for_shutdown()
        out += out2
        err += err2
        raise
    except asyncio.TimeoutError:
        timed_out = True
        out2, err2 = await _terminate_proc_for_shutdown()
        out += out2
        err += err2

    # Persist Docker-level stdout/err under logs/
    try:
        log_dir = outdir / "logs"
        out_path = log_dir / "docker.out.log"
        err_path = log_dir / "docker.err.log"
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
            stderr_tail = tail
            if err_txt:
                for line in err_txt.splitlines():
                    if re.search(r"\\b\\w+Error\\b", line) or "Exception" in line:
                        error_summary = line.strip()
                        break
            if timed_out and not error_summary:
                error_summary = f"timeout after {to}s"
            if error_summary:
                diag = f"[docker] ERROR: {error_summary}\n".encode("utf-8")
                with open(err_path, "ab") as f:
                    f.write(diag)
    except Exception:
        # Best-effort only
        pass

    if timed_out:
        log.log(f"[docker.exec] Timeout after {to}s", level="ERROR")
        return {
            "error": "timeout",
            "seconds": to,
            "stderr_tail": stderr_tail,
            "error_summary": error_summary,
        }

    rc = proc.returncode
    ok = (rc == 0)
    if not ok:
        log.log(f"[docker.exec] Container exited with {rc}", level="ERROR")
        if error_summary:
            log.log(f"[docker.exec] stderr summary: {error_summary}", level="ERROR")
        elif stderr_tail:
            log.log(f"[docker.exec] stderr tail: {stderr_tail[-1000:]}", level="ERROR")

    return {
        "ok": ok,
        "returncode": rc,
        "stderr_tail": stderr_tail,
        "error_summary": error_summary,
    }
