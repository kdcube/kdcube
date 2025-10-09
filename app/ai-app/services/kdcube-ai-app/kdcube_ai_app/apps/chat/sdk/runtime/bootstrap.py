# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py
from __future__ import annotations
import os, logging

import os
import traceback
from dataclasses import asdict

import sys
import json
import base64
import importlib
import logging
from typing import Any, Dict, Optional
from contextvars import ContextVar

from kdcube_ai_app.apps.chat.emitters import (
    ChatRelayCommunicator,
    ChatCommunicator,
    _RelayEmitterAdapter,
)
from kdcube_ai_app.apps.chat.sdk.runtime.portable_spec import PortableSpec
from kdcube_ai_app.infra.service_hub.inventory import (
    ConfigRequest,
    create_workflow_config,
    ModelServiceBase,
)
from kdcube_ai_app.storage.storage import IStorageBackend

logger = logging.getLogger(__name__)

try:
    import cloudpickle
except Exception:  # pragma: no cover
    cloudpickle = None


# ---------------------------
# helpers for generic CV blob
# ---------------------------

def _decode_value(packed: dict) -> Any:
    kind = packed.get("kind")
    data = packed.get("data")
    if kind == "json":
        return data
    if kind == "pickle_b64" and cloudpickle is not None:
        return cloudpickle.loads(base64.b64decode(data.encode("ascii")))
    if kind == "repr":
        # last resort; you may choose to leave it as string
        return data
    return data


def _find_cv_in_child(entry: dict) -> Optional[ContextVar]:
    # Prefer exact module.attr match if present
    mod = entry.get("module")
    attr = entry.get("attr")
    if mod and attr:
        try:
            m = importlib.import_module(mod)
            maybe = getattr(m, attr, None)
            if isinstance(maybe, ContextVar):
                return maybe
        except Exception:
            pass

    # Fallback: scan by ContextVar.name
    want_name = entry.get("name")
    if want_name:
        for m in list(sys.modules.values()):
            if not m:
                continue
            try:
                for a, v in vars(m).items():
                    if isinstance(v, ContextVar) and getattr(v, "name", None) == want_name:
                        return v
            except Exception:
                continue
    return None


def restore_all_contextvars(snapshot: dict) -> dict:
    restored, missing = 0, 0
    for e in (snapshot or {}).get("entries", []):
        cv = _find_cv_in_child(e)
        if not cv:
            missing += 1
            continue
        try:
            cv.set(_decode_value(e["value"]))
            restored += 1
        except Exception:
            missing += 1
    return {"restored": restored, "missing": missing}


# ---------------------------
# env / service / registry
# ---------------------------

def apply_env(env_map: Dict[str, str]):
    for k, v in (env_map or {}).items():
        try:
            os.environ[k] = v
        except Exception:
            pass


def make_model_service(spec: PortableSpec) -> ModelServiceBase:
    cfg_req = ConfigRequest(**asdict(spec.model_config))
    cfg = create_workflow_config(cfg_req)
    return ModelServiceBase(cfg)


def make_chat_comm(spec: PortableSpec) -> Optional[ChatCommunicator]:
    if not spec.comm:
        return None
    # ChatRelayCommunicator will pick redis URL from env (REDIS_URL) if not overridden
    relay = ChatRelayCommunicator(channel=spec.comm.channel)
    emitter = _RelayEmitterAdapter(relay)
    return ChatCommunicator(
        emitter=emitter,
        service=spec.comm.service,
        conversation=spec.comm.conversation,
        room=spec.comm.room,
        target_sid=spec.comm.target_sid,
    )

def make_registry(spec: PortableSpec) -> Dict[str, Any]:
    """
    Build runtime registry objects inside the child process.
    Keep this lightweight; do not ship connection pools across processes.
    """
    reg: Dict[str, Any] = {}
    try:
        from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
        reg["kb_client"] = KBClient(pool=None)  # lazy-init pool later
    except Exception:
        pass
    return reg


def bind_into_module(module, *, svc: ModelServiceBase, registry: Any = None, integrations: Dict[str, Any] | None = None):
    # bind at the module level
    _bind_target(module, svc=svc, registry=registry, integrations=integrations)


