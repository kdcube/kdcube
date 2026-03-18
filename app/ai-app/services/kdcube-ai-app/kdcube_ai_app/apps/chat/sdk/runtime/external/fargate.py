# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Fargate / distributed execution.

Flow:
- Snapshot workdir/outdir and upload to S3
- Rewrite TOOL_MODULE_FILES paths to container-local bundle paths
- Launch remote exec ECS task
- Poll until STOPPED or timeout
- Restore output zips back to caller directories
"""

from __future__ import annotations

import asyncio
import re
import json
import os
import pathlib
import time
import traceback
import weakref
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import (
    ExternalExecRequest,
    ExternalExecResult,
    ExternalRuntime,
    build_external_exec_env,
    format_size_summary,
    payload_size_bytes,
)
from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import (
    snapshot_exec_input,
    ensure_bundle_snapshot,
    restore_zip_to_dir,
    resolve_exec_snapshot_uri,
)
from kdcube_ai_app.apps.chat.sdk.runtime.external.payload_secret import (
    delete_exec_payload_secret,
    put_exec_payload_secret,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import build_exec_snapshot_workspace
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import resolve_exec_runtime_profile
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.config import (
    build_external_runtime_base_env,
    build_external_runtime_inline_env,
    prepare_external_runtime_globals,
)

# Container-side bundle root — exec task restores bundles here.
_CONTAINER_BUNDLES_ROOT = "/workspace/bundles"

# Serialize output merge when multiple agents run in parallel.
# WeakValueDictionary: locks are GC'd automatically once no coroutine holds a reference.
_MERGE_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def _merge_lock_for(turn_id: str) -> asyncio.Lock:
    tid = (turn_id or "").strip() or "_global"
    lock = _MERGE_LOCKS.get(tid)
    if lock is None:
        lock = asyncio.Lock()
        _MERGE_LOCKS[tid] = lock
    return lock


def _as_bool(val: Any) -> Optional[bool]:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return None


def _as_csv_list(val: Any) -> List[str]:
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    if isinstance(val, str):
        return [part.strip() for part in val.split(",") if part.strip()]
    return []


def _resolve_exec_runtime_config(runtime_globals: Dict[str, Any]) -> Dict[str, Any]:
    cfg = resolve_exec_runtime_profile(
        runtime=runtime_globals.get("EXEC_RUNTIME_CONFIG"),
        profile=None,
    )
    cfg = dict(cfg)
    nested = cfg.pop("fargate", None)
    if isinstance(nested, dict):
        merged = dict(cfg)
        merged.update(nested)
        return merged
    return cfg


def _pick_cfg(cfg: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return None


def _summarize_task_state(task: Dict[str, Any]) -> str:
    if not isinstance(task, dict):
        return "task=<missing>"
    parts: List[str] = []
    for key in ("taskArn", "desiredStatus", "lastStatus", "stopCode", "stoppedReason", "healthStatus"):
        value = task.get(key)
        if value:
            parts.append(f"{key}={value}")

    attachments = task.get("attachments") or []
    attachment_parts: List[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        kind = attachment.get("type") or "attachment"
        status = attachment.get("status") or "unknown"
        detail_parts: List[str] = []
        for detail in attachment.get("details") or []:
            if not isinstance(detail, dict):
                continue
            name = detail.get("name")
            value = detail.get("value")
            if name and value:
                detail_parts.append(f"{name}={value}")
        attachment_parts.append(
            f"{kind}:{status}" + (f"({', '.join(detail_parts)})" if detail_parts else "")
        )
    if attachment_parts:
        parts.append(f"attachments=[{' ; '.join(attachment_parts)}]")

    container_parts: List[str] = []
    for container in task.get("containers") or []:
        if not isinstance(container, dict):
            continue
        name = container.get("name") or "container"
        c_parts = [name]
        for key in ("lastStatus", "reason", "exitCode", "healthStatus"):
            value = container.get(key)
            if value is not None and value != "":
                c_parts.append(f"{key}={value}")
        container_parts.append(",".join(str(p) for p in c_parts))
    if container_parts:
        parts.append(f"containers=[{' ; '.join(container_parts)}]")

    return " ".join(parts) if parts else "task=<empty>"


class FargateRuntime(ExternalRuntime):
    async def run(self, request: ExternalExecRequest, *, logger: Optional[AgentLogger] = None) -> ExternalExecResult:
        runtime_globals = request.runtime_globals or {}
        exec_ctx = runtime_globals.get("EXEC_CONTEXT") or {}
        exec_id = (
            (request.extra_env or {}).get("EXECUTION_ID")
            or runtime_globals.get("EXECUTION_ID")
            or runtime_globals.get("RESULT_FILENAME")
            or "run"
        )
        payload_env = build_external_runtime_base_env(os.environ)
        inline_env = build_external_runtime_inline_env(os.environ)
        if request.extra_env:
            payload_env.update(request.extra_env)
            inline_env.update(request.extra_env)

        snapshot = None
        snapshot_workdir = request.workdir
        snapshot_outdir = request.outdir
        try:
            # Build lightweight snapshot workspace (timeline + referenced files)
            try:
                code_path = pathlib.Path(request.workdir) / "main.py"
                code_text = code_path.read_text(encoding="utf-8") if code_path.exists() else ""
            except Exception:
                code_text = ""
            timeline_payload = {}
            try:
                tl_path = pathlib.Path(request.outdir) / "timeline.json"
                if tl_path.exists():
                    timeline_payload = json.loads(tl_path.read_text(encoding="utf-8"))
            except Exception:
                timeline_payload = {}
            ws = build_exec_snapshot_workspace(
                workdir=pathlib.Path(request.workdir),
                outdir=pathlib.Path(request.outdir),
                timeline=timeline_payload,
                code=code_text,
            )
            snapshot_workdir = ws.get("workdir", snapshot_workdir)
            snapshot_outdir = ws.get("outdir", snapshot_outdir)
            snapshot = snapshot_exec_input(
                exec_ctx=exec_ctx,
                exec_id=str(exec_id),
                workdir=pathlib.Path(snapshot_workdir),
                outdir=pathlib.Path(snapshot_outdir),
                codegen_run_id=exec_ctx.get("codegen_run_id"),
            )
            runtime_globals["EXEC_SNAPSHOT"] = {
                "storage_uri": snapshot.storage_uri,
                "base_prefix": snapshot.base_prefix,
            }
        except Exception as e:
            if logger:
                logger.log(f"[fargate] Failed to snapshot exec input: {e}", level="ERROR")

        try:
            bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
            bundle_id = bundle_spec.get("id") if isinstance(bundle_spec, dict) else None
            bundle_version = bundle_spec.get("version") if isinstance(bundle_spec, dict) else None
            bundle_root = request.bundle_root
            if bundle_id and bundle_root and exec_ctx:
                b = ensure_bundle_snapshot(
                    tenant=exec_ctx.get("tenant") or exec_ctx.get("tenant_id") or "unknown",
                    project=exec_ctx.get("project") or exec_ctx.get("project_id") or "unknown",
                    bundle_id=str(bundle_id),
                    bundle_root=bundle_root,
                    bundle_version=str(bundle_version) if bundle_version else None,
                )
                runtime_globals["BUNDLE_SNAPSHOT_URI"] = b.bundle_uri
        except Exception as e:
            if logger:
                logger.log(f"[fargate] Failed to snapshot bundle: {e}", level="WARNING")

        try:
            import boto3  # type: ignore
        except Exception:
            if logger:
                logger.log("[fargate] boto3 not installed; cannot run ECS task", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="boto3_missing")

        exec_runtime_cfg = _resolve_exec_runtime_config(runtime_globals)
        enabled_override = _as_bool(_pick_cfg(exec_runtime_cfg, "enabled", "FARGATE_EXEC_ENABLED"))
        enabled = (
            enabled_override
            if enabled_override is not None
            else os.environ.get("FARGATE_EXEC_ENABLED", "0").lower() in {"1", "true", "yes"}
        )
        if not enabled:
            if logger:
                logger.log("[fargate] Distributed execution not enabled (FARGATE_EXEC_ENABLED=1)", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="fargate_disabled")

        cluster = str(_pick_cfg(exec_runtime_cfg, "cluster", "FARGATE_CLUSTER") or os.environ.get("FARGATE_CLUSTER") or "").strip()
        task_def = str(_pick_cfg(exec_runtime_cfg, "task_definition", "taskDefinition", "FARGATE_TASK_DEFINITION") or os.environ.get("FARGATE_TASK_DEFINITION") or "").strip()
        container_name = str(_pick_cfg(exec_runtime_cfg, "container_name", "containerName", "FARGATE_CONTAINER_NAME") or os.environ.get("FARGATE_CONTAINER_NAME") or "").strip()
        subnets = _as_csv_list(_pick_cfg(exec_runtime_cfg, "subnets", "FARGATE_SUBNETS")) or [s for s in (os.environ.get("FARGATE_SUBNETS") or "").split(",") if s.strip()]
        sec_groups = _as_csv_list(_pick_cfg(exec_runtime_cfg, "security_groups", "securityGroups", "FARGATE_SECURITY_GROUPS")) or [s for s in (os.environ.get("FARGATE_SECURITY_GROUPS") or "").split(",") if s.strip()]
        assign_public_ip = str(_pick_cfg(exec_runtime_cfg, "assign_public_ip", "assignPublicIp", "FARGATE_ASSIGN_PUBLIC_IP") or os.environ.get("FARGATE_ASSIGN_PUBLIC_IP", "DISABLED")).strip() or "DISABLED"
        launch_type = str(_pick_cfg(exec_runtime_cfg, "launch_type", "launchType", "FARGATE_LAUNCH_TYPE") or os.environ.get("FARGATE_LAUNCH_TYPE", "FARGATE")).strip() or "FARGATE"
        platform_version = str(_pick_cfg(exec_runtime_cfg, "platform_version", "platformVersion", "FARGATE_PLATFORM_VERSION") or os.environ.get("FARGATE_PLATFORM_VERSION") or "").strip() or None
        aws_region = str(
            _pick_cfg(exec_runtime_cfg, "region", "aws_region", "AWS_REGION")
            or payload_env.get("AWS_REGION")
            or payload_env.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or ""
        ).strip() or None

        if not cluster or not task_def or not container_name or not subnets:
            if logger:
                logger.log(
                    f"[fargate] Missing ECS config (cluster/task/container/subnets) cluster={cluster!r} task_def={task_def!r} container={container_name!r} subnets={subnets!r}",
                    level="ERROR",
                )
            return ExternalExecResult(ok=False, returncode=1, error="fargate_config_missing")

        redis_url = payload_env.get("REDIS_URL") or get_settings().REDIS_URL
        if redis_url:
            payload_env["REDIS_URL"] = redis_url

        payload_env.setdefault("REDIS_CLIENT_NAME", "exec")

        bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
        bundle_id = bundle_spec.get("id") if isinstance(bundle_spec, dict) else None
        module_name = bundle_spec.get("module") if isinstance(bundle_spec, dict) else None
        module_first_segment = module_name.split(".", 1)[0] if isinstance(module_name, str) and module_name else None
        bundle_dir = module_first_segment or bundle_id
        container_bundle_root = f"{_CONTAINER_BUNDLES_ROOT}/{bundle_dir}" if bundle_dir else None
        raw_runtime_globals_bytes = payload_size_bytes(runtime_globals)
        runtime_globals = prepare_external_runtime_globals(
            runtime_globals,
            host_bundle_root=request.bundle_root,
            bundle_root=container_bundle_root,
            bundle_dir=bundle_dir,
            bundle_id=(bundle_id or bundle_dir),
        )
        payload_secret_id = put_exec_payload_secret(
            exec_id=str(exec_id),
            payload={
                "runtime_globals": runtime_globals,
                "tool_module_names": request.tool_module_names or [],
                "env": payload_env,
            },
            region_name=aws_region,
        )

        if logger:
            if bundle_dir:
                logger.log(
                    f"[fargate] bundle_dir={bundle_dir} exec_bundle_root={container_bundle_root}",
                    level="INFO",
                )
            logger.log(
                f"[fargate] effective config mode=fargate cluster={cluster} task_def={task_def} container={container_name} region={aws_region or 'auto'} subnets={len(subnets)} security_groups={len(sec_groups)} assign_public_ip={assign_public_ip}",
                level="INFO",
            )

        env = build_external_exec_env(
            base_env=inline_env,
            runtime_globals=None,
            tool_module_names=None,
            exec_id=str(exec_id),
            sandbox=inline_env.get("EXECUTION_SANDBOX") or "fargate",
            log_file_prefix="supervisor",
            bundle_root=(f"/workspace/bundles/{bundle_dir}" if bundle_dir else None),
            bundle_id=(bundle_id or bundle_dir),
            include_runtime_payload=False,
            extra_runtime_env={
                "KDCUBE_EXEC_PAYLOAD_SECRET_ID": payload_secret_id,
            },
        )

        try:
            ecs = boto3.client("ecs", region_name=aws_region) if aws_region else boto3.client("ecs")
        except Exception as e:
            if logger:
                logger.log(f"[fargate] Failed to create ECS client: {type(e).__name__}: {e}", level="ERROR")
                logger.log(traceback.format_exc(), level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error=f"fargate_client_init_failed: {type(e).__name__}: {e}")
        overrides = {
            "containerOverrides": [
                {
                    "name": container_name,
                    "environment": [{"name": k, "value": str(v)} for k, v in env.items()],
                }
            ]
        }
        network = {
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": sec_groups,
                "assignPublicIp": assign_public_ip,
            }
        }

        if logger:
            logger.log(
                f"[fargate] payload sizes runtime_globals_bytes={payload_size_bytes(runtime_globals)} raw_runtime_globals_bytes={raw_runtime_globals_bytes} payload_env_bytes={payload_size_bytes(payload_env)} env_keys={len(env)} overrides_bytes={payload_size_bytes(overrides)}",
                level="INFO",
            )
            logger.log(
                f"[fargate] largest runtime_globals entries {format_size_summary(runtime_globals)}",
                level="INFO",
            )
            logger.log(
                f"[fargate] largest payload env entries {format_size_summary(payload_env)}",
                level="INFO",
            )
            logger.log(
                f"[fargate] largest inline env entries {format_size_summary(env)}",
                level="INFO",
            )
            logger.log(f"[fargate] launching task {task_def} on {cluster}", level="INFO")

        try:
            overrides_bytes = payload_size_bytes(overrides)
            if overrides_bytes > 8192:
                msg = f"container overrides length {overrides_bytes} exceeds ECS limit 8192"
                if logger:
                    logger.log(f"[fargate] {msg}", level="ERROR")
                return ExternalExecResult(
                    ok=False,
                    returncode=1,
                    error=f"fargate_overrides_too_large: {msg}",
                )

            run_kwargs: Dict[str, Any] = dict(
                cluster=cluster,
                taskDefinition=task_def,
                launchType=launch_type,
                networkConfiguration=network,
                overrides=overrides,
            )
            if platform_version:
                run_kwargs["platformVersion"] = platform_version

            try:
                resp = await asyncio.to_thread(ecs.run_task, **run_kwargs)
            except Exception as e:
                if logger:
                    logger.log(f"[fargate] ecs.run_task raised: {type(e).__name__}: {e}", level="ERROR")
                    logger.log(traceback.format_exc(), level="ERROR")
                return ExternalExecResult(ok=False, returncode=1, error=f"fargate_run_task_exception: {type(e).__name__}: {e}")
            tasks = resp.get("tasks") or []
            if not tasks:
                if logger:
                    logger.log(f"[fargate] run_task failed: {resp}", level="ERROR")
                failures = resp.get("failures") or []
                failure_summary = "; ".join(
                    f"{f.get('arn') or '?'}:{f.get('reason') or 'unknown'}"
                    for f in failures
                    if isinstance(f, dict)
                )
                return ExternalExecResult(ok=False, returncode=1, error=f"fargate_run_failed: {failure_summary or 'no task returned'}")

            task_arn = tasks[0].get("taskArn")
            if logger:
                logger.log(f"[fargate] task started: {task_arn}", level="INFO")

            t0 = time.monotonic()
            deadline = time.time() + (request.timeout_s or 600)
            last_status = None
            exit_code = None
            last_task_state = ""
            while time.time() < deadline:
                await asyncio.sleep(2)
                try:
                    desc = await asyncio.to_thread(ecs.describe_tasks, cluster=cluster, tasks=[task_arn])
                except Exception as e:
                    if logger:
                        logger.log(f"[fargate] describe_tasks failed for {task_arn}: {type(e).__name__}: {e}", level="ERROR")
                        logger.log(traceback.format_exc(), level="ERROR")
                    return ExternalExecResult(ok=False, returncode=1, error=f"fargate_describe_failed: {type(e).__name__}: {e}")
                t = (desc.get("tasks") or [{}])[0]
                last_status = t.get("lastStatus")
                state_summary = _summarize_task_state(t)
                if state_summary != last_task_state:
                    last_task_state = state_summary
                    if logger:
                        logger.log(f"[fargate] task state {state_summary}", level="INFO")
                if last_status == "STOPPED":
                    containers = t.get("containers") or []
                    for c in containers:
                        if c.get("name") == container_name:
                            exit_code = c.get("exitCode")
                            break
                    break

            elapsed = time.monotonic() - t0

            if last_status != "STOPPED":
                fallback_state = f"lastStatus={last_status or 'unknown'}"
                timeout_detail = (
                    f"ECS task did not reach STOPPED before timeout. "
                    f"Last known state: {last_task_state or fallback_state}"
                )
                if logger:
                    logger.log(f"[fargate] task timeout; stopping. {timeout_detail}", level="ERROR")
                try:
                    await asyncio.to_thread(ecs.stop_task, cluster=cluster, task=task_arn, reason="exec-timeout")
                except Exception as e:
                    if logger:
                        logger.log(f"[fargate] stop_task failed for {task_arn}: {type(e).__name__}: {e}", level="WARNING")
                return ExternalExecResult(ok=False, returncode=124, error=f"timeout: {timeout_detail}", seconds=elapsed)

            snap = runtime_globals.get("EXEC_SNAPSHOT") or {}
            if isinstance(snap, dict):
                out_work = resolve_exec_snapshot_uri(snap, "output_work_uri")
                out_out = resolve_exec_snapshot_uri(snap, "output_out_uri")
                if out_work:
                    try:
                        restore_zip_to_dir(out_work, request.workdir)
                    except Exception as e:
                        if logger:
                            logger.log(f"[fargate] Failed to restore workdir output: {e}", level="WARNING")
                if out_out:
                    try:
                        import tempfile
                        tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="fargate_out_"))
                        restore_zip_to_dir(out_out, tmp_dir)
                        async with _merge_lock_for(exec_ctx.get("turn_id") or ""):
                            for root, _, files in os.walk(tmp_dir):
                                root_path = pathlib.Path(root)
                                rel_root = root_path.relative_to(tmp_dir)
                                if not rel_root.parts:
                                    continue
                                top = rel_root.parts[0]
                                if top == "logs":
                                    for f in files:
                                        src = root_path / f
                                        dst = pathlib.Path(request.outdir) / rel_root / f
                                        dst.parent.mkdir(parents=True, exist_ok=True)
                                        try:
                                            with open(dst, "ab") as out_f:
                                                out_f.write(src.read_bytes())
                                        except Exception:
                                            pass
                                    continue
                                if top.startswith("turn_"):
                                    for f in files:
                                        src = root_path / f
                                        dst = pathlib.Path(request.outdir) / rel_root / f
                                        dst.parent.mkdir(parents=True, exist_ok=True)
                                        try:
                                            dst.write_bytes(src.read_bytes())
                                        except Exception:
                                            pass
                    except Exception as e:
                        if logger:
                            logger.log(f"[fargate] Failed to restore outdir output: {e}", level="WARNING")

            ok = (exit_code == 0)
            return ExternalExecResult(ok=ok, returncode=int(exit_code or 1), error=None if ok else "nonzero_exit", seconds=elapsed)
        finally:
            try:
                delete_exec_payload_secret(secret_id=payload_secret_id, region_name=aws_region)
            except Exception as e:
                if logger:
                    logger.log(f"[fargate] Failed to delete payload secret {payload_secret_id}: {type(e).__name__}: {e}", level="WARNING")


async def run_py_in_fargate(
    *,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    runtime_globals: Dict[str, Any],
    tool_module_names: List[str],
    logger: AgentLogger,
    timeout_s: int,
    bundle_root: Optional[pathlib.Path],
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    runtime = FargateRuntime()
    req = ExternalExecRequest(
        workdir=workdir,
        outdir=outdir,
        runtime_globals=runtime_globals,
        tool_module_names=tool_module_names,
        timeout_s=timeout_s,
        bundle_root=bundle_root,
        extra_env=extra_env,
    )
    res = await runtime.run(req, logger=logger)
    stderr_tail = ""
    error_summary = ""
    if not res.ok:
        try:
            err_path = pathlib.Path(outdir) / "logs" / "runtime.err.log"
            if err_path.exists():
                err_txt = err_path.read_text(encoding="utf-8", errors="ignore")
                stderr_tail = err_txt[-4000:] if err_txt else ""
                if err_txt:
                    for line in err_txt.splitlines():
                        if re.search(r"\b\w+Error\b", line) or "Exception" in line:
                            error_summary = line.strip()
                            break
        except Exception:
            pass
        if not error_summary:
            error_summary = (res.error or "").strip()
    return {
        "ok": res.ok,
        "returncode": res.returncode,
        "error": res.error,
        "seconds": res.seconds,
        "stderr_tail": stderr_tail,
        "error_summary": error_summary,
    }
