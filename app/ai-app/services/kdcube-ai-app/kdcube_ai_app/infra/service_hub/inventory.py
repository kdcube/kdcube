# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/inventory.py — minimal, clean, role-mapped, cached clients

import asyncio
import json
import os
import logging
from datetime import datetime
from uuid import uuid4

import aiohttp
import requests
import time
from typing import Optional, Any, Dict, List, AsyncIterator, Callable, Awaitable, TypedDict, Union

from pydantic import BaseModel, Field
from langchain_core.embeddings import Embeddings
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage, AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, EMBEDDERS, model_caps
from kdcube_ai_app.infra.accounting import track_llm
from kdcube_ai_app.infra.accounting.usage import (
    _structured_usage_extractor,
    _norm_usage_dict,
    _approx_tokens_by_chars,
    ServiceUsage,
    ClientConfigHint,
)
from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord, AIProvider, AIProviderName
from kdcube_ai_app.infra.llm.util import get_service_key_fn
from kdcube_ai_app.infra.embedding.embedding import get_embedding
from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec


# =========================
# ids/util
# =========================
def _mid(role: str, msg_ts: str | None = None) -> str:
    if not msg_ts:
        msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
    return f"{role}-{msg_ts}-{uuid4().hex[:8]}"


from langchain_openai import ChatOpenAI
def make_chat_openai(*, model: str, api_key: str,
                     temperature: float | None = None,
                     stream_usage: bool = True,
                     **extra_kwargs) -> ChatOpenAI:
    caps = model_caps(model)

    params = {
        "model": model,
        "api_key": api_key,
        "stream_usage": stream_usage,
        # ↓↓↓ important for built-in tools & annotations
        "output_version": "responses/v1",   # format blocks/annotations nicely
        "use_responses_api": True,          # routes when tools are present

        **extra_kwargs,
    }

    # Only include temperature if supported AND provided
    if temperature is not None and caps.get("temperature", True):
        params["temperature"] = float(temperature)

    return ChatOpenAI(**params)

