# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/__init__.py
"""
Self-contained accounting system with async-safe context isolation using contextvars
"""

import contextvars
import uuid
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

from kdcube_ai_app.apps.utils.util import _deep_merge
from kdcube_ai_app.infra.accounting.usage import ServiceUsage

# ================================
# REPORTING POLICY
# ================================

from typing import Set

# keys we want at the event root (others can remain nested under "context" if you prefer)
CONTEXT_EXPORT_KEYS: Set[str] = {
    "user_id", "session_id", "project_id", "tenant_id",
    "request_id", "component", "app_bundle_id"
}

def register_context_keys(*keys: str) -> None:
    CONTEXT_EXPORT_KEYS.update(keys)

# ================================
# ASYNC-SAFE CONTEXT USING CONTEXTVARS
# ================================

class AccountingContext:
    def __init__(self):
        # canonical storage for EVERYTHING
        self._ctx: Dict[str, Any] = {}
        self.event_enrichment: Dict[str, Any] = {}

    # convenience properties for legacy/common keys (optional)
    @property
    def user_id(self) -> Optional[str]: return self._ctx.get("user_id")
    @user_id.setter
    def user_id(self, v): self._ctx["user_id"] = v

    @property
    def session_id(self) -> Optional[str]: return self._ctx.get("session_id")
    @session_id.setter
    def session_id(self, v): self._ctx["session_id"] = v

    @property
    def component(self) -> Optional[str]: return self._ctx.get("component")
    @component.setter
    def component(self, v): self._ctx["component"] = v

    # enrichment is orthogonal;
    # event_enrichment: Dict[str, Any] = {}

    def update(self, **kwargs):
        self._ctx.update(kwargs)

    def to_dict(self) -> Dict[str, Any]:
        # return a shallow copy
        return dict(self._ctx)

# Context variables for async-safe storage
_context_var: contextvars.ContextVar[Optional[AccountingContext]] = contextvars.ContextVar(
    'accounting_context', default=None
)
_storage_var: contextvars.ContextVar[Optional['IAccountingStorage']] = contextvars.ContextVar(
    'accounting_storage', default=None
)
_default_storage = None

def _get_context() -> AccountingContext:
    """Get async-safe accounting context"""
    context = _context_var.get()
    if context is None:
        context = AccountingContext()
        _context_var.set(context)
    return context

def _get_enrichment() -> Dict[str, Any]:
    return _get_context().event_enrichment or {}

def _get_storage():
    """Get async-safe accounting storage"""
    return _storage_var.get()

def _set_storage(storage):
    """Set async-safe accounting storage"""
    _storage_var.set(storage)

def _set_context(context: AccountingContext):
    """Set async-safe accounting context"""
    _context_var.set(context)

# ================================
# PUBLIC API FOR CONTEXT MANAGEMENT
# ================================

def set_context(**kwargs):
    """Set accounting context fields"""
    context = _get_context()
    context.update(**kwargs)

def get_context() -> Dict[str, Any]:
    """Get current accounting context as dict"""
    return _get_context().to_dict()

def set_component(component: str):
    """Set current component context"""
    _get_context().component = component

def clear_context():
    """Clear accounting context"""
    _context_var.set(None)

def get_enrichment() -> Dict[str, Any]: return dict(_get_context().event_enrichment or {})

# ================================
# STORAGE INTERFACE AND IMPLEMENTATIONS
# ================================

class ServiceType(str, Enum):
    """Types of AI services that can be tracked"""
    LLM = "llm"
    EMBEDDING = "embedding"
    WEB_SEARCH = "web_search"
    IMAGE_GENERATION = "image_generation"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    VISION = "vision"
    OTHER = "other"

