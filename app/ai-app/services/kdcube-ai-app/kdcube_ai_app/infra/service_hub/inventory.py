# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/inventory.py
# minimal, clean, role-mapped, cached clients

import asyncio
import json
import os, sys
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
import kdcube_ai_app.infra.service_hub.errors as service_errors
import kdcube_ai_app.apps.chat.sdk.tools.citations as citation_utils

# =========================
# ids/util
# =========================
def _mid(role: str, msg_ts: str | None = None) -> str:
    if not msg_ts:
        msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
    return f"{role}-{msg_ts}-{uuid4().hex[:8]}"

def _msg_history(ms: List[BaseMessage]) -> dict:
    """
    Build a message history suitable for diagnostics/logging.

    Supports:
      - Plain text messages
      - Anthropic-style block messages in additional_kwargs['message_blocks']
      - Messages whose .content is already a list of blocks

    For block messages, we record:
      - 'content'          → concatenated text from text blocks (for quick preview/search)
      - 'content_blocks'   → the normalized blocks (preserves cache_control and non-text parts)
    """
    history = []
    try:
        for m in ms:
            role = (
                "system" if isinstance(m, SystemMessage) else
                "user" if isinstance(m, HumanMessage) else
                "assistant" if isinstance(m, AIMessage) else
                "unknown"
            )

            entry = {"role": role}
            addkw = getattr(m, "additional_kwargs", {}) or {}

            # 1) Anthropic-style message_blocks on the message
            blocks = addkw.get("message_blocks")

            # 2) Or LC message content already as list-of-blocks
            if not blocks and isinstance(getattr(m, "content", None), list):
                blocks = m.content  # treat as blocks

            if blocks:
                # If the message has a message-level cache_control, apply as a default
                default_cache_ctrl = addkw.get("cache_control")
                norm_blocks = _normalize_anthropic_blocks(blocks, default_cache_ctrl=default_cache_ctrl)

                # Concise preview: join only text blocks
                text_preview = "\n\n".join(
                    b.get("text", "")
                    for b in norm_blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                )

                entry["content"] = text_preview
                entry["content_blocks"] = norm_blocks
                # Optional: surface message-level cache_control if present
                if default_cache_ctrl:
                    entry["cache_control"] = default_cache_ctrl
            else:
                # Plain string content (default path)
                entry["content"] = getattr(m, "content", "") or ""

            history.append(entry)
    except Exception:
        # Keep logging resilient; return whatever we could collect
        pass

    return {"history": history}

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

# Add to inventory.py or a separate utils module

from langchain_core.messages import SystemMessage
from typing import List, Union

def _build_blocks_with_modality(content: Union[str, List[dict]], cache_last: bool = False) -> Union[str, dict]:
    """
    Helper: convert content to blocks with optional caching.
    Supports text, images, and documents.

    Args:
        content: str OR list of block dicts
        cache_last: cache entire message (if str) or last block (if list)

    Returns:
        Plain string OR {"message_blocks": [...]}

    Supported block types and MIME types:
        • text: {"type": "text", "text": "...", "cache": bool}
          No MIME type needed

        • image: {"type": "image", "data": base64_str, "media_type": "...", "cache": bool}
          Supported MIME types:
            - image/jpeg (JPEG)
            - image/png (PNG)
            - image/gif (GIF)
            - image/webp (WebP)
          Default: image/png
          Max size: 5MB per image (before base64 encoding)

        • document: {"type": "document", "data": base64_str, "media_type": "...", "cache": bool}
          Supported MIME types:
            - application/pdf (PDF only)
          Default: application/pdf
          Note: Anthropic renders PDFs as images internally (~10k chars/page limit)

    Examples:
        # Simple text with caching
        _build_blocks_with_modality("Hello", cache_last=True)

        # Mixed content with selective caching
        _build_blocks_with_modality([
            {"type": "text", "text": "Analyze:"},
            {"type": "document", "data": pdf_b64, "media_type": "application/pdf", "cache": True},
            {"type": "image", "data": img_b64, "media_type": "image/jpeg"},
        ])
    """
    if isinstance(content, str):
        if cache_last:
            return {"message_blocks": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]}
        return content  # Plain string

    # Multi-part content (text/image/document)
    blocks = []
    for i, block in enumerate(blocks_list := content):
        btype = block.get("type", "text")
        should_cache = block.get("cache", False) or (cache_last and i == len(blocks_list) - 1)

        # Build block
        if btype == "text":
            blk = {"type": "text", "text": block["text"]}
        elif btype in ("image", "document"):
            default_media = "image/png" if btype == "image" else "application/pdf"
            blk = {
                "type": btype,
                "source": {
                    "type": "base64",
                    "media_type": block.get("media_type", default_media),
                    "data": block["data"]
                }
            }
        else:
            blk = block  # Pass through

        # Apply cache
        if should_cache:
            blk["cache_control"] = {"type": "ephemeral"}

        blocks.append(blk)

    return {"message_blocks": blocks}