# =========================
# Logging
# =========================
class AgentLogger:
    def __init__(self, name: str, log_level: str = "INFO"):
        self.logger = logging.getLogger(f"agent.{name}")
        self.logger.setLevel(getattr(logging, log_level.upper()))
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())
            # h = logging.StreamHandler()
            # h.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            # self.logger.addHandler(h)
        self.start_time = None
        self.execution_logs = []

    def start_operation(self, operation: str, **kwargs):
        self.start_time = time.time()
        log_data = {"operation": operation, "timestamp": datetime.now().isoformat(), "inputs": kwargs}
        self.logger.info(f"🚀 Starting {operation} - {json.dumps(log_data, indent=2)}")
        return log_data

    def log_step(self, step: str, data: Any = None, level: str = "INFO"):
        entry = {"step": step, "timestamp": datetime.now().isoformat(), "data": data or "No data"}
        if self.start_time:
            entry["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self.execution_logs.append(entry)
        getattr(self.logger, level.lower())(f"📋 {step} - {json.dumps(entry, indent=2, default=str)}")

    def log_model_call(self, model_name: str, prompt_length: int, response_length: int | None = None, success: bool = True):
        data = {"model": model_name, "prompt_length": prompt_length, "response_length": response_length,
                "success": success, "timestamp": datetime.now().isoformat()}
        if self.start_time:
            data["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self.logger.info(f"{'✅' if success else '❌'} Model Call - {json.dumps(data, indent=2)}")

    def log_error(self, error: Exception, context: str | None = None):
        data = {"error_type": type(error).__name__, "error_message": str(error), "context": context,
                "timestamp": datetime.now().isoformat()}
        if self.start_time:
            data["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self.logger.error(f"💥 Error - {json.dumps(data, indent=2)}")

    def finish_operation(self,
                         success: bool = True,
                         result_summary: str | None = None,
                         **kwargs):
        if not self.start_time:
            return
        total = time.time() - self.start_time
        summary = {"success": success, "total_time": f"{total:.2f}s", "result_summary": result_summary,
                   "total_steps": len(self.execution_logs), "timestamp": datetime.now().isoformat(),
                   **kwargs}
        self.logger.info(f"{'🎉' if success else '💥'} Operation Complete - {json.dumps(summary, indent=2)}")
        self.start_time = None
        self.execution_logs = []
        return summary

    def log(self, message: str, level: str = "INFO"):
        getattr(self.logger, level.lower())(message)

# =========================
# Config (+ role mapping)
# =========================
class ConfigRequest(BaseModel):
    # Keys
    openai_api_key: Optional[str] = None
    claude_api_key: Optional[str] = None

    # Global defaults
    selected_model: str = None   # used for default role mapping if role_models not provided

    # RAG embeddings
    selected_embedder: str = "openai-text-embedding-3-small"
    custom_embedding_endpoint: Optional[str] = None
    custom_embedding_model: Optional[str] = "sentence-transformers/all-MiniLM-L6-v2"
    custom_embedding_size: Optional[int] = 384

    # Feature toggles
    has_classifier: Optional[bool] = None         # override; else derive from MODEL_CONFIGS
    format_fix_enabled: bool = True               # enable JSON fixer on structured calls

    # Role mapping (bundle “template” can fill this)
    # {"classifier":{"provider":"openai","model":"o3-mini"}, ...}
    role_models: Optional[Dict[str, Dict[str, str]]] = None

    # Optional custom model endpoint (for your HF-style endpoint)
    custom_model_endpoint: Optional[str] = None
    custom_model_api_key: Optional[str] = None
    custom_model_name: Optional[str] = None

    # KB
    kb_search_endpoint: Optional[str] = None

    # Bundle selection
    agentic_bundle_id: Optional[str] = None

    # slow storage for bundle needs
    bundle_storage_url: Optional[str] = None

    tenant: Optional[str] = None
    project: Optional[str] = None

class Config:
    """
    Central config: keys, embedding, and ROLE → {provider, model} mapping.
    Back-compat: still exposes .provider and {classifier,query_writer,reranker,answer_generator}_model via properties.
    """
    def __init__(self,
                 openai_api_key: Optional[str] = None,
                 claude_api_key: Optional[str] = None,
                 embedding_model: Optional[str] = None,
                 default_llm_model: Optional[str] = None,
                 role_models: Optional[Dict[str, Dict[str, str]]] = None):
        # keys
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.claude_api_key = claude_api_key or os.getenv("ANTHROPIC_API_KEY", "")

        # embeddings (declarative)
        self.selected_embedder = "openai-text-embedding-3-small"
        self.embedder_config = EMBEDDERS.get(self.selected_embedder, EMBEDDERS["openai-text-embedding-3-small"])
        self.embedding_model = embedding_model or "text-embedding-3-small"
        self.custom_embedding_endpoint = None
        self.custom_embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        self.custom_embedding_size = 384

        # logging
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

        # format fix (Claude by default)
        self.format_fixer_model = "claude-3-haiku-20240307"
        self.format_fix_enabled = True

        self.default_llm_model = MODEL_CONFIGS.get(default_llm_model) or  MODEL_CONFIGS.get("o3-mini")
        # role map (filled later; defaults below)
        self.role_models: Dict[str, Dict[str, str]] = role_models or self.default_role_map()

        # custom endpoint support (for CustomModelClient)
        self.custom_model_endpoint = os.getenv("CUSTOM_MODEL_ENDPOINT", "")
        self.custom_model_api_key = os.getenv("CUSTOM_MODEL_API_KEY", "")
        self.custom_model_name = os.getenv("CUSTOM_MODEL_NAME", "custom-model")
        self.use_custom_endpoint = bool(self.custom_model_endpoint)

        # KB
        self.kb_search_url = os.getenv("KB_SEARCH_URL", None)

        # CB, bundles
        self.bundle_storage_url = os.getenv("CB_BUNDLE_STORAGE_URL", None)

        self.tenant = os.getenv("TENANT_ID", None)
        self.project = os.getenv("DEFAULT_PROJECT_NAME", None)

        self.ai_bundle_spec: Optional[BundleSpec] = None

    # ----- embedding config -----
    def set_embedder(self, embedder_id: str, custom_endpoint: str | None = None):
        if embedder_id not in EMBEDDERS:
            raise ValueError(f"Unknown embedder: {embedder_id}")
        self.selected_embedder = embedder_id
        self.embedder_config = EMBEDDERS[embedder_id]
        if self.embedder_config["provider"] == "custom":
            if not custom_endpoint:
                raise ValueError("Custom embedders require an endpoint")
            self.custom_embedding_endpoint = custom_endpoint
            self.custom_embedding_model = self.embedder_config["model_name"]
            self.custom_embedding_size = self.embedder_config["dim"]
        else:
            self.custom_embedding_endpoint = None
            self.embedding_model = self.embedder_config["model_name"]

    def set_custom_embedding_endpoint(self, endpoint: str, model: str | None = None, size: int | None = None):
        self.custom_embedding_endpoint = endpoint
        if model: self.custom_embedding_model = model
        if size:  self.custom_embedding_size = size

    def set_kb_search_endpoint(self, endpoint: str):
        self.kb_search_url = endpoint

    # ----- role map -----
    def default_role_map(self) -> Dict[str, Dict[str, str]]:
        """Defaults for our base roles; new roles will still get default provider/model lazily."""
        BASE_ROLES = ("classifier", "query_writer", "reranker", "answer_generator", "format_fixer")
        base = {r: {"provider": self.default_llm_model["provider"], "model": self.default_llm_model["model_name"]}
                for r in BASE_ROLES}
        # prefer Anthropic for format fixer
        base["format_fixer"] = {"provider": "anthropic", "model": self.format_fixer_model}
        return base

    def get_default_role_spec(self) -> Dict[str, str]:
        """Default spec for any role not explicitly configured."""
        return {"provider": self.default_llm_model["provider"], "model": self.default_llm_model["model_name"]}

    def ensure_role(self, role: str) -> Dict[str, str]:
        """Make sure a role has a mapping; if missing, fill with defaults (special-case format_fixer)."""
        if role not in self.role_models:
            if role == "format_fixer":
                self.role_models[role] = {"provider": "anthropic", "model": self.format_fixer_model}
            else:
                self.role_models[role] = self.get_default_role_spec()
        return self.role_models[role]

    def set_role_models(self, role_models: Dict[str, Dict[str, str]] | None):
        """
        Merge provided role map with defaults.
        - Known base roles get defaults unless overridden
        - Any EXTRA roles are accepted; if provider/model missing, fill from defaults
        """
        provided = role_models or {}
        defaults = self.default_role_map()
        merged: Dict[str, Dict[str, str]] = {}

        # start with defaults for base roles
        merged.update(defaults)

        # merge/insert provided roles (including brand-new ones)
        for r, spec in provided.items():
            prov = spec.get("provider") or merged.get(r, {}).get("provider") or self.default_llm_model["provider"]
            model = spec.get("model")    or merged.get(r, {}).get("model")    or self.default_llm_model["model_name"]
            merged[r] = {"provider": prov, "model": model}

        self.role_models = merged

    # ----- back-compat properties -----
    @property
    def provider(self) -> str:
        # “global provider” (legacy): just return the classifier’s provider
        return self.role_models.get("classifier", {}).get("provider", self.default_llm_model["provider"])

    @property
    def classifier_model(self) -> str:
        return self.role_models.get("classifier", {}).get("model", self.default_llm_model["model_name"])

    @property
    def query_writer_model(self) -> str:
        return self.role_models.get("query_writer", {}).get("model", self.default_llm_model["model_name"])

    @property
    def reranker_model(self) -> str:
        return self.role_models.get("reranker", {}).get("model", self.default_llm_model["model_name"])

    @property
    def answer_generator_model(self) -> str:
        return self.role_models.get("answer_generator", {}).get("model", self.default_llm_model["model_name"])


# =========================
# Config factory (accept bundle template or fill defaults)
# =========================
def create_workflow_config(config_request: ConfigRequest) -> Config:
    cfg = Config(default_llm_model=config_request.selected_model)

    # keys
    if config_request.openai_api_key:
        cfg.openai_api_key = config_request.openai_api_key
    if config_request.claude_api_key:
        cfg.claude_api_key = config_request.claude_api_key

    cfg.format_fix_enabled = bool(config_request.format_fix_enabled)

    # embeddings
    try:
        cfg.set_embedder(config_request.selected_embedder, config_request.custom_embedding_endpoint)
    except ValueError:
        if config_request.custom_embedding_endpoint:
            cfg.set_custom_embedding_endpoint(
                config_request.custom_embedding_endpoint,
                config_request.custom_embedding_model or "sentence-transformers/all-MiniLM-L6-v2",
                int(config_request.custom_embedding_size or 384),
                )

    # role models (template-filled or defaults)
    cfg.set_role_models(config_request.role_models)

    # custom endpoint
    if config_request.custom_model_endpoint:
        cfg.custom_model_endpoint = config_request.custom_model_endpoint
        cfg.custom_model_api_key = config_request.custom_model_api_key or ""
        cfg.custom_model_name = config_request.custom_model_name or "custom-model"
        cfg.use_custom_endpoint = True

    if config_request.kb_search_endpoint:
        cfg.set_kb_search_endpoint(config_request.kb_search_endpoint)

    if config_request.tenant:
        cfg.tenant = config_request.tenant
    if config_request.project:
        cfg.project = config_request.project

    return cfg

# =========================
# Provider-aware router (lazy, cached)
# =========================
class ModelRouter:
    """Creates/returns clients on demand; caches by (provider, model)."""
    def __init__(self, config: Config):
        self.config = config
        self.logger = AgentLogger("ModelRouter", config.log_level)
        self._cache: Dict[tuple, Any] = {}
        self._anthropic_client = None
        self._anthropic_async = None

    def _mk_openai(self, model: str, temperature: float) -> ChatOpenAI:
        return make_chat_openai(
            model=model,                      # e.g., "o3-mini" or "gpt-4o"
            api_key=self.config.openai_api_key,
            temperature=temperature,          # user knob (may be ignored for reasoning models)
            stream_usage=True
        )

    def _mk_custom(self, model: str, temperature: float):
        return CustomModelClient(
            endpoint=self.config.custom_model_endpoint,
            api_key=self.config.custom_model_api_key,
            model_name=model,
            temperature=temperature,
        )
    def _mk_anthropic(self):
        if self._anthropic_client:
            return self._anthropic_client
        try:
            import anthropic
            self._anthropic_client = anthropic.Anthropic(api_key=self.config.claude_api_key)
            return self._anthropic_client
        except ImportError:
            raise RuntimeError("anthropic package not available")

    def _mk_anthropic_async(self):
        if self._anthropic_async:
            return self._anthropic_async
        import anthropic
        self._anthropic_async = anthropic.AsyncAnthropic(api_key=self.config.claude_api_key)
        return self._anthropic_async

    def get_client(self, role: str, temperature: float) -> Optional[Any]:
        # ensure mapping exists even for new roles
        spec = self.config.ensure_role(role)
        if not spec:
            return None

        client = self._mk_openai(spec["model"], temperature)
        provider, model = spec["provider"], spec["model"]
        key = (provider, model, role, round(temperature, 3))

        if key in self._cache:
            return self._cache[key]

        if provider == "openai":
            client = self._mk_openai(model, temperature)
        elif provider == "anthropic":
            client = self._mk_anthropic()  # model applied at call
        elif provider == "custom":
            if not self.config.custom_model_endpoint:
                raise ValueError("Custom provider requires CUSTOM_MODEL_ENDPOINT")
            client = self._mk_custom(model, temperature)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        self._cache[key] = client
        return client

    def describe(self, role: str) -> ClientConfigHint:
        spec = self.config.ensure_role(role)
        return ClientConfigHint(provider=spec.get("provider", "unknown"),
                                model_name=spec.get("model", "unknown"))

# =========================
# Usage metadata helpers
# =========================
def ms_provider_extractor(model_service, client, *args, **kw) -> str:
    cfg = kw.get("client_cfg") or model_service.describe_client(client)
    return getattr(cfg, "provider", "unknown")

def ms_model_extractor(model_service, client, *args, **kw) -> str:
    cfg = kw.get("client_cfg") or model_service.describe_client(client)
    return getattr(cfg, "model_name", "unknown")

def ms_structured_meta_extractor(model_service, _client, system_prompt: str, user_message: str, response_format, **kw):
    client_cfg = kw.get("client_cfg")
    return {
        "model": (client_cfg.model_name if client_cfg else None),
        "provider": (client_cfg.provider if client_cfg else None),
        "expected_format": getattr(response_format, "__name__", str(response_format)),
        "prompt_chars": len(system_prompt or "") + len(user_message or ""),
        "temperature": kw.get("temperature"),
        "max_tokens": kw.get("max_tokens"),
    }

# def ms_freeform_meta_extractor(model_service, _client, messages, *a, **kw):
def ms_freeform_meta_extractor(model_service, _client = None, messages = None, *a, **kw):
    try:
        if not messages:
            messages = kw.get("messages") or []
        prompt_chars = sum(len(getattr(m, "content", "") or "") for m in (messages or []))
    except Exception:
        prompt_chars = 0
    client_cfg = kw.get("client_cfg")

    return {
        "selected_model": (client_cfg.model_name if client_cfg else None),
        "provider": (client_cfg.provider if client_cfg else None),
        "prompt_chars": prompt_chars,
        "temperature": kw.get("temperature"),
        "max_tokens": kw.get("max_tokens"),
    }

# =========================
# Format fixer
# =========================
class FormatFixerService:
    """Fixes malformed JSON responses using Claude"""
    def __init__(self, config: Config):
        self.config = config
        self.logger = AgentLogger("FormatFixer", config.log_level)
        try:
            import anthropic
            self.claude_client = anthropic.Anthropic(api_key=config.claude_api_key)
            self.logger.log_step("claude_client_initialized", {"model": config.format_fixer_model})
        except ImportError:
            self.claude_client = None
            self.logger.log_error(ImportError("anthropic package not available"), "Claude client initialization")

    async def fix_format(self, raw_output: str, expected_format: str, input_data: str, system_prompt: str) -> Dict[str, Any]:
        self.logger.start_operation("format_fixing",
                                    raw_output_length=len(raw_output),
                                    expected_format=expected_format,
                                    input_data_length=len(input_data),
                                    model=self.config.format_fixer_model,
                                    provider="anthropic"
                                    )
        if not self.claude_client:
            msg = "Claude client not available"
            self.logger.log_error(Exception(msg), "Client unavailable")
            self.logger.finish_operation(False, msg)
            return {"success": False, "error": msg, "raw": raw_output}
        try:
            fix_prompt = f"""You are a JSON format fixer. You receive malformed JSON output and need to fix it to match the expected format.

Original system prompt: {system_prompt}
Original input: {input_data}
Expected format: {expected_format}
Malformed output: {raw_output}

Please fix the JSON to match the expected format. Return only the fixed JSON, no additional text."""
            self.logger.log_step("sending_fix_request", {
                "model": self.config.format_fixer_model,
                "fix_prompt_length": len(fix_prompt),
                # "raw_output_preview": raw_output[:200] + "..." if len(raw_output) > 200 else raw_output
                "raw_output": raw_output
            })
            response = self.claude_client.messages.create(
                model=self.config.format_fixer_model,
                max_tokens=1000,
                messages=[{"role": "user", "content": fix_prompt}]
            )
            fixed_content = response.content[0].text
            try:
                parsed = json.loads(fixed_content)
                self.logger.log_step("fix_validation_successful", {"parsed_type": type(parsed).__name__})
                self.logger.finish_operation(True, "Format fixing successful")
                return {"success": True, "data": parsed, "raw": fixed_content}
            except json.JSONDecodeError as e:
                self.logger.log_error(e, "Fixed content still not valid JSON")
                self.logger.finish_operation(False, "Fixed content still invalid")
                return {"success": False, "error": "Fixed content is still not valid JSON", "raw": fixed_content}
        except Exception as e:
            self.logger.log_error(e, "Format fixing failed")
            self.logger.finish_operation(False, f"Format fixing failed: {str(e)}")
            return {"success": False, "error": str(e), "raw": raw_output}

from kdcube_ai_app.apps.chat.reg import EMBEDDERS
def embedding_model() -> ModelRecord:
    provider_name = AIProviderName.open_ai
    provider = AIProvider(provider=provider_name, apiToken=get_service_key_fn(provider_name))
    model_config = EMBEDDERS.get("openai-text-embedding-3-small")
    model_name = model_config.get("model_name")
    return ModelRecord(
        modelType="base",
        status="active",
        provider=provider,
        systemName=model_name,
    )

# =========================
# Model service — thin, tidy, role-first
# =========================
class ModelServiceBase:
    """
    Role-aware, provider-agnostic.
    - Use router.get_client(role, temperature)
    - `describe_client(client, role)` returns provider/model for accounting
    - Back-compat props: .classifier_client etc. (lazy via router)
    """
    def __init__(self, config: Config):
        self.config = config
        self.logger = AgentLogger("ModelServiceBase", config.log_level)
        self.router = ModelRouter(config)
        self.format_fixer = FormatFixerService(config)
        self._anthropic_async = None

        self._emb_model = embedding_model()

    # ---------- back-compat clients (lazily resolved) ----------
    @property
    def classifier_client(self):       return self.router.get_client("classifier", 0.1)
    @property
    def query_writer_client(self):     return self.router.get_client("query_writer", 0.3)
    @property
    def reranker_client(self):         return self.router.get_client("reranker", 0.1)
    @property
    def answer_generator_client(self): return self.router.get_client("answer_generator", 0.3)

    def get_client(self, role, temperature: float = 0.7 ):
        return self.router.get_client(role, temperature)

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Uses your accounting-aware embedding path via get_embedding().
        Kept simple; if you want to offload to thread pool, you can wrap get_embedding in run_in_executor.
        """
        out: List[List[float]] = []
        for t in texts:
            out.append(get_embedding(model=self._emb_model, text=t))
        return out

    # ---------- helper ----------
    def describe_client(self, client, role: Optional[str] = None) -> ClientConfigHint:
        # Prefer role mapping when provided
        if role:
            return self.router.describe(role)

        # Fallback best-effort (used by accounting hooks)
        if isinstance(client, CustomModelClient):
            return ClientConfigHint(provider="custom", model_name=client.model_name)
        if isinstance(client, ChatOpenAI):
            return ClientConfigHint(provider="openai", model_name=getattr(client, "model", "unknown"))
        if hasattr(client, "messages"):  # Anthropic SDK
            # derive a role-less hint (less precise)
            return ClientConfigHint(provider="anthropic", model_name="(role-mapped at call)")
        return ClientConfigHint(provider="unknown", model_name="unknown")

    # ---------- structured ----------
    @track_llm(
        provider_extractor=ms_provider_extractor,
        model_extractor=ms_model_extractor,
        usage_extractor=_structured_usage_extractor,
        metadata_extractor=ms_structured_meta_extractor,
    )
    async def call_model_with_structure(
            self,
            client,
            system_prompt: str,
            user_message: str,
            response_format: BaseModel,
            *,
            client_cfg: Optional[ClientConfigHint] = None,
            role: Optional[str] = None,
            temperature: float = 0.2,
            max_tokens: int = 1200,
    ) -> Dict[str, Any]:

        self.logger.start_operation(
            "model_call_structured",
            system_prompt_length=len(system_prompt),
            user_message_length=len(user_message),
            expected_format=response_format.__name__,
        )

        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))
        provider_name, model_name = cfg.provider, cfg.model_name
        usage: Dict[str, int] = {}
        provider_message_id = None

        try:
            self.logger.log_step("sending_request", {
                "preview": (system_prompt + "\n\n" + user_message)[:240]
            })

            # ---- provider dispatch ----
            if provider_name == "anthropic" and hasattr(client, "messages"):
                # Anthropic SDK; model chosen via mapping
                resp = client.messages.create(
                    model=model_name,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                response_content = "".join([p.text for p in getattr(resp, "content", []) if getattr(p, "type", "") == "text"])
                u = getattr(resp, "usage", None)
                if u:
                    usage = {
                        "input_tokens": getattr(u, "input_tokens", 0) or 0,
                        "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        "total_tokens": (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0),
                    }
                provider_message_id = getattr(resp, "id", None)

            else:
                # OpenAI (LangChain) or Custom endpoint (returns AIMessage)
                ai_msg = await client.ainvoke(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_message),
                    ],
                    # nudge OpenAI to strict JSON
                    response_format={"type": "json_object"},
                )
                response_content = ai_msg.content
                usage = (
                        getattr(ai_msg, "usage_metadata", None)
                        or (getattr(ai_msg, "response_metadata", {}) or {}).get("token_usage")
                        or (getattr(ai_msg, "additional_kwargs", {}) or {}).get("usage")
                        or {}
                )
                provider_message_id = (
                        (getattr(ai_msg, "response_metadata", {}) or {}).get("id")
                        or (getattr(ai_msg, "additional_kwargs", {}) or {}).get("provider_message_id")
                )

            self.logger.log_model_call(model_name, len(system_prompt) + len(user_message), len(response_content), True)

            # ---- parse + validate; try fixer once if enabled ----
            def _ok(parsed):
                try:
                    response_format.model_validate(parsed)
                    return True
                except Exception:
                    return False

            try:
                parsed = json.loads(response_content)
                if not _ok(parsed):
                    raise ValueError("Pydantic validation failed")
                validated = response_format.model_validate(parsed)
                self.logger.finish_operation(True,
                                             f"Parsed {response_format.__name__}",
                                             model=model_name,
                                             provider=provider_name,)
                return {
                    "success": True,
                    "data": validated.model_dump(),
                    "raw": response_content,
                    "usage": _norm_usage_dict(usage) if usage else _approx_tokens_by_chars(system_prompt + user_message),
                    "provider_message_id": provider_message_id,
                    "model_name": model_name,
                }
            except Exception as e:
                self.logger.log_error(e, "JSON parsing/validation failed")
                if self.config.format_fix_enabled and role != "format_fixer":
                    fix = await self.format_fixer.fix_format(
                        raw_output=response_content,
                        expected_format=response_format.__name__,
                        input_data=user_message,
                        system_prompt=system_prompt,
                    )
                    if fix.get("success"):
                        try:
                            validated = response_format.model_validate(fix["data"])
                            self.logger.finish_operation(True,
                                                         "Parsed after FormatFixer",
                                                         model=self.format_fixer.config.format_fixer_model,
                                                         provider="anthropic",)
                            return {
                                "success": True,
                                "data": validated.model_dump(),
                                "raw": fix.get("raw") or response_content,
                                "usage": _norm_usage_dict(usage) if usage else _approx_tokens_by_chars(system_prompt + user_message),
                                "provider_message_id": provider_message_id,
                                "model_name": model_name,
                            }
                        except Exception as ve:
                            self.logger.log_error(ve, "Validation failed after FormatFixer")

                self.logger.finish_operation(False, "Parsing failed")
                return {
                    "success": False,
                    "error": "Failed to parse/validate structured output",
                    "raw": response_content,
                    "usage": _norm_usage_dict(usage) if usage else _approx_tokens_by_chars(system_prompt + user_message),
                    "provider_message_id": provider_message_id,
                    "model_name": model_name,
                }

        except Exception as e:
            self.logger.log_error(e, "Model API call failed")
            self.logger.log_model_call(model_name, len(system_prompt) + len(user_message), success=False)
            self.logger.finish_operation(False, f"API call failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "raw": None,
                "usage": _approx_tokens_by_chars(system_prompt + user_message),
                "provider_message_id": provider_message_id,
                "model_name": model_name,
            }

    # Convenience: call by role (no need to fetch client outside)
    async def call_structured_role(self, role: str, system_prompt: str, user_message: str, response_format: BaseModel,
                                   *, temperature: float = 0.2, max_tokens: int = 1200):
        client = self.router.get_client(role, temperature)
        return await self.call_model_with_structure(
            client, system_prompt, user_message, response_format,
            client_cfg=self.router.describe(role), role=role,
            temperature=temperature, max_tokens=max_tokens,
        )

    # ---------- freeform ----------
    @staticmethod
    def _freeform_usage_extractor(result, *_a, **_kw) -> ServiceUsage:
        try:
            u = _norm_usage_dict(result.get("usage") or {})
            return ServiceUsage(input_tokens=u["prompt_tokens"], output_tokens=u["completion_tokens"], total_tokens=u["total_tokens"], requests=1)
        except Exception:
            return ServiceUsage(requests=1)

    @track_llm(
        provider_extractor=ms_provider_extractor,
        model_extractor=ms_model_extractor,
        usage_extractor=_structured_usage_extractor,
        metadata_extractor=ms_freeform_meta_extractor,
    )
    async def call_model_text(
            self,
            client,
            messages: List[BaseMessage],
            *,
            temperature: Optional[float] = 0.3,
            max_tokens: Optional[int] = 1200,
            client_cfg: ClientConfigHint | None = None,
            role: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))
        provider_name, model_name = cfg.provider, cfg.model_name
        usage = {}
        provider_message_id = None

        try:
            if provider_name == "anthropic" and hasattr(client, "messages"):
                # Convert LC messages to Anthropic format
                sys_prompt = None
                convo = []
                for m in messages:
                    if isinstance(m, SystemMessage):
                        sys_prompt = (sys_prompt + "\n" + m.content) if sys_prompt else m.content
                    elif isinstance(m, HumanMessage):
                        convo.append({"role": "user", "content": m.content})
                    elif isinstance(m, AIMessage):
                        convo.append({"role": "assistant", "content": m.content})
                    else:
                        convo.append({"role": "user", "content": str(getattr(m, "content", ""))})
                resp = client.messages.create(
                    model=model_name, system=sys_prompt, messages=convo,
                    max_tokens=max_tokens or 1200, temperature=temperature if temperature is not None else 0.3,
                )
                text = "".join([c.text for c in getattr(resp, "content", []) if getattr(c, "type", "") == "text"])
                u = getattr(resp, "usage", None)
                if u:
                    usage = {
                        "input_tokens": getattr(u, "input_tokens", 0) or 0,
                        "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        "total_tokens": (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0),
                    }
                provider_message_id = getattr(resp, "id", None)
            else:
                ai_msg = await client.ainvoke(messages)
                text = ai_msg.content
                usage = (getattr(ai_msg, "usage_metadata", None)
                         or (getattr(ai_msg, "response_metadata", {}) or {}).get("token_usage")
                         or {})
                provider_message_id = (getattr(ai_msg, "response_metadata", {}) or {}).get("id")

            if not usage:
                approx = _approx_tokens_by_chars("".join((getattr(m, "content", "") or "") for m in messages))
                usage = approx

            return {"text": text, "usage": _norm_usage_dict(usage),
                    "provider_message_id": provider_message_id, "model_name": model_name}

        except Exception as e:
            self.logger.log_error(e, "freeform model call failed")
            return {"text": f"Model call failed: {e}",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "provider_message_id": None, "model_name": model_name}


    # ---------- streaming (kept, but slimmer) ----------
    async def stream_model_text(
            self,
            client,
            messages: List[BaseMessage],
            *,
            temperature: float = 0.3,
            max_tokens: int = 1200,
            client_cfg: ClientConfigHint | None = None,
            role: Optional[str] = None,
            tools: Optional[list] = None,
            tool_choice: Optional[Union[str, dict]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))
        provider_name, model_name = cfg.provider, cfg.model_name

        index = -1
        # Anthropic streaming
        if provider_name == "anthropic":
            import anthropic
            async_client = getattr(self.router, "_mk_anthropic_async")()
            sys_prompt = None
            convo = []
            for m in messages:
                if isinstance(m, SystemMessage):
                    sys_prompt = (sys_prompt + "\n" + m.content) if sys_prompt else m.content
                elif isinstance(m, HumanMessage):
                    convo.append({"role": "user", "content": m.content})
                elif isinstance(m, AIMessage):
                    convo.append({"role": "assistant", "content": m.content})
                else:
                    convo.append({"role": "user", "content": str(getattr(m, "content", ""))})

            async with async_client.messages.stream(
                    model=model_name,
                    system=sys_prompt,
                    messages=convo,
                    max_tokens=max_tokens,
                    temperature=temperature,
            ) as stream:
                index += 1
                async for text in stream.text_stream:
                    if text:
                        # yield {"event": "text.delta", "text": text, "delta": text, "index": index}
                        yield {"event": "text.delta", "text": text, "index": index}

                usage = {}
                final_obj = None
                if hasattr(stream, "get_final_message"):
                    final_obj = await stream.get_final_message()
                elif hasattr(stream, "get_final_response"):
                    # older SDKs returned a sync method; guard it
                    maybe = stream.get_final_response()
                    final_obj = await maybe if asyncio.iscoroutine(maybe) else maybe

                if final_obj is not None:
                    u = getattr(final_obj, "usage", None)
                    if u:
                        usage = {
                            "input_tokens":  getattr(u, "input_tokens", 0) or 0,
                            "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        }
                        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

                yield {"event": "final", "usage": _norm_usage_dict(usage), "model_name": model_name, "index": index}
            return

        # OpenAI streaming
        if isinstance(client, ChatOpenAI):
            model_limitations = model_caps(model_name)
            tools_support = model_limitations.get("tools", False)
            reasoning_support = model_limitations.get("reasoning", False)
            temperature_supported = model_limitations.get("temperature", False)

            stream_kwargs = {
                "max_output_tokens": max_tokens,
                "extra_body": {
                    "text": {"format": {"type": "text"}, "verbosity": "medium"},
                },
            }
            if temperature_supported:
                stream_kwargs["temperature"] = temperature
            if tools and tools_support:
                stream_kwargs["tools"] = tools
                if tool_choice is not None:
                    stream_kwargs["tool_choice"] = tool_choice
                stream_kwargs["parallel_tool_calls"] = False
                web_search_tool = next((t for t in (tools or []) if t.get("type") == "web_search"), None)
                if web_search_tool:
                   stream_kwargs["extra_body"]["include"] = ["web_search_call.action.sources"]
            if reasoning_support:
                stream_kwargs["extra_body"]["reasoning"] = {"effort": "medium", "summary": "auto"}

            usage, seen_citation_urls = {}, set()
            source_registry: dict[str, dict] = {}
            def _norm_url(u: str) -> str:
                # simple normalization: strip whitespace + trailing slash
                # (you can expand this: lowercase host, drop utm_* params, etc.)
                if not u: return u
                u = u.strip()
                if u.endswith("/"): u = u[:-1]
                return u

            async for chunk in client.astream(messages, **stream_kwargs):
                from langchain_core.messages import AIMessageChunk
                index += 1
                if not isinstance(chunk, AIMessageChunk):
                    txt = getattr(chunk, "content", "") or getattr(chunk, "text", "")
                    if txt:
                        yield {"delta": txt, "index": index}
                        yield {"event": "text.delta", "text": txt, "stage": -1}
                    continue
                yield {"all_event": chunk, "index": index}  # for debugging

                if getattr(chunk, "usage_metadata", None):
                    um = chunk.usage_metadata or {}
                    usage = {
                        "input_tokens": um.get("input_tokens", 0) or 0,
                        "output_tokens": um.get("output_tokens", 0) or 0,
                        "input_tokens_details": um.get("input_token_details", {}), # {'cache_read': 233344}
                        "output_tokens_details": um.get("output_token_details", {}), # {'reasoning': 1344}
                    }
                    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

                blocks = chunk.content if isinstance(chunk.content, list) else [{"type": "text", "text": chunk.content}]
                for b in blocks:
                    btype = b.get("type")
                    bid = b.get("index")  # 'stage' number. it's when agentic models / tools nodes interleave

                    # 1) Handle annotations
                    anns = b.get("annotations") or []
                    for ann in anns:
                        if ann.get("type") == "url_citation":
                            url = _norm_url(ann.get("url"))
                            if not url:
                                continue
                            # mark as used in registry
                            rec = source_registry.get(url) or {
                                "url": url,
                                "title": ann.get("title"),
                                "first_seen_stage": bid,
                                "tool_ids": set(),
                                "ranks": [],
                                "used_in_output": False,
                                "citations": [],
                            }
                            rec["used_in_output"] = True
                            rec["citations"].append({"start": ann.get("start_index"), "end": ann.get("end_index")})
                            if not rec.get("title") and ann.get("title"):
                                rec["title"] = ann["title"]
                            source_registry[url] = rec

                            # your existing outward-facing event (optional dedupe)
                            if url not in seen_citation_urls:
                                seen_citation_urls.add(url)
                                yield {
                                    "event": "citation",
                                    "title": ann.get("title"),
                                    "url": url,
                                    "start": ann.get("start_index"),
                                    "end": ann.get("end_index"),
                                    "index": index,
                                    "stage": bid,
                                }
                    # 2) text / output_text deltas
                    if btype in ("output_text", "text"):
                        delta = b.get("text") or ""
                        if delta:
                            yield {"event": "text.delta", "text": delta, "index": index, "stage": bid}
                    # 3) Handle reasoning
                    if btype == "reasoning":
                        # Ignore encrypted_content; only summaries are visible
                        for si in b.get("summary") or []:
                            if si:
                                order_in_group = 0
                                if isinstance(si, dict):
                                    s = si.get("text") or si.get("summary_text") or ""
                                    order_in_group = si.get("index")
                                else: s = si
                                yield {"event": "thinking.delta", "text": s, "stage": bid, "index": index, "group_index": order_in_group}
                    # 4) Handle tool calls
                    if btype == "web_search_call":
                        action = b.get("action") or {}
                        status = b.get("status")       # "in_progress" | "searching" | "completed"
                        evt = {
                            "event": "tool.search",
                            "id": b.get("id"), # fingerprint of this tool execution. # "stage": bid
                            "status": status,
                            "query": action.get("query"),
                            "sources": action.get("sources"),
                            "index": index
                        }
                        yield evt

            # 5) Finish (emit both rich and legacy finals)
            index += 1
            yield {"event": "final", "usage": usage, "model_name": model_name, "index": index}
            return

        # Custom endpoint streaming
        if isinstance(client, CustomModelClient):
            async for ev in client.astream(messages, temperature=temperature, max_new_tokens=max_tokens):
                yield ev
            return

        # Fallback: non-stream -> fake stream
        res = await self.call_model_text(client, messages, temperature=temperature, max_tokens=max_tokens, client_cfg=cfg)
        text = res.get("text", "") or ""
        for i in range(0, len(text), 30):
            yield {"delta": text[i:i+30]}
        yield {"event": "final", "usage": res.get("usage", {}), "model_name": res.get("model_name", model_name)}

    @track_llm(
        provider_extractor=ms_provider_extractor,
        model_extractor=ms_model_extractor,
        usage_extractor=_freeform_usage_extractor,
        metadata_extractor=ms_freeform_meta_extractor,
    )
    async def stream_model_text_tracked(
            self,
            client,
            messages: List[BaseMessage],
            *,
            on_delta: Callable[[str], Awaitable[None]],
            on_thinking: Optional[Callable[[Any], Awaitable[None]]] = None,
            on_tool_result_event: Optional[Callable[[Any], Awaitable[None]]] = None,
            on_event: Optional[Callable[[dict], Awaitable[None]]] = None,
            temperature: float = 0.3,
            max_tokens: int = 1200,
            client_cfg: ClientConfigHint | None = None,
            role: Optional[str] = None,
            on_complete: Optional[Callable[[dict], Awaitable[None]]] = None,
            debug: bool = True,
            tools: Optional[list] = None,
            tool_choice: Optional[Union[str, dict]] = None,
    ) -> Dict[str, Any]:
        # dedicated streaming logger (new instance as requested)
        slog = AgentLogger("StreamTracker", self.config.log_level)
        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))

        # helpful context + previews
        def _msg_preview(ms: List[BaseMessage]) -> dict:
            try:
                sys = next((m.content for m in ms if isinstance(m, SystemMessage)), "")[:200]
                usr = next((m.content for m in ms if isinstance(m, HumanMessage)), "")[:200]
            except Exception:
                sys, usr = "", ""
            return {"system_preview": sys, "user_preview": usr}

        slog.start_operation(
            "stream_model_text_tracked",
            provider=cfg.provider,
            model=cfg.model_name,
            role=role,
            temperature=temperature,
            max_tokens=max_tokens,
            msg_count=len(messages),
            **_msg_preview(messages),
        )

        final_chunks: list[str] = []
        usage_out: Dict[str, Any] = {}
        chunk_count = 0

        # Aggregations
        citations: list[dict] = []
        seen_cite_urls: set[str] = set()

        tool_calls_by_id: dict[str, dict] = {}
        tool_calls_list: list[dict] = []

        thoughts_grouped: list[str] = []
        _current_thought_parts: list[str] = []

        agentic_stage = -1
        def _flush_thought_group():
            nonlocal _current_thought_parts, thoughts_grouped
            if _current_thought_parts:
                thoughts_grouped.append("".join(_current_thought_parts))
                _current_thought_parts = []

        try:
            async for ev in self.stream_model_text(
                client,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                client_cfg=cfg,
                role=role,
                tools=tools,
                tool_choice=tool_choice,
            ):
                if on_event and "all_event" in ev:
                    await on_event(ev["all_event"].model_dump())

                # ---- Structured events ----
                etype = ev.get("event")
                if etype == "text.delta":
                    try:
                        # await on_delta(ev["delta"])
                        await on_delta(ev["text"])
                    except Exception as cb_err:
                        slog.log_error(cb_err, "on_delta_callback_failed")
                    final_chunks.append(ev["text"])
                    chunk_count += 1
                    # non-thinking event: close any open thought cluster
                    _flush_thought_group()
                    continue

                if etype == "thinking.delta":
                    txt = ev.get("text") or ""
                    _stage = ev.get("stage")
                    if _stage is not None and _stage != agentic_stage:
                        _flush_thought_group()
                        agentic_stage = _stage
                    if txt:
                        _current_thought_parts.append(txt)
                        if on_thinking:
                            try:
                                await on_thinking(ev)
                            except Exception as cb_err:
                                slog.log_error(cb_err, "on_thinking_callback_failed")
                    # thinking continues; do not flush yet
                    continue

                if etype == "tool.search":
                    # update by id (if present), else append as-is
                    # we want to group by id, but also keep arrival order
                    tid = ev.get("id") or f"search_{len(tool_calls_list)+1}"
                    _stage = ev.get("stage")
                    if _stage is not None and _stage != agentic_stage:
                        _flush_thought_group()
                        agentic_stage = _stage
                    call = tool_calls_by_id.get(tid) or {}
                    call["id"] = tid
                    if ev.get("query"): call["query"] = ev.get("query")
                    call["status"] = ev.get("status") or call.get("status")
                    if ev.get("sources"): call["sources"] = ev.get("sources")

                    # coalesce updates for same id
                    tool_calls_by_id[tid] = call
                    tool_calls_list.append(call)
                    # _flush_thought_group()  # tools break thought grouping

                    if on_tool_result_event:
                        try:
                            await on_tool_result_event({"type": "tool.search", **call})
                        except Exception as cb_err:
                            slog.log_error(cb_err, "on_event_callback_failed")
                    continue

                if etype == "citation":
                    url = ev.get("url")
                    if url and url not in seen_cite_urls:
                        seen_cite_urls.add(url)
                        citations.append({
                            "title": ev.get("title"),
                            "url": url,
                            "start": ev.get("start"),
                            "end": ev.get("end"),
                        })
                    # citations don't break thinking, but you can choose to:
                    # _flush_thought_group()
                    if on_event:
                        try:
                            await on_event({"type": "citation", **ev})
                        except Exception as cb_err:
                            slog.log_error(cb_err, "on_event_callback_failed")
                    continue

                if etype == "final":
                    usage_out = ev.get("usage") or usage_out
                    # will also receive the legacy {final: True} below; just keep the latest usage
                    _flush_thought_group()
                    continue

                # any other event: fan out (optional)
                if etype and on_event:
                    try:
                        await on_event(ev)
                    except Exception as cb_err:
                        slog.log_error(cb_err, "on_event_callback_failed")

            full_text = "".join(final_chunks)
            slog.log_step(
                "stream_finished",
                {
                    "chunks": chunk_count,
                    "final_text_len": len(full_text),
                    **({"final_text": full_text} if debug else {"final_text_preview": full_text[:600]}),
                    "usage": usage_out,
                    "provider": cfg.provider,
                    "model": cfg.model_name,
                    "role": role,
                    "thought_groups": len(thoughts_grouped),
                    "tool_events": len(tool_calls_list),
                    "citations": len(citations),
                },
            )

            ret = {
                "text": full_text,
                "usage": _norm_usage_dict(usage_out),
                "provider_message_id": None,
                "model_name": cfg.model_name,
                "thoughts": thoughts_grouped,          # <- list[str], grouped
                "tool_calls": tool_calls_list,         # <- list[dict] in arrival order
                "citations": citations,                # <- list[dict]
            }

            if on_complete:
                try:
                    await on_complete(ret)
                    slog.log_step("on_complete_called", {"status": "ok", "final_text_len": len(full_text)})
                except Exception as e:
                    slog.log_error(e, "on_complete_failed")

            slog.finish_operation(True, "stream_model_text_tracked_complete",
                                  provider=cfg.provider, model=cfg.model_name, role=role)
            return ret

        except Exception as e:
            slog.log_error(e, "stream_loop_failed")
            slog.finish_operation(False, "stream_model_text_tracked_failed",
                                  provider=cfg.provider, model=cfg.model_name, role=role)
            return {
                "text": f"Model call failed: {e}",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "provider_message_id": None,
                "model_name": cfg.model_name,
                "thoughts": [],
                "tool_calls": [],
                "citations": [],
            }
# =========================
# Custom endpoint client (unchanged API)
# =========================
class CustomModelClient:
    def __init__(self, endpoint: str, api_key: str, model_name: str, temperature: float = 0.7):
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.logger = AgentLogger("CustomModelClient")

        self.default_params = {
            "max_new_tokens": 1024, "temperature": temperature, "top_p": 0.9,
            "min_p": None, "skip_cot": True, "fabrication_awareness": False, "prompt_mode": "default"
        }

    def _convert_langchain_to_conversation(self, messages: List[BaseMessage]) -> List[Dict[str, str]]:
        convo = []
        for i, message in enumerate(messages):
            if isinstance(message, SystemMessage):
                convo.append({"role": "system", "content": message.content})
            elif isinstance(message, HumanMessage):
                convo.append({"role": "user", "content": message.content})
            elif isinstance(message, AIMessage):
                convo.append({"role": "assistant", "content": message.content})
            else:
                self.logger.log_step("unknown_message_type", {"index": i, "type": type(message).__name__}, level="WARNING")
                convo.append({"role": "user", "content": str(message.content)})
        return convo

    def _prepare_payload(self, messages: List[BaseMessage], **kwargs) -> Dict[str, Any]:
        parameters = {**self.default_params, **kwargs}
        return {"inputs": self._convert_langchain_to_conversation(messages), "parameters": parameters}

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        self.logger.start_operation("async_model_invocation", model_name=self.model_name, endpoint=self.endpoint,
                                    message_count=len(messages), parameters=kwargs)
        payload = self._prepare_payload(messages, **kwargs)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, headers=self._headers(), json=payload,
                                        timeout=aiohttp.ClientTimeout(total=300)) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}: {await response.text()}")
                    result = await response.json()
                    if "error" in result:
                        raise Exception(f"Model error: {result['error']}")
                    text = result.get("response", "") or result.get("text", "") or "No response generated"
                    usage = result.get("usage") or {}
                    try:
                        hdr = {k.lower(): v for k, v in dict(response.headers).items()}
                        pt = int(hdr.get("x-prompt-tokens", 0)); ct = int(hdr.get("x-completion-tokens", 0))
                        if pt or ct:
                            usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
                    except Exception:
                        pass
                    mid = result.get("id") or result.get("message_id")
                    self.logger.log_model_call(self.model_name, sum(len(str(m.content)) for m in messages), len(text), True)
                    self.logger.finish_operation(True, f"Generated {len(text)} characters")
                    return AIMessage(content=text, additional_kwargs={"usage": usage, "provider_message_id": mid, "model_name": self.model_name})
        except Exception as e:
            self.logger.log_error(e, "custom_invoke_failed")
            self.logger.finish_operation(False, "custom_invoke_failed")
            raise

    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict[str, Any]]:
        self.logger.start_operation("async_model_stream", model_name=self.model_name, endpoint=self.endpoint,
                                    message_count=len(messages), parameters=kwargs)
        payload = self._prepare_payload(messages, **{**kwargs, "stream": True})
        async with aiohttp.ClientSession() as session:
            async with session.post(self.endpoint, headers=self._headers(), json=payload,
                                    timeout=aiohttp.ClientTimeout(total=600)) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {await resp.text()}")
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" in ctype:
                    usage = {}
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if not line: continue
                        if line.startswith("data:"):
                            s = line[len("data:"):].strip()
                            if s == "[DONE]": break
                            try: evt = json.loads(s)
                            except Exception: continue
                            if "delta" in evt: yield {"delta": evt["delta"]}
                            elif "response" in evt: yield {"delta": evt["response"]}
                            if evt.get("final"):
                                usage = evt.get("usage") or {}
                                yield {"event": "final", "usage": _norm_usage_dict(usage), "model_name": self.model_name}
                                return
                    yield {"event": "final", "usage": {}, "model_name": self.model_name}
                    return

                text_buffer = []
                async for raw in resp.content:
                    chunk = raw.decode("utf-8", errors="ignore")
                    for line in chunk.splitlines():
                        s = line.strip()
                        if not s: continue
                        try: obj = json.loads(s)
                        except Exception: text_buffer.append(s); continue
                        if "delta" in obj: yield {"delta": obj["delta"]}
                        elif "response" in obj: yield {"delta": obj["response"]}
                        if obj.get("final"):
                            usage = obj.get("usage") or {}
                            yield {"event": "final", "usage": _norm_usage_dict(usage), "model_name": self.model_name}
                            return
                try:
                    full = await resp.json()
                    out = full.get("response", "") or full.get("text", "")
                    if out:
                        for i in range(0, len(out), 30): yield {"delta": out[i:i+30]}
                    usage = full.get("usage") or {}
                    yield {"event": "final", "usage": _norm_usage_dict(usage), "model_name": self.model_name}
                except Exception:
                    out = "".join(text_buffer)
                    if out:
                        for i in range(0, len(out), 30): yield {"delta": out[i:i+30]}
                    yield {"event": "final", "usage": {}, "model_name": self.model_name}

# =========================
# Embeddings
# =========================
class CustomEmbeddings(Embeddings):
    def __init__(self, endpoint: str, model: str = "sentence-transformers/all-MiniLM-L6-v2", size: int = 384):
        self.endpoint = endpoint; self.model = model; self.size = size
        self.logger = AgentLogger("CustomEmbeddings")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out = []
        for t in texts:
            try:
                r = requests.post(self.endpoint, json={"inputs": t, "model": self.model, "size": self.size}, timeout=30)
                r.raise_for_status(); e = r.json().get("embedding", [])
                out.append(e); self.logger.log_step("document_embedded", {"text_length": len(t), "embedding_dim": len(e)})
            except Exception as e:
                self.logger.log_error(e, "embed_documents"); out.append([0.0] * self.size)
        return out

    def embed_query(self, text: str) -> List[float] | None:
        try:
            r = requests.post(self.endpoint, json={"inputs": text, "model": self.model, "size": self.size}, timeout=30)
            r.raise_for_status(); e = r.json().get("embedding", [])
            self.logger.log_step("query_embedded", {"text_length": len(text), "embedding_dim": len(e)})
            return e
        except Exception as e:
            self.logger.log_error(e, "embed_query"); return None

# =========================
# Helpers for diagnostics
# =========================
def setup_logging():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler('agent_execution.log', mode='a')])

def export_execution_logs(execution_data: Dict[str, Any], filename: str | None = None):
    if not filename:
        filename = f"execution_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(filename, 'w') as f:
            json.dump(execution_data, f, indent=2, default=str)
        return f"Logs exported to {filename}"
    except Exception as e:
        return f"Failed to export logs: {str(e)}"

def probe_embeddings(config_request: ConfigRequest) -> Dict[str, Any]:
    cfg = create_workflow_config(config_request)
    ecfg = cfg.embedder_config
    if ecfg["provider"] == "openai":
        embeddings = OpenAIEmbeddings(model=ecfg["model_name"], openai_api_key=cfg.openai_api_key)
        test_text = "This is a test embedding query"
        embedding = embeddings.embed_query(test_text)
        return {"status": "success", "embedder_id": cfg.selected_embedder, "provider": "openai",
                "model": ecfg["model_name"], "embedding_size": len(embedding),
                "test_text": test_text, "embedding_preview": embedding[:5]}
    if ecfg["provider"] == "custom":
        if not config_request.custom_embedding_endpoint:
            raise Exception("Custom embedder requires an endpoint")
        embeddings = CustomEmbeddings(endpoint=config_request.custom_embedding_endpoint,
                                      model=ecfg["model_name"], size=ecfg["dim"])
        test_text = "This is a test embedding query"
        embedding = embeddings.embed_query(test_text)
        return {"status": "success" if embedding else "failed", "embedder_id": cfg.selected_embedder,
                "provider": "custom", "endpoint": config_request.custom_embedding_endpoint,
                "model": ecfg["model_name"], "embedding_size": len(embedding or []),
                "test_text": test_text, "embedding_preview": (embedding or [])[:5]}
    raise Exception(f"Unknown embedding provider: {ecfg['provider']}")

class BundleState(TypedDict, total=False):
    request_id: str
    tenant: str
    project: str
    user: str
    user_type: Optional[str]
    session_id: str
    conversation_id: str
    text: Optional[str]
    turn_id: str
    final_answer: Optional[str]
    followups: Optional[list[str]]
    error_message: Optional[str]
    step_logs: Optional[list[dict]]
    start_time: Optional[float]

APP_STATE_KEYS = [
    "request_id", "tenant", "project", "user", "session_id",
    "text", "final_answer", "followups", "error_message", "step_logs"
]

class FollowupsOutput(BaseModel):
    """Raw JSON payload emitted after <HERE GOES FOLLOWUP>."""
    followups: List[str] = Field(
        default_factory=list,
        description="0–5 concise, user-imperative next steps (<=120 chars each)."
    )

if __name__ == "__main__":

    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    async def streaming():

        # from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        # model_name = "gpt-4o-mini"
        # model_name = "claude-3-7-sonnet-20250219"
        # client = ChatOpenAI(model=model_name, stream_usage=True)
        msgs = [SystemMessage(content="You are concise."), HumanMessage(content="Say hi!")]
        # async for evt in base_service.stream_model_text(client, msgs):
        #     print(evt)
        m = "claude-3-7-sonnet-20250219"
        # m = "claude-sonnet-4-20250514"
        role = "segment_enrichment"
        req = ConfigRequest(
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            claude_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            selected_model=m,
            role_models={ role: {"provider": "anthropic", "model": m}},
        )
        ms = ModelServiceBase(create_workflow_config(req))
        client = ms.get_client(role)

        async def on_delta(d):
            print(d)
        await ms.stream_model_text_tracked(
            client,
            msgs,
            on_delta=on_delta,
            role=role,
            temperature=0.3,
            max_tokens=500,
            debug=True
        )
        print()

    asyncio.run(streaming())