@dataclass
class SystemResource:
    """System resource identifier"""
    resource_type: str
    resource_id: str
    rn: str
    resource_version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AccountingEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # dynamic snapshot of context at event creation
    context: Dict[str, Any] = field(default_factory=dict)

    # Service details
    service_type: ServiceType = ServiceType.OTHER
    provider: str = ""
    model_or_service: str = ""

    # Caller-provided resources/metadata
    seed_system_resources: List[SystemResource] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Usage & status
    usage: ServiceUsage = field(default_factory=ServiceUsage)
    success: bool = True
    error_message: Optional[str] = None
    provider_request_id: Optional[str] = None

    # ---- Backward-compatible properties (optional but helpful) ----
    def _ctx_get(self, key: str): return self.context.get(key)
    def _ctx_set(self, key: str, value: Any): self.context.__setitem__(key, value)

    @property
    def user_id(self): return self._ctx_get("user_id")
    @user_id.setter
    def user_id(self, v): self._ctx_set("user_id", v)

    @property
    def session_id(self): return self._ctx_get("session_id")
    @session_id.setter
    def session_id(self, v): self._ctx_set("session_id", v)

    @property
    def project_id(self): return self._ctx_get("project_id")
    @project_id.setter
    def project_id(self, v): self._ctx_set("project_id", v)

    @property
    def tenant_id(self): return self._ctx_get("tenant_id")
    @tenant_id.setter
    def tenant_id(self, v): self._ctx_set("tenant_id", v)

    @property
    def request_id(self): return self._ctx_get("request_id")
    @request_id.setter
    def request_id(self, v): self._ctx_set("request_id", v)

    @property
    def component(self): return self._ctx_get("component")
    @component.setter
    def component(self, v): self._ctx_set("component", v)

    @property
    def app_bundle_id(self): return self._ctx_get("app_bundle_id")
    @app_bundle_id.setter
    def app_bundle_id(self, v): self._ctx_set("app_bundle_id", v)

    # ---- Serialization helpers ----
    @staticmethod
    def _compact(obj: Dict[str, Any]) -> Dict[str, Any]:
        def keep(v):
            return not (v is None or v == "" or v == [] or v == {} or v == 0)
        return {k: v for k, v in obj.items() if keep(v)}

    def to_dict(self) -> Dict[str, Any]:
        # Flatten selected context keys to the root (only non-null)
        flat_ctx = {k: v for k, v in self.context.items()
                    if k in CONTEXT_EXPORT_KEYS and v is not None}

        # Optionally filter metadata duplicates (don’t repeat root keys)
        filtered_meta = {k: v for k, v in self.metadata.items()
                         if k not in flat_ctx}  # drop duplicates

        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            **flat_ctx,  # user_id, session_id, tenant_id, app_bundle_id, etc.
            "service_type": self.service_type.value if hasattr(self.service_type, "value") else str(self.service_type),
            "provider": self.provider,
            "model_or_service": self.model_or_service,
            "seed_system_resources": [
                {
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "rn": r.rn,
                    "resource_version": r.resource_version,
                    "metadata": r.metadata
                } for r in self.seed_system_resources
            ],
            "usage": self.usage.to_compact_dict(),
            "success": self.success,
            "error_message": self.error_message,
            "provider_request_id": self.provider_request_id,
            "metadata": filtered_meta,
            # Optional: keep the full context payload too (handy for forensics)
            "context": {k: v for k, v in self.context.items() if k not in flat_ctx}
        }

class IAccountingStorage(ABC):
    """Storage interface for accounting events"""

    @abstractmethod
    async def store_event(self, event: AccountingEvent) -> bool: ...

class FileAccountingStorage(IAccountingStorage):
    def __init__(self, storage_backend, base_path: str = "accounting",
                 path_strategy: Optional[Callable[['AccountingEvent'], str]] = None):
        self.storage_backend = storage_backend
        self.base_path = base_path.strip("/")
        self.logger = logging.getLogger(self.__class__.__name__)
        self.path_strategy = path_strategy

    def _default_path(self, event: AccountingEvent) -> str:
        dt = datetime.fromisoformat(event.timestamp.replace("Z","+00:00")) if event.timestamp else datetime.now()
        date_path = f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}"
        tenant = event.tenant_id or "unknown"
        project = event.project_id or "unknown"
        return f"{self.base_path}/{tenant}/{project}/{date_path}/{event.service_type.value}/{event.event_id}.json"

    async def store_event(self, event: AccountingEvent) -> bool:
        try:
            rel_path = f"{self.base_path}/{self.path_strategy(event)}" if self.path_strategy else self._default_path(event)
            content = json.dumps(event.to_dict(), indent=2)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self.storage_backend.write_text(rel_path, content))
            return True
        except Exception as e:
            self.logger.error(f"Failed to store accounting event {event.event_id}: {e}")
            return False

class NoOpAccountingStorage(IAccountingStorage):
    """No-op storage for when accounting is disabled"""

    async def store_event(self, event: AccountingEvent) -> bool:
        return True

# ================================
# DECORATOR SYSTEM
# ================================