def create_cached_system_message(content: Union[str, List[dict]], cache_last: bool = False) -> SystemMessage:
    """
    SystemMessage with text/images/documents and optional caching.

    Args:
        content: str OR list of blocks:
            - {"type": "text", "text": "...", "cache": bool}
            - {"type": "image", "data": b64, "media_type": "image/png", "cache": bool}
            - {"type": "document", "data": b64, "media_type": "application/pdf", "cache": bool}
        cache_last: cache entire message (if str) or last block (if list)

    Examples:
        # Simple text
        create_cached_system_message("You are helpful.", cache_last=True)

        # Multi-part with KB context
        create_cached_system_message([
            {"type": "text", "text": "You are an analyst.", "cache": False},
            {"type": "document", "data": kb_pdf_b64, "media_type": "application/pdf", "cache": True},
            {"type": "text", "text": "Answer based on the document above.", "cache": False}
        ])
    """
    result = _build_blocks_with_modality(content, cache_last)
    if isinstance(result, str):
        return SystemMessage(content=result)
    return SystemMessage(content="", additional_kwargs=result)


def create_cached_human_message(content: Union[str, List[dict]], cache_last: bool = False) -> HumanMessage:
    """
    HumanMessage with text/images/documents and optional caching.

    Same API as create_cached_system_message.

    Examples:
        # Simple text
        create_cached_human_message("What's 2+2?")

        # Image with question
        create_cached_human_message([
            {"type": "image", "data": img_b64, "media_type": "image/jpeg", "cache": True},
            {"type": "text", "text": "What's in this image?"}
        ])

        # Multiple documents
        create_cached_human_message([
            {"type": "text", "text": "Compare these reports:"},
            {"type": "document", "data": report1_b64, "cache": False},
            {"type": "document", "data": report2_b64, "cache": True},
            {"type": "text", "text": "What changed?"}
        ])
    """
    result = _build_blocks_with_modality(content, cache_last)
    if isinstance(result, str):
        return HumanMessage(content=result)
    return HumanMessage(content="", additional_kwargs=result)

def create_modal_message(blocks: List[dict], cache_last: bool = False) -> HumanMessage:
    """
    HumanMessage with text/images/documents. Alias for create_cached_human_message with list input.

    Examples:
        create_modal_message([
            {"type": "text", "text": "Analyze:"},
            {"type": "document", "data": pdf_b64, "cache": True}
        ])
    """
    return create_cached_human_message(blocks, cache_last)


def create_document_message(text: str, document_data: str, media_type: str = "application/pdf",
                            cache_document: bool = False, cache_text: bool = False) -> HumanMessage:
    """Convenience: HumanMessage with single document + text."""
    return create_cached_human_message([
        {"type": "document", "data": document_data, "media_type": media_type, "cache": cache_document},
        {"type": "text", "text": text, "cache": cache_text}
    ])


