# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import sys
# chat/sdk/runtime/run_ctx.py
# Context variables to hold runtime context info
from contextvars import ContextVar
import os

OUTDIR_CV = ContextVar("OUTDIR_CV", default="")
WORKDIR_CV = ContextVar("WORKDIR_CV", default="")

# Holds {'next': int} for continuous sources numbering
SOURCE_ID_CV = ContextVar("SOURCE_ID_CV", default=None)

# --- portable snapshot/restore for run_ctx ---
def snapshot_ctxvars() -> dict:
    return {
        "OUTDIR_CV": OUTDIR_CV.get(""),
        "WORKDIR_CV": WORKDIR_CV.get(""),
        "SOURCE_ID_CV": SOURCE_ID_CV.get(None),  # {'next': int} or None
    }

def restore_ctxvars(payload: dict) -> None:
    try:
        if "OUTDIR_CV" in payload and payload["OUTDIR_CV"]:
            print(f'Setting OUTDIR_CV from payload {payload["OUTDIR_CV"]}')
            OUTDIR_CV.set(payload["OUTDIR_CV"])
        else:
            print(f'Failed to set OUTDIR_CV from payload {payload.get("OUTDIR_CV")}', file=sys.stderr)
    except Exception:
        pass
    try:
        if "WORKDIR_CV" in payload and payload["WORKDIR_CV"]:
            print(f'Setting WORKDIR_CV from payload {payload["WORKDIR_CV"]}')
            WORKDIR_CV.set(payload["WORKDIR_CV"])
        else:
            print(f'Failed to set WORKDIR_CV from payload {payload.get("WORKDIR_CV")}', file=sys.stderr)
    except Exception:
        pass
    try:
        if "SOURCE_ID_CV" in payload and payload["SOURCE_ID_CV"] is not None:
            SOURCE_ID_CV.set(payload["SOURCE_ID_CV"])
    except Exception:
        pass

def restore_ctxvars_from_env() -> None:
    """
    Idempotent: if CVs are empty, take them from env.
    Useful when only OUTPUT_DIR/WORKDIR are provided via env.
    """
    try:
        if not OUTDIR_CV.get(""):
            od = os.environ.get("OUTPUT_DIR")
            if od:
                print(f"Setting OUTDIR_CV from OUTPUT_DIR env: {od}")
                OUTDIR_CV.set(od)
            else:
                print(f"Failed to set OUTDIR_CV from OUTPUT_DIR env:", file=sys.stderr)
    except Exception: pass
    try:
        if not WORKDIR_CV.get(""):
            wd = os.environ.get("WORKDIR")
            if wd:
                print(f"Setting WORKDIR_CV from WORKDIR env: {wd}")
                WORKDIR_CV.set(wd)
            else:
                print(f"Failed to set WORKDIR_CV from WORKDIR env:", file=sys.stderr)
    except Exception: pass