class AccountingTracker:
    """Base class for tracking decorators"""

    def __init__(self,
                 service_type: ServiceType,
                 provider_extractor: Optional[Callable] = None,
                 model_extractor: Optional[Callable] = None,
                 usage_extractor: Optional[Callable] = None,
                 metadata_extractor: Optional[Callable] = None):
        self.service_type = service_type
        self.provider_extractor = provider_extractor
        self.model_extractor = model_extractor
        self.usage_extractor = usage_extractor
        self.metadata_extractor = metadata_extractor

    def _extract_provider(self, *a, **kw) -> str:
        """Extract provider name"""
        if self.provider_extractor: return self.provider_extractor(*a, **kw)
        for arg in a:
            if hasattr(arg, "provider") and hasattr(arg.provider, "provider"):
                pv = arg.provider.provider
                return pv.value if hasattr(pv, "value") else str(pv)
        if "model" in kw and hasattr(kw["model"], "provider"):
            pv = kw["model"].provider.provider
            return pv.value if hasattr(pv, "value") else str(pv)
        return "unknown"

    def _extract_model(self, *args, **kwargs) -> str:
        """Extract model name"""
        if self.model_extractor:
            return self.model_extractor(*args, **kwargs)

        if 'model' in kwargs:
            model_record = kwargs['model']
            if hasattr(model_record, 'systemName'):
                return model_record.systemName

        return "unknown"

    def _extract_usage(self, result: Any, *args, **kwargs) -> ServiceUsage:
        """Extract usage from result"""
        if self.usage_extractor:
            return self.usage_extractor(result, *args, **kwargs)

        # Default extraction based on result type
        if hasattr(result, 'usage') and result.usage:
            usage_obj = result.usage
            return ServiceUsage(
                input_tokens=getattr(usage_obj, 'input_tokens', 0),
                output_tokens=getattr(usage_obj, 'output_tokens', 0),
                cache_creation_tokens=getattr(usage_obj, 'cache_creation_tokens', 0),
                cache_read_tokens=getattr(usage_obj, 'cache_read_tokens', 0),
                total_tokens=getattr(usage_obj, 'total_tokens', 0),
                embedding_tokens=getattr(usage_obj, 'embedding_tokens', 0),
                embedding_dimensions=getattr(usage_obj, 'embedding_dimensions', 0),
                requests=1
            )

        # For embedding results (list of floats)
        if isinstance(result, list) and result and isinstance(result[0], (int, float)):
            return ServiceUsage(
                embedding_dimensions=len(result),
                requests=1
            )

        return ServiceUsage(requests=1)

    def _extract_metadata(self, *args, **kwargs) -> Dict[str, Any]:
        """Extract additional metadata"""
        if self.metadata_extractor:
            return self.metadata_extractor(*args, **kwargs)
        return {}

    def _create_event(self, result: Any, exception: Optional[Exception],
                      start_time: datetime, *args, **kwargs) -> AccountingEvent:
        """Create accounting event"""

        context = _get_context()

        enrich = context.event_enrichment or {}

        # Extract information
        provider = self._extract_provider(*args, **kwargs)
        model = self._extract_model(*args, **kwargs)
        usage = self._extract_usage(result, *args, **kwargs)
        meta = self._extract_metadata(*args, **kwargs)

        # merge in enrichment metadata (caller-provided data)
        extra_meta = dict(enrich.get("metadata") or {})
        elapsed_ms =  (datetime.now() - start_time).total_seconds() * 1000
        extra_meta["processing_time_ms"] = round(elapsed_ms, 3)
        meta.update(extra_meta)

        # caller-provided resources (or none)
        seeds: List[SystemResource] = enrich.get("seed_system_resources") or []

        # Determine success and error
        success = exception is None
        error_message = str(exception) if exception else None

        # Provider request ID
        provider_request_id = None
        if hasattr(result, 'provider_message_id'):
            provider_request_id = result.provider_message_id

        context_snapshot = context.to_dict()
        return AccountingEvent(
            context=context_snapshot,
            service_type=self.service_type,
            provider=provider,
            model_or_service=model,
            seed_system_resources=seeds,
            usage=usage,
            success=success,
            error_message=error_message,
            provider_request_id=provider_request_id,
            metadata=meta
        )

    def __call__(self, func: Callable) -> Callable:
        """Decorator implementation"""

        if asyncio.iscoroutinefunction(func):
            import functools
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                storage = _get_storage()
                if not storage:
                    # No storage configured, just call function
                    return await func(*args, **kwargs)

                start_time = datetime.now()
                result = None
                exception = None

                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    exception = e
                    raise
                finally:
                    try:
                        event = self._create_event(result, exception, start_time, *args, **kwargs)
                        # store_coro = storage.store_event(event)
                        # asyncio.create_task(store_coro)
                        await storage.store_event(event)
                    except Exception as e:
                        # Log but don't fail the original function
                        logging.getLogger("accounting").error(f"Failed to create accounting event: {e}")

            return async_wrapper
        else:
            import functools
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                storage = _get_storage()
                if not storage:
                    return func(*args, **kwargs)

                start_time = datetime.now()
                result = None
                exception = None

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    exception = e
                    raise
                finally:
                    try:
                        event = self._create_event(result, exception, start_time, *args, **kwargs)

                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(storage.store_event(event))
                            else:
                                loop.run_until_complete(storage.store_event(event))
                        except RuntimeError:
                            # No event loop running, create new one
                            asyncio.run(storage.store_event(event))
                    except Exception as e:
                        logging.getLogger("accounting").error(f"Failed to create accounting event: {e}")

            return sync_wrapper