def create_image_message(text: str, image_data: str, media_type: str = "image/png",
                         cache_image: bool = False, cache_text: bool = False) -> HumanMessage:
    """Convenience: HumanMessage with single image + text."""
    return create_cached_human_message([
        {"type": "image", "data": image_data, "media_type": media_type, "cache": cache_image},
        {"type": "text", "text": text, "cache": cache_text}
    ])


create_multimodal_message = create_modal_message

# --- helper: normalize anthropic blocks (text-only; extend for img/audio if needed) ---
def _normalize_anthropic_blocks(blocks: list, default_cache_ctrl: dict | None = None) -> list:
    """Normalize blocks to Anthropic format (text/image/document)."""
    norm = []

    for b in blocks:
        # Handle raw strings
        if not isinstance(b, dict):
            norm.append({"type": "text", "text": str(b)})
            continue

        btype = b.get("type", "text")

        # Build normalized block
        if btype == "text":
            text = b.get("text") or b.get("content", "")
            blk = {"type": "text", "text": str(text)}
        elif btype in ("image", "document"):
            blk = {"type": btype, "source": b.get("source", {})}
        else:
            norm.append(b)  # Unknown type - pass through
            continue

        # Apply cache control (block-level overrides default)
        if "cache_control" in b:
            blk["cache_control"] = b["cache_control"]
        elif default_cache_ctrl:
            blk["cache_control"] = default_cache_ctrl

        norm.append(blk)

    return norm


def _extract_system_prompt_text(system_prompt: Union[str, SystemMessage]) -> str:
    """
    Extract text from system prompt, handling both string and SystemMessage types.
    For SystemMessage with message_blocks (cached messages), concatenates all text parts.
    """
    if isinstance(system_prompt, str):
        return system_prompt

    if isinstance(system_prompt, SystemMessage):
        # Check for multi-part Anthropic blocks (cached messages)
        anthro_blocks = (system_prompt.additional_kwargs or {}).get("message_blocks")
        if anthro_blocks:
            # Concatenate all text blocks
            return "\n\n".join(
                block.get("text", "")
                for block in anthro_blocks
                if block.get("type") == "text"
            )

        # Simple SystemMessage - just return content
        return system_prompt.content or ""

    # Fallback for any other type
    return str(system_prompt)

from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage, AIMessage
from typing import List, Union

def _flatten_message(msg: BaseMessage) -> BaseMessage:
    """
    Convert Anthropic-style block messages to regular text messages.

    - If msg.additional_kwargs["message_blocks"] (or msg.content is a list of blocks)
      exists, we concatenate all text blocks into a single string.
    - We drop cache_control and other Anthropic-specific metadata.
    - Works for SystemMessage, HumanMessage, AIMessage.
    """
    addkw = getattr(msg, "additional_kwargs", {}) or {}
    blocks = addkw.get("message_blocks")

    # Also handle messages whose .content is already a list of blocks
    if not blocks and isinstance(getattr(msg, "content", None), list):
        blocks = msg.content

    if not blocks:
        # Nothing special → return as-is
        return msg

    text_parts: List[str] = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get("type") == "text":
                t = b.get("text") or b.get("content") or ""
                if t:
                    text_parts.append(str(t))
        else:
            # raw strings (or anything else) → stringify
            text_parts.append(str(b))

    full_text = "\n\n".join(text_parts)

    # Recreate the same type, but with plain text content, dropping additional_kwargs
    if isinstance(msg, SystemMessage):
        return SystemMessage(content=full_text)
    if isinstance(msg, HumanMessage):
        return HumanMessage(content=full_text)
    if isinstance(msg, AIMessage):
        return AIMessage(content=full_text)

    # Fallback: best-effort, keep type if constructor supports content=
    try:
        return type(msg)(content=full_text)
    except Exception:
        return msg


# =========================
# Logging
# =========================