class _LocalFSBackend:
    """
    Minimal backend with write_text(rel_path, content), used by FileAccountingStorage.
    It writes under base_dir (defaults to env KDCUBE_STORAGE_PATH or /tmp/kdcube).
    """
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.environ.get("KDCUBE_STORAGE_PATH") or "/tmp/kdcube"

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        from pathlib import Path
        p = Path(self.base_dir) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)

def _make_storage_backend_from_spec(spec: PortableSpec) -> IStorageBackend|_LocalFSBackend:
    storage_path = None
    try:
        from kdcube_ai_app.storage.storage import create_storage_backend

        storage_path = (spec.accounting_storage or {}).get("storage_path") or os.environ.get("KDCUBE_STORAGE_PATH")
        logger.info(f"[Bootstrap._make_storage_backend_from_spec]. Using accounting storage path: {storage_path}")
        if storage_path:
            kdcube_storage_backend = create_storage_backend(storage_path, **{})
            return kdcube_storage_backend

    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
    return _LocalFSBackend(storage_path)

# -----------------------------------
# bootstrap (called from child)
# -----------------------------------

def _bind_target(target, *, svc, registry=None, integrations=None):
    try:
        # idempotence guard on any object we bind into
        if getattr(target, "__KDCUBE_BIND_DONE__", False):
            return
        if hasattr(target, "bind_service") and callable(target.bind_service):
            target.bind_service(svc)
        elif hasattr(target, "set_service") and callable(target.set_service):
            target.set_service(svc)
        else:
            setattr(target, "SERVICE", svc)
            setattr(target, "model_service", svc)

        if registry is not None:
            if hasattr(target, "bind_registry") and callable(target.bind_registry):
                target.bind_registry(registry)
            else:
                setattr(target, "REGISTRY", registry)

        if integrations is not None:
            if hasattr(target, "bind_integrations") and callable(target.bind_integrations):
                target.bind_integrations(integrations)
            else:
                setattr(target, "INTEGRATIONS", integrations)

        setattr(target, "__KDCUBE_BIND_DONE__", True)
    except Exception:
        pass

def bootstrap_bind_all(spec_json: str, *, module_names: list[str]) -> dict:
    """
    Single-shot bootstrap:
      - apply env and restore ContextVars once
      - build services/registry once
      - bind into every module in module_names (module and module.tools)
      - rebuild communicator once
    Safe to call multiple times (binders are idempotent).
    """
    spec = PortableSpec.from_json(spec_json)

    # 1) env passthrough
    try:
        apply_env(spec.env_passthrough)
    except Exception:
        print("apply_env failed", file=sys.stderr)

    # 2) run_ctx/accounting CVs
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx as _run_ctx
        if hasattr(_run_ctx, "restore_ctxvars_from_env"):
            _run_ctx.restore_ctxvars_from_env()
        if spec.contextvars and spec.contextvars.get("run_ctx"):
            _run_ctx.restore_ctxvars(spec.contextvars["run_ctx"])
    except Exception:
        print("run_ctx restore failed", file=sys.stderr)

    try:
        from kdcube_ai_app.infra import accounting as _acct
        storage_backend = _make_storage_backend_from_spec(spec)
        snap = (spec.contextvars or {}).get("accounting") or {}
        _acct.restore_ctxvars(snap, storage_backend=storage_backend, enabled=True)
    except Exception:
        print("accounting restore failed", file=sys.stderr)

    # 3) generic CV snapshot (best-effort)
    if spec.cv_snapshot:
        try:
            for e in (spec.cv_snapshot or {}).get("entries", []):
                mod = (e or {}).get("module")
                if mod:
                    try:
                        importlib.import_module(mod)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            restore_all_contextvars(spec.cv_snapshot)
        except Exception:
            print("generic CV restore failed", file=sys.stderr)

    # 4) make services/registry once
    try:
        # model_config may be a pydantic model or a plain dict
        mc = spec.model_config
        if hasattr(mc, "model_dump"):
            cfg_req = ConfigRequest(**mc.model_dump())
        elif isinstance(mc, dict):
            cfg_req = ConfigRequest(**mc)
        else:
            # last resort: dataclass-like
            from dataclasses import asdict as _asdict
            cfg_req = ConfigRequest(**_asdict(mc))
        cfg = create_workflow_config(cfg_req)
        svc = ModelServiceBase(cfg)
    except Exception:
        print("make_model_service failed", file=sys.stderr)
        raise

    try:
        registry = make_registry(spec)
    except Exception:
        registry = {}

    # 5) bind into every module (module and module.tools)
    for name in module_names or []:
        try:
            m = importlib.import_module(name)
            bind_into_module(
                m,
                svc=svc,
                registry=registry,
                integrations=(spec.integrations.__dict__ if spec.integrations else None),
            )
        except Exception:
            print(f"bind_into_module failed for {name}", file=sys.stderr)

    # 6) communicator once
    try:
        comm = make_chat_comm(spec)
        if comm:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm
            set_comm(comm)
    except Exception:
        print("make_chat_comm failed", file=sys.stderr)

    return {"ok": True}

