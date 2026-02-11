# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Fargate / distributed execution stub.

Planned flow:
- Snapshot workdir/outdir and upload to S3
- Launch remote exec task
- Collect output zips and return results
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import ExternalExecRequest, ExternalExecResult, ExternalRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import (
    snapshot_exec_input,
    ensure_bundle_snapshot,
    restore_zip_to_dir,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.solution_workspace import build_exec_snapshot_workspace

# Serialize output merge when multiple agents run in parallel.
_MERGE_LOCKS: Dict[str, asyncio.Lock] = {}


def _merge_lock_for(turn_id: str) -> asyncio.Lock:
    tid = (turn_id or "").strip()
    if not tid:
        # Fallback to a shared lock
        return _MERGE_LOCKS.setdefault("_global", asyncio.Lock())
    return _MERGE_LOCKS.setdefault(tid, asyncio.Lock())


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
                "base_prefix": snapshot.base_prefix,
                "input_work_uri": snapshot.input_work_uri,
                "input_out_uri": snapshot.input_out_uri,
                "output_work_uri": snapshot.output_work_uri,
                "output_out_uri": snapshot.output_out_uri,
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

        enabled = os.environ.get("FARGATE_EXEC_ENABLED", "0").lower() in {"1", "true", "yes"}
        if not enabled:
            if logger:
                logger.log("[fargate] Distributed execution not enabled (FARGATE_EXEC_ENABLED=1)", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="fargate_disabled")

        try:
            import boto3  # type: ignore
        except Exception:
            if logger:
                logger.log("[fargate] boto3 not installed; cannot run ECS task", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="boto3_missing")

        cluster = os.environ.get("FARGATE_CLUSTER") or ""
        task_def = os.environ.get("FARGATE_TASK_DEFINITION") or ""
        container_name = os.environ.get("FARGATE_CONTAINER_NAME") or ""
        subnets = [s for s in (os.environ.get("FARGATE_SUBNETS") or "").split(",") if s.strip()]
        sec_groups = [s for s in (os.environ.get("FARGATE_SECURITY_GROUPS") or "").split(",") if s.strip()]
        assign_public_ip = os.environ.get("FARGATE_ASSIGN_PUBLIC_IP", "DISABLED")
        launch_type = os.environ.get("FARGATE_LAUNCH_TYPE", "FARGATE")
        platform_version = os.environ.get("FARGATE_PLATFORM_VERSION") or None

        if not cluster or not task_def or not container_name or not subnets:
            if logger:
                logger.log("[fargate] Missing ECS config (cluster/task/container/subnets)", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="fargate_config_missing")

        env = {
            "WORKDIR": "/workspace/work",
            "OUTPUT_DIR": "/workspace/out",
            "LOG_DIR": "/workspace/out/logs",
            "LOG_FILE_PREFIX": "executor",
            "EXECUTION_ID": str(exec_id),
            "RUNTIME_GLOBALS_JSON": json.dumps(runtime_globals, ensure_ascii=False, default=str),
            "RUNTIME_TOOL_MODULES": json.dumps(request.tool_module_names or [], ensure_ascii=False),
        }

        bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
        bundle_id = bundle_spec.get("id") if isinstance(bundle_spec, dict) else None
        if bundle_id:
            env["EXEC_BUNDLE_ROOT"] = f"/workspace/bundles/{bundle_id}"

        if request.extra_env:
            for k, v in request.extra_env.items():
                if k in {"WORKDIR", "OUTPUT_DIR"}:
                    continue
                env[k] = v

        ecs = boto3.client("ecs")
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
            logger.log(f"[fargate] launching task {task_def} on {cluster}", level="INFO")

        resp = ecs.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            launchType=launch_type,
            networkConfiguration=network,
            overrides=overrides,
            **({"platformVersion": platform_version} if platform_version else {}),
        )
        tasks = resp.get("tasks") or []
        if not tasks:
            if logger:
                logger.log(f"[fargate] run_task failed: {resp}", level="ERROR")
            return ExternalExecResult(ok=False, returncode=1, error="fargate_run_failed")

        task_arn = tasks[0].get("taskArn")
        if logger:
            logger.log(f"[fargate] task started: {task_arn}", level="INFO")

        deadline = time.time() + (request.timeout_s or 600)
        last_status = None
        exit_code = None
        while time.time() < deadline:
            await asyncio.sleep(2)
            desc = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
            t = (desc.get("tasks") or [{}])[0]
            last_status = t.get("lastStatus")
            if last_status == "STOPPED":
                containers = t.get("containers") or []
                for c in containers:
                    if c.get("name") == container_name:
                        exit_code = c.get("exitCode")
                        break
                break

        if last_status != "STOPPED":
            if logger:
                logger.log("[fargate] task timeout; stopping", level="ERROR")
            try:
                ecs.stop_task(cluster=cluster, task=task_arn, reason="exec-timeout")
            except Exception:
                pass
            return ExternalExecResult(ok=False, returncode=124, error="timeout")

        # Merge outputs from snapshot storage
        snap = runtime_globals.get("EXEC_SNAPSHOT") or {}
        if isinstance(snap, dict):
            out_work = snap.get("output_work_uri")
            out_out = snap.get("output_out_uri")
            if out_work:
                try:
                    restore_zip_to_dir(out_work, request.workdir)
                except Exception as e:
                    if logger:
                        logger.log(f"[fargate] Failed to restore workdir output: {e}", level="WARNING")
            if out_out:
                try:
                    # Restore to temp dir, then selectively merge into real outdir
                    import tempfile
                    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="fargate_out_"))
                    restore_zip_to_dir(out_out, tmp_dir)
                    async with _merge_lock_for(exec_ctx.get("turn_id") or ""):
                        # Only merge expected outputs (avoid overwriting timeline/sources_pool)
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
        return ExternalExecResult(ok=ok, returncode=int(exit_code or 1), error=None if ok else "nonzero_exit")


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
    return {
        "ok": res.ok,
        "returncode": res.returncode,
        "error": res.error,
        "seconds": res.seconds,
    }
