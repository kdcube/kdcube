# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/workdir_discovery.py
import os, json
import pathlib
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_sources_any
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import (
    ARTIFACT_OUTPUT_ENV,
    RUNTIME_OUTPUT_ENV,
    artifact_outdir_for,
    runtime_outdir_for_artifact_outdir,
)


def _from_cv(cv) -> str:
    """
    Resolve the current runtime path from ContextVar state.

    Long-lived proc processes can run multiple turns/jobs concurrently, so
    per-turn path resolution must not read process-global OUTPUT_DIR/WORKDIR.
    Isolated child runtimes may receive those env vars, but bootstrap copies
    them into OUTDIR_CV/WORKDIR_CV before SDK tools use this module.
    """
    try:
        return cv.get("") or ""
    except Exception:
        return ""


def _isolated_runtime_env_is_trusted() -> bool:
    return bool(
        os.environ.get("EXEC_CONTAINER_ROLE")
        or os.environ.get("AGENT_IO_CONTEXT")
        or os.environ.get("RUNTIME_GLOBALS_JSON")
    )

def resolve_output_dir() -> pathlib.Path:
    """
    Resolve the solver's artifact output root.

    OUTPUT_DIR is accepted only at runtime bootstrap boundaries where it is
    copied into OUTDIR_CV. Reading it here would be process-global and can race
    between concurrent in-process tool calls.

    User-visible/generated files live under ``out/workdir``. Some older or
    local runtime paths still bind OUTDIR_CV to the runtime root ``out``; map
    that root to the separated artifact root here so SDK tools follow the same
    OUTPUT_DIR contract as generated code in Docker/Fargate runtimes.

    Ensures the directory exists.
    """
    explicit_artifact = os.environ.get(ARTIFACT_OUTPUT_ENV, "") if _isolated_runtime_env_is_trusted() else ""
    raw = explicit_artifact or _from_cv(OUTDIR_CV)
    if not raw:
        raise RuntimeError("OUTDIR_CV not set in run context")
    p = pathlib.Path(raw).resolve()
    if not explicit_artifact:
        p = artifact_outdir_for(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_runtime_output_dir() -> pathlib.Path:
    """
    Resolve the runtime/internal output root.

    In split execution, OUTPUT_DIR points to the artifact root while
    KDCUBE_RUNTIME_OUTPUT_DIR points to the internal root containing
    timeline/sources/log metadata. In legacy/local paths they may be the same.
    """
    explicit_runtime = os.environ.get(RUNTIME_OUTPUT_ENV, "") if _isolated_runtime_env_is_trusted() else ""
    raw = explicit_runtime or _from_cv(OUTDIR_CV)
    if not raw:
        raise RuntimeError("runtime output directory not set in run context")
    p = pathlib.Path(raw).resolve()
    if not explicit_runtime:
        p = runtime_outdir_for_artifact_outdir(p)
    p.mkdir(parents=True, exist_ok=True)
    return p

def resolve_workdir() -> pathlib.Path:
    """
    Resolve the solver's workdir from WORKDIR_CV.

    WORKDIR env is accepted only at runtime bootstrap boundaries where it is
    copied into WORKDIR_CV.

    Ensures the directory exists.
    """
    raw = _from_cv(WORKDIR_CV)
    if not raw:
        raise RuntimeError("WORKDIR_CV not set in run context")
    p = pathlib.Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_sources_pool_from_disk() -> list[dict]:
    outdir = resolve_runtime_output_dir()
    payload = None
    timeline_path = outdir / "timeline.json"
    if not timeline_path.exists():
        timeline_path = resolve_output_dir() / "timeline.json"
    if timeline_path.exists():
        try:
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
    if isinstance(payload, dict):
        pool = payload.get("sources_pool") or []
    else:
        pool = []
    return normalize_sources_any(pool)

__all__ = ["resolve_output_dir", "resolve_runtime_output_dir", "resolve_workdir"]