def bootstrap_from_spec(spec_json: str, *, tool_module) -> Dict[str, Any]:
    """
    Child-side bootstrap. Safe to call multiple times (idempotent-ish).
    - Applies env_passthrough
    - Restores run_ctx/accounting CVs (and best-effort generic CV snapshot)
    - Rebuilds ModelService + registry, binds them into tool_module
    - Rebuilds communicator and sets COMM_CV
    """
    # Parse spec
    spec = PortableSpec.from_json(spec_json)

    # 1) ENV first (so downstream fallbacks can read OUTPUT_DIR/WORKDIR/REDIS_URL, etc.)
    try:
        apply_env(spec.env_passthrough)
        print(f"env_passthrough restore done")
    except Exception:
        print(f"apply_env failed {traceback.format_exc()}", file=sys.stderr)

    # 2) Module-specific ContextVars restoration
    #    2.1 run_ctx: fallback from env → OUTDIR_CV/WORKDIR_CV, then restore snapshot
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx as _run_ctx
        # ensure env fallbacks (OUTPUT_DIR / WORKDIR) populate CVs if they were empty
        if hasattr(_run_ctx, "restore_ctxvars_from_env"):
            _run_ctx.restore_ctxvars_from_env()
        # then restore the parent snapshot (OUTDIR_CV/WORKDIR_CV/SOURCE_ID_CV)
        if spec.contextvars and spec.contextvars.get("run_ctx"):
            _run_ctx.restore_ctxvars(spec.contextvars["run_ctx"])
        print(f"run_ctx restore done")
    except Exception:
        print(f"run_ctx restore failed {traceback.format_exc()}", file=sys.stderr)

    #    2.2 accounting: reconstruct a fresh AccountingContext and init storage
    try:
        from kdcube_ai_app.infra import accounting as _acct
        storage_backend = _make_storage_backend_from_spec(spec)
        snap = (spec.contextvars or {}).get("accounting") or {}
        _acct.restore_ctxvars(snap, storage_backend=storage_backend, enabled=True)
        print(f"acct restore done")
    except Exception:
        print(f"accounting restore failed {traceback.format_exc()}", file=sys.stderr)

    # 3) Best-effort generic CV snapshot restore (for any other ContextVars)
    if spec.cv_snapshot:
        # Preload declaring modules to improve attr-based resolution
        try:
            for e in (spec.cv_snapshot or {}).get("entries", []):
                mod = (e or {}).get("module")
                if mod:
                    try:
                        importlib.import_module(mod)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            stats = restore_all_contextvars(spec.cv_snapshot)
            # optional debug:
            # logger.debug("Generic CV restore: %s", stats)
        except Exception:
            print("generic CV restore failed", file=sys.stderr)

    # 4) Build services/registry/integrations; bind into tool module
    try:
        svc = make_model_service(spec)
        print("Model service initialized")
    except Exception as e:
        print(f"make_model_service failed {traceback.format_exc()}", file=sys.stderr)
        raise

    try:
        registry = make_registry(spec)
    except Exception:
        registry = {}

    try:
        bind_into_module(
            tool_module,
            svc=svc,
            registry=registry,
            integrations=(spec.integrations.__dict__ if spec.integrations else None),
        )
    except Exception:
        logger.exception(f"bind_into_module failed {traceback.format_exc()}")

    # 5) Rebuild communicator and set COMM_CV (optional)
    try:
        comm = make_chat_comm(spec)
        if comm:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm
            set_comm(comm)
    except Exception:
        logger.exception(f"make_chat_comm / set_comm failed {traceback.format_exc()}")

    return {"ok": True}
