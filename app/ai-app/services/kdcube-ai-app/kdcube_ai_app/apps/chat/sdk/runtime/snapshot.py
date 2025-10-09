# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/snapshot.py

from __future__ import annotations
import os
import gc, sys, types, base64, json
from typing import Any, Dict, List, Optional
from contextvars import ContextVar
import contextvars
import asyncio

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.runtime.portable_spec import CommSpec, PortableSpec, ModelConfigSpec, IntegrationsSpec
from kdcube_ai_app.infra.service_hub.inventory import Config, ModelServiceBase


def run_in_executor_with_ctx(loop, func, *a, **k):
    ctx = contextvars.copy_context()
    return loop.run_in_executor(None, lambda: ctx.run(func, *a, **k))

def create_task_with_ctx(coro):
    ctx = contextvars.copy_context()
    return asyncio.create_task(ctx.run(lambda: coro))

try:
    import cloudpickle  # best effort for complex values
except Exception:
    cloudpickle = None

def _try_module_attr_of(cv: ContextVar) -> Optional[tuple[str, str]]:
    # Find a module.attr that references this ContextVar object (common case).
    for mod_name, mod in list(sys.modules.items()):
        if not isinstance(mod, types.ModuleType):
            continue
        try:
            for attr, val in vars(mod).items():
                if val is cv:
                    return mod_name, attr
        except Exception:
            continue
    return None

def _encode_value(v: Any) -> dict:
    # Prefer JSON; fall back to cloudpickle (base64). Mark encoding.
    try:
        json.dumps(v)
        return {"kind": "json", "data": v}
    except Exception:
        if cloudpickle is None:
            # last resort: repr (better than dropping it)
            return {"kind": "repr", "data": repr(v)}
        b = cloudpickle.dumps(v)
        return {"kind": "pickle_b64", "data": base64.b64encode(b).decode("ascii")}

def snapshot_all_contextvars() -> dict:
    """
    Returns a portable snapshot:
      {
        "entries": [
          {"module": "pkg.mod", "attr": "FOO_CV", "name": "FOO_CV", "value": {...}},
          ...
        ]
      }
    Includes every ContextVar with a value in the *current* context.
    """
    entries: List[dict] = []

    # Get the currently-active context by running a no-op to bind lookups
    # (copy_context not strictly necessary for reading, but harmless).
    for obj in gc.get_objects():
        if not isinstance(obj, ContextVar):
            continue
        cv: ContextVar = obj
        try:
            val = cv.get()
        except LookupError:
            continue  # not bound in this context; skip

        where = _try_module_attr_of(cv)  # best-effort identity
        serialized = _encode_value(val)

        ent = {
            "name": getattr(cv, "name", None),
            "value": serialized,
        }
        if where:
            ent["module"], ent["attr"] = where
        entries.append(ent)

    return {"entries": entries}

def _pick_selected_model_name(d: dict | None) -> Optional[str]:
    """
    Config.default_llm_model is a dict from MODEL_CONFIGS[…].
    Try common keys safely.
    """
    if not d:
        return None
    return (
        d.get("model_name")
    )

def _config_to_model_config_spec(cfg: Config) -> ModelConfigSpec:
    return ModelConfigSpec(
        openai_api_key=cfg.openai_api_key or None,
        claude_api_key=cfg.claude_api_key or None,
        selected_model=_pick_selected_model_name(cfg.default_llm_model),
        selected_embedder=cfg.selected_embedder,
        custom_embedding_endpoint=cfg.custom_embedding_endpoint,
        custom_embedding_model=cfg.custom_embedding_model,
        custom_embedding_size=cfg.custom_embedding_size,
        format_fix_enabled=bool(getattr(cfg, "format_fix_enabled", True)),
        role_models=dict(cfg.role_models or {}),
        custom_model_endpoint=cfg.custom_model_endpoint or None,
        custom_model_api_key=cfg.custom_model_api_key or None,
        custom_model_name=cfg.custom_model_name or None,
        kb_search_endpoint=cfg.kb_search_url or None,
        agentic_bundle_id=(getattr(cfg, "ai_bundle_spec", None).id if getattr(cfg, "ai_bundle_spec", None) else None),
        bundle_storage_url=cfg.bundle_storage_url or None,
        tenant=cfg.tenant or None,
        project=cfg.project or None,
    )

def build_portable_spec(*, svc: ModelServiceBase, chat_comm: ChatCommunicator, integrations: dict | None = None) -> PortableSpec:
    comm_spec = None
    if chat_comm:
        c = chat_comm._export_comm_spec_for_runtime()
        comm_spec = CommSpec(**c)

    # Pick the very small set of env you truly need
    # env_whitelist = [
    #     "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    #     "CUSTOM_MODEL_ENDPOINT", "CUSTOM_MODEL_API_KEY", "CUSTOM_MODEL_NAME",
    #     "KB_SEARCH_URL", "REDIS_URL", "ORCHESTRATOR_IDENTITY",
    #     "TENANT_ID", "DEFAULT_PROJECT_NAME",
    # ]
    # env_passthrough = {k: os.environ[k] for k in env_whitelist if os.environ.get(k)}
    env_passthrough = dict(os.environ)
    # integrations_spec = IntegrationsSpec(ctx_client=(integrations or {}).get("ctx_client"))
    integrations_spec = IntegrationsSpec()

    from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx as _run_ctx
    from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx as _comm_ctx
    from kdcube_ai_app.infra import accounting as _acct

    contextvars = {
        "run_ctx": _run_ctx.snapshot_ctxvars(),
        "comm_ctx": _comm_ctx.snapshot_ctxvars(),
        "accounting": _acct.snapshot_ctxvars(),
    }
    accounting_storage = { "storage_path": os.environ.get("KDCUBE_STORAGE_PATH") }
    config_spec = _config_to_model_config_spec(svc.config)
    spec = PortableSpec(
        model_config=config_spec,
        comm=comm_spec,
        integrations=integrations_spec,
        cv_snapshot=snapshot_all_contextvars(),
        env_passthrough=env_passthrough,
        contextvars=contextvars,
        accounting_storage=accounting_storage
    )
    return spec