# ================================
# PREDEFINED TRACKERS
# ================================

def track_llm(provider_extractor=None, model_extractor=None,
              usage_extractor=None, metadata_extractor=None):
    """Decorator for tracking LLM usage"""
    return AccountingTracker(
        ServiceType.LLM, provider_extractor, model_extractor,
        usage_extractor, metadata_extractor
    )

def track_embedding(provider_extractor=None, model_extractor=None,
                    usage_extractor=None, metadata_extractor=None):
    """Decorator for tracking embedding usage"""
    return AccountingTracker(
        ServiceType.EMBEDDING, provider_extractor, model_extractor,
        usage_extractor, metadata_extractor
    )

def track_web_search(provider_extractor=None, model_extractor=None,
                     usage_extractor=None, metadata_extractor=None):
    """Decorator for tracking web search usage"""
    return AccountingTracker(
        ServiceType.WEB_SEARCH, provider_extractor, model_extractor,
        usage_extractor, metadata_extractor
    )

# ================================
# ACCOUNTING SYSTEM INTERFACE
# ================================

class AccountingSystem:
    """Main accounting system interface"""

    @staticmethod
    def init_storage(storage_backend,
                     enabled: bool = True,
                     *,
                     base_path: str = "accounting",
                     path_strategy: Optional[Callable[['AccountingEvent'], str]] = None):
        """Initialize accounting storage in context"""
        global _default_storage
        if enabled:
            if not path_strategy:
                path_strategy = grouped_by_component_and_seed()
            _default_storage = FileAccountingStorage(storage_backend, base_path=base_path, path_strategy=path_strategy)
        else:
            _default_storage = NoOpAccountingStorage()
        _set_storage(_default_storage)

    @staticmethod
    def set_context(**kwargs):
        """Set accounting context"""
        set_context(**kwargs)

    @staticmethod
    def set_component(component: str):
        """Set current component context"""
        set_component(component)

    @staticmethod
    def get_context() -> Dict[str, Any]:
        """Get current context"""
        return get_context()

    @staticmethod
    def clear_context():
        """Clear context"""
        clear_context()

# ================================
# USAGE HELPERS
# ================================

# Sentinel used to mark "key was absent" in context overlays
_MISSING = object()

class with_accounting:
    """Context manager for setting component and overlaying context keys (async safe)."""

    def __init__(self, component: str, **kwargs):
        self.component = component
        self._new_enrichment = kwargs or {}
        self.previous_component = None
        self._prev_enrichment = None

        # track context overlays so we can restore on exit
        self._overlaid_prev = {}  # key -> previous value or _MISSING

    def __enter__(self):
        ctx = _get_context()

        # remember current state
        self.previous_component = ctx.component
        self._prev_enrichment = dict(ctx.event_enrichment or {})

        # set/override component in the canonical context
        ctx.component = self.component

        # split kwargs: pull out enrichment keys using a sentinel so static analysis
        new = dict(self._new_enrichment)

        enrich_patch = {}
        for key in ("metadata", "seed_system_resources"):
            val = new.pop(key, _MISSING)
            if val is not _MISSING:
                enrich_patch[key] = val

        # overlay all remaining keys into canonical context (stack semantics)
        # NOTE: we touch ctx._ctx here because we need to restore precisely.
        for k, v in new.items():
            self._overlaid_prev[k] = ctx._ctx.get(k, _MISSING)
            ctx._ctx[k] = v

        # also keep everything in enrichment (deep-merge)
        # also include the non-meta overlays in enrichment so it's visible there too
        enrich_patch.update(new)
        merged = _deep_merge(self._prev_enrichment, enrich_patch)
        ctx.event_enrichment = merged
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ctx = _get_context()

        # restore component and enrichment
        ctx.component = self.previous_component
        ctx.event_enrichment = self._prev_enrichment or {}

        # restore overlaid context keys
        for k, prev in self._overlaid_prev.items():
            if prev is _MISSING:
                ctx._ctx.pop(k, None)
            else:
                ctx._ctx[k] = prev

    async def __aenter__(self): return self.__enter__()
    async def __aexit__(self, *a): return self.__exit__(*a)

