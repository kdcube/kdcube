# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/workdir_discovery.py
import os
import pathlib
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV

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
    Resolve the solver's OUTPUT_DIR:
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

__all__ = ["resolve_output_dir", "resolve_workdir"]
