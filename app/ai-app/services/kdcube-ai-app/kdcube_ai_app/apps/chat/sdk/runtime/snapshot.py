# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/snapshot.py

from __future__ import annotations
import os
import gc, sys, types, base64, json
from typing import Any, Dict, List, Optional, Tuple
from contextvars import ContextVar
import contextvars
import asyncio

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.config import get_settings
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

def _is_json_primitive(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))

def _encode_value(v: Any) -> Tuple[bool, Any]:
    """
    Encode a ContextVar value into something JSON-serializable.

    Returns:
        (ok, encoded_value)

    - ok == False → value is NOT portable; caller MUST skip this ContextVar.
    - ok == True  → encoded_value is JSON-safe and may be stored.
    """

    # Primitives are fine
    if _is_json_primitive(v):
        return True, v

    # Lists: keep encodable elements
    if isinstance(v, list):
        out = []
        for item in v:
            ok_item, enc_item = _encode_value(item)
            if ok_item:
                out.append(enc_item)
        return True, out

    # Dicts: keep encodable values, stringifiable keys
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            # We only support primitive keys
            if not _is_json_primitive(k):
                continue
            ok_val, enc_val = _encode_value(val)
            if ok_val:
                out[str(k)] = enc_val
        return True, out

    # Any other type (custom classes, AccountingContext, storage backends, etc.)
    # is considered NON-portable here → we SKIP it instead of stringifying.
    return False, None


def _decode_value(v: Any) -> Any:
    """
    Reverse of _encode_value for our simple scheme.
    All stored values are JSON-safe; just return them.
    """
    return v

def snapshot_all_contextvars() -> dict:
    """
    Returns a portable snapshot:

      {
        "entries": [
          {"module": "pkg.mod", "attr": "FOO_CV", "name": "FOO_CV", "value": ...},
          ...
        ]
      }

    Includes every ContextVar with a *portable* value in the current context.

    - Values must be JSON-safe (primitives / lists / dicts) per _encode_value.
    - Any ContextVar with a non-portable value (e.g. AccountingContext, storage backends)
      is **skipped** here and should be handled by its own dedicated snapshot logic.
    """
    entries: List[dict] = []

    # Copy list to avoid surprises if GC mutates during iteration
    for obj in list(gc.get_objects()):
        try:
            is_cv = isinstance(obj, ContextVar)
        except ReferenceError:
            # weakref proxy whose referent is gone
            continue
        except Exception:
            # ultra-defensive: never let snapshot explode
            continue

        if not is_cv:
            continue

        cv: ContextVar = obj
        try:
            val = cv.get()
        except LookupError:
            # not bound in this context
            continue
        except ReferenceError:
            # referent gone between isinstance and get()
            continue
        except Exception:
            # any other weirdness → skip
            continue

        ok, encoded = _encode_value(val)
        if not ok:
            # Non-portable value (e.g. AccountingContext); skip this CV entirely.
            continue

        try:
            where = _try_module_attr_of(cv)  # best-effort identity (module + attr)
        except Exception:
            where = None

        ent: Dict[str, Any] = {
            "name": getattr(cv, "name", None),
            "value": encoded,
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
    cache_spec = None
    if isinstance(integrations, dict):
        cache_val = integrations.get("namespaced_kv_cache")
        if cache_val is not None:
            try:
                cache_cfg = cache_val.to_config() if hasattr(cache_val, "to_config") else cache_val
                if hasattr(cache_cfg, "as_dict"):
                    cache_spec = cache_cfg.as_dict()
                elif isinstance(cache_cfg, dict):
                    cache_spec = cache_cfg
            except Exception:
                cache_spec = None
    if cache_spec is None:
        try:
            from kdcube_ai_app.infra.service_hub.cache import build_default_favicon_cache_config
            cache_spec = build_default_favicon_cache_config().as_dict()
        except Exception:
            cache_spec = None

    integrations_spec = IntegrationsSpec(
        ctx_client=(integrations or {}).get("ctx_client") if isinstance(integrations, dict) else None,
        namespaced_kv_cache=cache_spec,
    )

    from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx as _run_ctx
    from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx as _comm_ctx
    from kdcube_ai_app.infra import accounting as _acct

    contextvars = {
        "run_ctx": _run_ctx.snapshot_ctxvars(),
        "comm_ctx": _comm_ctx.snapshot_ctxvars(),
        "accounting": _acct.snapshot_ctxvars(),
    }
    _settings = get_settings()
    # accounting_storage = { "storage_path": os.environ.get("KDCUBE_STORAGE_PATH") }
    accounting_storage = { "storage_path": _settings.STORAGE_PATH }
    config_spec = _config_to_model_config_spec(svc.config)
    try:
        cv_snapshot = snapshot_all_contextvars()
    except Exception as e:
        # logging optional
        # logger.warning("Failed to snapshot all contextvars: %s", e)
        cv_snapshot = {"entries": []}

    spec = PortableSpec(
        model_config=config_spec,
        comm=comm_spec,
        integrations=integrations_spec,
        cv_snapshot=cv_snapshot,
        env_passthrough=env_passthrough,
        contextvars=contextvars,
        accounting_storage=accounting_storage
    )
    return spec