def grouped_by_component_and_seed() -> "callable":
    """
    Returns a function(event) -> relative path like:
      <tenant>/<project>/<YYYY.MM.DD>/<component|type|id|version>/usage_<timestamp>.json
    or, if no seeds:
      <tenant>/<project>/<YYYY.MM.DD>/<component>/usage_<timestamp>.json
    """
    def _strategy(event) -> str:
        # date folder: 2025.07.28
        dt = datetime.fromisoformat(event.timestamp.replace('Z', '+00:00')) if event.timestamp else datetime.now()
        date_folder = f"{dt.year:04d}.{dt.month:02d}.{dt.day:02d}"

        # group folder
        component = (event.component or event.context.get("component") or "unknown")
        tenant   = (event.tenant_id or event.context.get("tenant_id") or "unknown")
        project  = (event.project_id or event.context.get("project_id") or "unknown")

        service_type = event.service_type.value if hasattr(event.service_type, "value") else str(event.service_type)
        if event.seed_system_resources:
            r = event.seed_system_resources[0]  # pick the primary seed
            rtype = (r.resource_type or "res").strip()
            source_id = r.metadata.get("source_id") if r.metadata else None
            rid = (source_id or "unknown").strip()
            rver = str(r.resource_version) if r.resource_version is not None else "unknown"
            group = f"{component}|{rtype}|{rid}|{rver}"
        else:
            group = component

        # filename with ms; add short id suffix to avoid rare collisions
        ts = dt.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"usage_{ts}-{event.event_id[:8]}.json"

        # final path UNDER your base_path
        return f"{tenant}/{project}/{date_folder}/{service_type}/{group}/{filename}"
    return _strategy

def _new_context_with(**fields) -> AccountingContext:
    ctx = AccountingContext()
    ctx.update(**fields)
    return ctx

# ---------- portable snapshot/restore for Accounting ----------
def snapshot_ctxvars() -> dict:
    """
    Returns a JSON-friendly snapshot of accounting context & a storage marker.
    We DO NOT serialize the storage backend instance. The parent process that
    builds PORTABLE_SPEC should also include a storage config/factory id if needed.
    """
    ctx = _get_context()
    return {
        "context": ctx.to_dict(),
        "enrichment": dict(ctx.event_enrichment or {}),
        # Optional marker only; child side should init its own storage backend
        "storage_present": _get_storage() is not None,
    }

def restore_ctxvars(payload: dict, *, storage_backend=None, enabled: bool = True) -> None:
    """
    Re-create a fresh AccountingContext in the child and set it into _context_var.
    Optionally (re-)init storage using storage_backend supplied by bootstrap.
    """
    try:
        AccountingSystem.init_storage(storage_backend, enabled)  # storage_backend may be None
    except Exception:
        pass

    context = _new_context_with(**(payload.get("context") or {}))
    context.event_enrichment = dict(payload.get("enrichment") or {})
    _set_context(context)  # push the newly created context into the ContextVar


# ================================
# EXPORT API
# ================================

__all__ = [
    # Core system
    'AccountingSystem',

    # Decorators
    'track_llm',
    'track_embedding',
    'track_web_search',

    # Context management
    'with_accounting',
    'set_context',
    'set_component',
    'get_context',

    # Data classes
    'AccountingEvent',
    'ServiceUsage',
    'ServiceType',
    'SystemResource',

    # Storage
    'IAccountingStorage',
    'FileAccountingStorage',
    'NoOpAccountingStorage'
]