class AgentLogger:
    def __init__(self, name: str, log_level: str = "INFO"):
        self.logger = logging.getLogger(f"agent.{name}")
        self.logger.setLevel(getattr(logging, log_level.upper()))

        # OPTIONAL: make stdout UTF-8 if this process owns the console
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())
            # If you add real handlers elsewhere, prefer FileHandler(..., encoding="utf-8")

        self.start_time = None
        self.execution_logs = []

    # ---------- ROBUST HELPERS ----------

    @staticmethod
    def _json_dumps_robust(obj: Any) -> str:
        """
        Try to serialize to JSON nicely; fall back to ASCII; last resort repr of the error.
        Also sanitize to a string that won't crash the output stream.
        """
        # 1) Best: pretty UTF-8, allow non-serializables via default=str
        try:
            s = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        except Exception as e1:
            # 2) Fallback: ASCII-only
            try:
                s = json.dumps(obj, indent=2, ensure_ascii=True, default=str)
            except Exception as e2:
                s = f"<json.dumps failed: {e1!r}; fallback failed: {e2!r}>"

        # 3) Make it safe for whatever stream encoding the handler uses.
        # If encoding fails (e.g., unpaired surrogates), backslash-escape the offenders.
        try:
            # No-op test encode to UTF-8
            s.encode("utf-8")
            return s
        except Exception:
            return s.encode("utf-8", "backslashreplace").decode("utf-8", "strict")

    def _emit(self, level: str, prefix: str, obj: Any = None, text: str | None = None):
        """
        Single guarded emit:
          - Builds a log-safe string (robust JSON if obj is given, else text).
          - If the handler's stream can't encode it, we backslash-escape.
          - Never throws.
        """
        try:
            payload = text if text is not None else self._json_dumps_robust(obj)
            msg = f"{prefix}{payload}"

            # Try to encode against the first handler's stream encoding (if any)
            enc = None
            try:
                if self.logger.handlers:
                    stream = getattr(self.logger.handlers[0], "stream", None)
                    enc = getattr(stream, "encoding", None)
            except Exception:
                enc = None

            if enc:
                try:
                    msg.encode(enc)
                except Exception:
                    msg = msg.encode(enc, "backslashreplace").decode(enc, "strict")
            else:
                # Unknown encoding → make sure it's UTF-8 safe
                try:
                    msg.encode("utf-8")
                except Exception:
                    msg = msg.encode("utf-8", "backslashreplace").decode("utf-8", "strict")

            getattr(self.logger, level.lower())(msg)

        except Exception as e:
            # Absolute last-resort: never crash due to logging
            try:
                getattr(self.logger, "error")(
                    f"[logging-fallback] {prefix}{repr(obj) if obj is not None else repr(text)} (exc={e!r})"
                )
            except Exception:
                # Swallow everything
                pass

    # ---------- PUBLIC API ----------

    def start_operation(self, operation: str, **kwargs):
        self.start_time = time.time()
        log_data = {"operation": operation, "timestamp": datetime.now().isoformat(), "inputs": kwargs}
        self._emit("debug", f"🚀 Starting {operation} - ", obj=log_data)
        return log_data

    def log_step(self, step: str, data: Any = None, level: str = "DEBUG"):
        entry = {"step": step, "timestamp": datetime.now().isoformat(), "data": data or "No data"}
        if self.start_time:
            entry["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self.execution_logs.append(entry)
        self._emit(level, "📋 " + step + " - ", obj=entry)

    def log_model_call(self, model_name: str, prompt_length: int, response_length: int | None = None, success: bool = True):
        data = {
            "model": model_name, "prompt_length": prompt_length, "response_length": response_length,
            "success": success, "timestamp": datetime.now().isoformat()
        }
        if self.start_time:
            data["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self._emit("info", ("✅" if success else "❌") + " Model Call - ", obj=data)

    def log_error(self, error: Exception, context: str | None = None):
        data = {
            "error_type": type(error).__name__, "error_message": str(error),
            "context": context, "timestamp": datetime.now().isoformat()
        }
        if self.start_time:
            data["elapsed_time"] = f"{time.time() - self.start_time:.2f}s"
        self._emit("error", "💥 Error - ", obj=data)

    def finish_operation(self, success: bool = True, result_summary: str | None = None, **kwargs):
        if not self.start_time:
            return
        total = time.time() - self.start_time
        summary = {
            "success": success, "total_time": f"{total:.2f}s", "result_summary": result_summary,
            "total_steps": len(self.execution_logs), "timestamp": datetime.now().isoformat(), **kwargs
        }
        self._emit("debug", ("🎉" if success else "💥") + " Operation Complete - ", obj=summary)
        self.start_time = None
        self.execution_logs = []
        return summary

    def log(self, message: str, level: str = "INFO"):
        # Guard free-form messages too
        self._emit(level, "", text=message)

# =========================
# Config (+ role mapping)
# =========================
class ConfigRequest(BaseModel):
    # Keys
    openai_api_key: Optional[str] = None
    claude_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

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

    # Gemini caching (optional, safe defaults)
    gemini_cache_enabled: Optional[bool] = None
    gemini_cache_ttl_seconds: Optional[int] = None

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
                 google_api_key: Optional[str] = None,
                 embedding_model: Optional[str] = None,
                 default_llm_model: Optional[str] = None,
                 role_models: Optional[Dict[str, Dict[str, str]]] = None):
        # keys
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.claude_api_key = claude_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.google_api_key = google_api_key or os.getenv("GEMINI_API_KEY", "")

        # Gemini cache options (env or defaults)
        self.gemini_cache_enabled: bool = bool(int(os.getenv("GEMINI_CACHE_ENABLED", "0")))
        self.gemini_cache_ttl_seconds: int = int(os.getenv("GEMINI_CACHE_TTL_SECONDS", "3600"))

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
    if config_request.google_api_key:                    # <<< NEW (google)
        cfg.google_api_key = config_request.google_api_key

    # Gemini cache
    if config_request.gemini_cache_enabled is not None:
        cfg.gemini_cache_enabled = bool(config_request.gemini_cache_enabled)
    if config_request.gemini_cache_ttl_seconds is not None:
        cfg.gemini_cache_ttl_seconds = int(config_request.gemini_cache_ttl_seconds)

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

    def _mk_gemini(self, model: str, temperature: float) -> "GeminiModelClient":
        if not self.config.google_api_key:
            raise ValueError("Gemini provider requires GEMINI_API_KEY or google_api_key in ConfigRequest")

        from kdcube_ai_app.infra.service_hub.gemini import GeminiModelClient
        return GeminiModelClient(
            api_key=self.config.google_api_key,
            model_name=model,
            temperature=temperature,
            cache_enabled=self.config.gemini_cache_enabled,
            cache_ttl_seconds=self.config.gemini_cache_ttl_seconds,
        )

    def get_client(self, role: str, temperature: float) -> Optional[Any]:
        # ensure mapping exists even for new roles
        spec = self.config.ensure_role(role)
        if not spec:
            return None

        provider, model = spec["provider"], spec["model"]
        key = (provider, model, role, round(temperature, 3))

        if key in self._cache:
            return self._cache[key]

        if provider == "openai":
            client = self._mk_openai(model, temperature)
        elif provider == "anthropic":
            client = self._mk_anthropic()
        elif provider in ("google", "gemini"):           # <--- NEW
            client = self._mk_gemini(model, temperature)
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
    cfg = kw.get("client_cfg")
    if not cfg and model_service and client:
        try:
            cfg = model_service.describe_client(client, role=kw.get("role"))
        except TypeError:
            cfg = model_service.describe_client(client)
        except Exception:
            cfg = None
    return getattr(cfg, "provider", "unknown") if cfg else "unknown"


def ms_model_extractor(model_service, client, *args, **kw) -> str:
    cfg = kw.get("client_cfg")
    if not cfg and model_service and client:
        try:
            cfg = model_service.describe_client(client, role=kw.get("role"))
        except TypeError:
            cfg = model_service.describe_client(client)
        except Exception:
            cfg = None
    return getattr(cfg, "model_name", "unknown") if cfg else "unknown"


def ms_structured_meta_extractor(model_service, _client, system_prompt: str, user_message: str, response_format, **kw):
    client_cfg = kw.get("client_cfg")
    return {
        "model": (client_cfg.model_name if client_cfg else None),
        "provider": (client_cfg.provider if client_cfg else None),
        "expected_format": getattr(response_format, "__name__", str(response_format)),
        "prompt_chars": len(system_prompt or "") + len(user_message or ""),
        "temperature": kw.get("temperature"),
        "max_tokens": kw.get("max_tokens"),
        "role": kw.get("role"),
    }


def ms_freeform_meta_extractor(model_service, _client=None, messages=None, *a, **kw):
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
        "role": kw.get("role"),
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
            # self.logger.log_step("claude_client_initialized", {"model": config.format_fixer_model})
        except ImportError:
            self.claude_client = None
            self.logger.log_error(ImportError("anthropic package not available"), "Claude client initialization")

    async def fix_format(
        self,
        raw_output: str,
        expected_format: str,
        input_data: str,
        system_prompt: Union[str, SystemMessage]
    ) -> Dict[str, Any]:
        """
        Fix malformed JSON output to match expected format.

        Args:
            raw_output: The malformed JSON string to fix
            expected_format: Description of expected format (e.g., schema name)
            input_data: Original user input/message
            system_prompt: Original system prompt (string or SystemMessage)
        """
        # Extract text from system prompt (handles both types)
        system_prompt_text = _extract_system_prompt_text(system_prompt)

        self.logger.start_operation(
            "format_fixing",
            raw_output_length=len(raw_output),
            expected_format=expected_format,
            input_data_length=0,
            system_prompt_length=len(system_prompt_text),
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

Original system prompt: {system_prompt_text}
Original input: {input_data}
Expected format: {expected_format}
Malformed output: {raw_output}

Please fix the JSON to match the expected format. Return only the fixed JSON, no additional text."""

            self.logger.log_step("sending_fix_request", {
                "model": self.config.format_fixer_model,
                "fix_prompt_length": len(fix_prompt),
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
        self._format_fixer = None
        self._anthropic_async = None

        self._emb_model = embedding_model()

    # ---------- back-compat clients (lazily resolved) ----------
    @property
    def format_fixer(self):
        if self._format_fixer is None:
            self._format_fixer = FormatFixerService(self.config)
        return self._format_fixer

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

        from kdcube_ai_app.infra.service_hub.gemini import GeminiModelClient
        # Fallback best-effort (used by accounting hooks)
        if isinstance(client, CustomModelClient):
            return ClientConfigHint(provider="custom", model_name=client.model_name)
        if isinstance(client, ChatOpenAI):
            return ClientConfigHint(provider="openai", model_name=getattr(client, "model", "unknown"))
        if isinstance(client, GeminiModelClient):
            return ClientConfigHint(provider="google", model_name=getattr(client, "model", "unknown"))
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
    def _freeform_usage_extractor(result: Any = None, *_a, **_kw) -> ServiceUsage:
        """
        Accounting-safe usage extractor for freeform returns:
        expects result dict like:
          {"text": ..., "usage": {...}, ...}
        but never assumes it exists.
        """
        try:
            if isinstance(result, dict):
                u = _norm_usage_dict(result.get("usage") or {})
                return ServiceUsage(
                    input_tokens=u.get("prompt_tokens", 0) or 0,
                    output_tokens=u.get("completion_tokens", 0) or 0,
                    thinking_tokens=u.get("thinking_tokens", 0) or 0,
                    cache_creation_tokens=u.get("cache_creation_input_tokens", 0) or 0,
                    cache_read_tokens=u.get("cache_read_input_tokens", 0) or 0,
                    cache_creation=u.get("cache_creation") or {},
                    total_tokens=u.get("total_tokens", 0) or (
                            (u.get("prompt_tokens", 0) or 0) + (u.get("completion_tokens", 0) or 0)
                    ),
                    requests=1
                )
        except Exception:
            pass

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
            max_thinking_tokens: int = 128,
            client_cfg: ClientConfigHint | None = None,
            role: Optional[str] = None,
            tools: Optional[list] = None,
            tool_choice: Optional[Union[str, dict]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))
        provider_name, model_name = cfg.provider, cfg.model_name

        index = -1
        # Anthropic streaming
        if provider_name == "anthropic" and hasattr(client, "messages"):
            import anthropic
            async_client = getattr(self.router, "_mk_anthropic_async")()

            # --- Enhanced conversion with cache support ---
            sys_blocks = []  # system as list of content blocks
            convo = []

            for m in messages:
                if isinstance(m, SystemMessage):
                    # Check for Anthropic-specific block structure
                    message_blocks = (m.additional_kwargs or {}).get("message_blocks")

                    if message_blocks:
                        # Multi-part system message with selective caching
                        sys_blocks.extend(message_blocks)
                    else:
                        # Single system message - check for cache_control
                        cache_ctrl = (m.additional_kwargs or {}).get("cache_control")
                        block = {"type": "text", "text": m.content}
                        if cache_ctrl:
                            block["cache_control"] = cache_ctrl
                        sys_blocks.append(block)

                elif isinstance(m, HumanMessage):
                    message_blocks = (m.additional_kwargs or {}).get("message_blocks")
                    cache_ctrl = (m.additional_kwargs or {}).get("cache_control")

                    if message_blocks:
                        # Use provided blocks (pass-through), but normalize into Anthropic content format
                        # If you want to apply a default cache_control to text blocks lacking one, set default_cache_ctrl=cache_ctrl
                        content_blocks = []
                        for b in message_blocks:
                            if isinstance(b, dict):
                                # keep non-text blocks as-is; ensure text blocks have proper shape
                                if b.get("type") == "text":
                                    blk = {"type": "text", "text": b.get("text", "")}
                                    if "cache_control" in b:
                                        blk["cache_control"] = b["cache_control"]
                                    elif cache_ctrl:
                                        # optional: apply message-level cache_control when block doesn't specify one
                                        blk["cache_control"] = cache_ctrl
                                    content_blocks.append(blk)
                                else:
                                    content_blocks.append(b)
                            else:
                                # raw string -> text block
                                blk = {"type": "text", "text": str(b)}
                                if cache_ctrl:
                                    blk["cache_control"] = cache_ctrl
                                content_blocks.append(blk)

                        convo.append({"role": "user", "content": content_blocks})

                    else:
                        if cache_ctrl:
                            convo.append({
                                "role": "user",
                                "content": [{"type": "text", "text": m.content, "cache_control": cache_ctrl}]
                            })
                        else:
                            convo.append({
                                "role": "user",
                                "content": [{"type": "text", "text": m.content}]
                            })

                elif isinstance(m, AIMessage):
                    convo.append({"role": "assistant", "content": m.content})
                else:
                    convo.append({"role": "user", "content": str(getattr(m, "content", ""))})

            # Build final system parameter
            # If we have blocks, use them; otherwise None
            final_system = sys_blocks if sys_blocks else None

            async with async_client.messages.stream(
                    model=model_name,
                    system=final_system,
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
                        cache_creation = getattr(u, "cache_creation", None)
                        if cache_creation:
                            cache_creation = cache_creation.model_dump()
                        usage = {
                            "input_tokens":  getattr(u, "input_tokens", 0) or 0,
                            "output_tokens": getattr(u, "output_tokens", 0) or 0,
                            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                            **{"cache_creation": cache_creation if cache_creation else {}}
                        }
                        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

                yield {"event": "final", "usage": _norm_usage_dict(usage), "model_name": model_name, "index": index}
            return

        # OpenAI streaming
        if isinstance(client, ChatOpenAI):

            # ✅ Convert block-based messages to regular messages
            normalized_messages = [_flatten_message(message) for message in messages]

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

            async for chunk in client.astream(normalized_messages, **stream_kwargs):
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
                        "cache_read_input_tokens": (um.get("input_token_details") or {}).get("cache_read"),
                        "input_tokens_details": (um.get("input_token_details") or {}).get("cache_read"), # {'cache_read': 233344}
                        "output_tokens_details": (um.get("output_token_details") or {}).get("cache_read_input_tokens"), # {'reasoning': 1344}
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

        from kdcube_ai_app.infra.service_hub.gemini import GeminiModelClient
        # Gemini streaming
        if isinstance(client, GeminiModelClient):
            async for ev in client.astream(
                    messages=messages,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_budget=max_thinking_tokens,
                    include_thoughts=True,   # <-- ask Gemini for thought summaries
            ):
                etype = ev.get("event")

                if etype in ("text.delta", "thinking.delta"):
                    index += 1
                    # pass-through, but add index and default stage/group_index
                    out = {
                        **ev,
                        "index": index,
                    }
                    # keep shape similar to OpenAI branch
                    out.setdefault("stage", 0)
                    if etype == "thinking.delta":
                        out.setdefault("group_index", 0)
                    yield out

                elif etype == "final":
                    index += 1
                    yield {
                        "event": "final",
                        "usage": ev.get("usage") or {},
                        "model_name": ev.get("model_name") or model_name,
                        "index": index,
                    }
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
            max_thinking_tokens: int = 128, # after Gemini 2.5 Pro min boundary
            client_cfg: ClientConfigHint | None = None,
            role: Optional[str] = None,
            on_complete: Optional[Callable[[dict], Awaitable[None]]] = None,
            debug: bool = True,
            tools: Optional[list] = None,
            tool_choice: Optional[Union[str, dict]] = None,
            debug_citations: bool = False,
    ) -> Dict[str, Any]:
        # dedicated streaming logger (new instance as requested)
        slog = AgentLogger("StreamTracker", self.config.log_level)
        cfg = client_cfg or (self.router.describe(role) if role else self.describe_client(client))

        # helpful context + previews
        def _msg_preview(ms: List[BaseMessage]) -> dict:
            try:
                def _preview_text(m: BaseMessage) -> str:
                    addkw = getattr(m, "additional_kwargs", {}) or {}
                    blocks = addkw.get("message_blocks")
                    if not blocks and isinstance(getattr(m, "content", None), list):
                        blocks = m.content
                    if blocks:
                        norm_blocks = _normalize_anthropic_blocks(blocks)
                        joined = "\n\n".join(
                            b.get("text", "")
                            for b in norm_blocks
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                        return joined
                    return str(getattr(m, "content", "") or "")

                sys = _preview_text(next((m for m in ms if isinstance(m, SystemMessage)), SystemMessage("")))[:200]
                usr = _preview_text(next((m for m in ms if isinstance(m, HumanMessage)), HumanMessage("")))[:200]
            except Exception:
                sys, usr = "", ""
            return {"system_preview": sys, "user_preview": usr}

        msg_data = _msg_history(messages) if debug else _msg_preview(messages)

        slog.start_operation(
            "stream_model_text_tracked",
            provider=cfg.provider,
            model=cfg.model_name,
            role=role,
            temperature=temperature,
            max_tokens=max_tokens,
            msg_count=len(messages),
            **msg_data
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
                    max_thinking_tokens=max_thinking_tokens,
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
            suspicious_tokens = None
            if debug_citations:
                suspicious_tokens = citation_utils.debug_only_suspicious_tokens(full_text)
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
                    "citation_debug": suspicious_tokens
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
                "service_error": None,
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
            svc_error = service_errors.mk_llm_error(
                exc=e,
                stage="stream_loop",
                cfg=cfg,
                service_name="StreamTracker",
                context={"role": role},
            )
            return {
                "text": f"Model call failed: {e}",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "provider_message_id": None,
                "model_name": cfg.model_name,
                "thoughts": [],
                "tool_calls": [],
                "citations": [],
                "service_error": svc_error.model_dump()
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
            json.dump(execution_data, f, indent=2, default=str, ensure_ascii=False)
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
