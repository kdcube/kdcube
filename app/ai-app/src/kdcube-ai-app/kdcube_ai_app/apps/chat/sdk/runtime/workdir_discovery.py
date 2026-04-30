# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/workdir_discovery.py
import os, json
import pathlib
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_sources_any
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import RUNTIME_OUTPUT_ENV


def _from_cv_or_env(cv, env_key: str) -> str:
    """
    Try ContextVar first; if empty, fall back to environment variable.
    Returns '' if neither is available.
    """
    try:
        v = cv.get("")
    except Exception:
        v = ""
    return v or os.environ.get(env_key, "")

def resolve_output_dir() -> pathlib.Path:
    """
    Resolve the solver's OUTPUT_DIR (data/artifact root):
      1) OUTDIR_CV ContextVar
      2) os.environ['OUTPUT_DIR']
    Ensures the directory exists.
    """
    raw = _from_cv_or_env(OUTDIR_CV, "OUTPUT_DIR")
    if not raw:
        raise RuntimeError("OUTPUT_DIR not set in run context")
    p = pathlib.Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_runtime_output_dir() -> pathlib.Path:
    """
    Resolve the runtime/internal output root.

    In split execution, OUTPUT_DIR points to the artifact root while
    KDCUBE_RUNTIME_OUTPUT_DIR points to the internal root containing
    timeline/sources/log metadata. In legacy/local paths they may be the same.
    """
    raw = os.environ.get(RUNTIME_OUTPUT_ENV, "")
    if not raw:
        try:
            outdir_raw = OUTDIR_CV.get("")
        except Exception:
            outdir_raw = ""
        raw = outdir_raw or os.environ.get("OUTPUT_DIR", "")
    if not raw:
        raise RuntimeError("runtime output directory not set in run context")
    p = pathlib.Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def resolve_workdir() -> pathlib.Path:
    """
    Resolve the solver's WORKDIR:
      1) WORKDIR_CV ContextVar
      2) os.environ['WORKDIR']
    Ensures the directory exists.
    """
    raw = _from_cv_or_env(WORKDIR_CV, "WORKDIR")
    if not raw:
        raise RuntimeError("WORKDIR not set in run context")
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
