# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# infra/service_hub/openrouter.py
#
# OpenRouter completion API integration.
#
# This module provides non-streaming completion calls via OpenRouter,
# targeting single-turn, ad-hoc use cases: data processing, extraction,
# classification, tagging, schema generation, summarization, etc.
#
# NOT a replacement for the streaming pipeline (which is cache-optimized
# for Anthropic). OpenRouter cannot manage context caching and is therefore
# unsuitable as a copilot/agent backend.

import json
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

from kdcube_ai_app.infra.accounting import track_llm
from kdcube_ai_app.infra.accounting.usage import ServiceUsage, ClientConfigHint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_PROVIDER = "openrouter"

# ---------------------------------------------------------------------------
# Usage mapping: OpenRouter response -> internal ServiceUsage
# ---------------------------------------------------------------------------

def _map_openrouter_usage(raw_usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Convert OpenRouter's usage dict into our normalised token-count format.

    OpenRouter reports usage in OpenAI-compatible format:
      { "prompt_tokens": int, "completion_tokens": int, "total_tokens": int }

    Some models may also include "native_tokens_prompt" and
    "native_tokens_completion" for the underlying provider's counts.
    We record the canonical prompt/completion/total and pass native counts
    through as metadata.
    """
    if not raw_usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    prompt = int(raw_usage.get("prompt_tokens") or 0)
    completion = int(raw_usage.get("completion_tokens") or 0)
    total = int(raw_usage.get("total_tokens") or (prompt + completion))

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "input_tokens": prompt,
        "output_tokens": completion,
    }


# ---------------------------------------------------------------------------
# Extractors for @track_llm
# ---------------------------------------------------------------------------

def _or_provider_extractor(result: Any, *_args, **kwargs) -> str:
    """Always returns 'openrouter' as the provider."""
    return OPENROUTER_PROVIDER


def _or_model_extractor(*_args, **kwargs) -> str:
    """Extract model name from kwargs (passed explicitly by the caller)."""
    return kwargs.get("model") or "unknown"


def _or_usage_extractor(result: Any, *_args, **kwargs) -> ServiceUsage:
    """
    Extract usage from the completion result dict.

    result = {"text": ..., "usage": {...}, "model": ..., ...}
    """
    try:
        if isinstance(result, dict):
            mapped = _map_openrouter_usage(result.get("usage"))
            return ServiceUsage(
                input_tokens=mapped["input_tokens"],
                output_tokens=mapped["output_tokens"],
                total_tokens=mapped["total_tokens"],
                requests=1,
            )
    except Exception:
        pass
    return ServiceUsage(requests=1)


def _or_meta_extractor(*_args, **kwargs) -> Dict[str, Any]:
    """Capture call metadata for accounting events."""
    return {
        "provider": OPENROUTER_PROVIDER,
        "model": kwargs.get("model"),
        "temperature": kwargs.get("temperature"),
        "max_tokens": kwargs.get("max_tokens"),
        "openrouter_model_id": kwargs.get("model"),
    }


# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------

@track_llm(
    provider_extractor=_or_provider_extractor,
    model_extractor=_or_model_extractor,
    usage_extractor=_or_usage_extractor,
    metadata_extractor=_or_meta_extractor,
)
async def openrouter_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Single-turn completion call via OpenRouter (non-streaming).

    Parameters
    ----------
    model : str
        OpenRouter model identifier, e.g. "anthropic/claude-3.5-sonnet",
        "google/gemini-2.5-pro", "meta-llama/llama-3.1-405b-instruct", etc.
    messages : list[dict]
        OpenAI-compatible message list:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    temperature : float
        Sampling temperature.
    max_tokens : int
        Maximum tokens in the response.
    api_key : str | None
        OpenRouter API key.  Falls back to OPENROUTER_API_KEY env var.
    base_url : str | None
        Override for the OpenRouter base URL (testing / proxies).
    response_format : dict | None
        Optional response format hint, e.g. {"type": "json_object"}.
    extra_headers : dict | None
        Additional HTTP headers (e.g. HTTP-Referer, X-Title).
    extra_body : dict | None
        Additional body fields to merge into the request payload.

    Returns
    -------
    dict with keys:
        text : str           – The generated text.
        usage : dict         – Normalised usage dict (prompt_tokens, completion_tokens, total_tokens).
        model : str          – Model ID that actually served the request (may differ from requested).
        raw_response : dict  – Full response body for diagnostics.
        success : bool
        error : str | None
    """
    resolved_key = api_key or os.getenv("OPENROUTER_API_KEY") or ""
    if not resolved_key:
        return {
            "text": "",
            "usage": _map_openrouter_usage(None),
            "model": model,
            "raw_response": None,
            "success": False,
            "error": "OPENROUTER_API_KEY is not set",
        }

    resolved_url = (base_url or os.getenv("OPENROUTER_BASE_URL") or OPENROUTER_BASE_URL).rstrip("/")

    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_body:
        payload.update(extra_body)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{resolved_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                body = await resp.json()

                if resp.status != 200:
                    error_msg = body.get("error", {}).get("message", resp.reason) if isinstance(body, dict) else str(body)
                    logger.error("OpenRouter API error %s: %s", resp.status, error_msg)
                    return {
                        "text": "",
                        "usage": _map_openrouter_usage(None),
                        "model": model,
                        "raw_response": body,
                        "success": False,
                        "error": f"HTTP {resp.status}: {error_msg}",
                    }

                # Extract text from first choice
                choices = body.get("choices") or []
                text = ""
                if choices:
                    msg = choices[0].get("message") or {}
                    text = msg.get("content") or ""

                raw_usage = body.get("usage") or {}
                mapped_usage = _map_openrouter_usage(raw_usage)
                actual_model = body.get("model") or model

                return {
                    "text": text,
                    "usage": mapped_usage,
                    "model": actual_model,
                    "raw_response": body,
                    "success": True,
                    "error": None,
                }

    except Exception as exc:
        logger.exception("OpenRouter completion call failed")
        return {
            "text": "",
            "usage": _map_openrouter_usage(None),
            "model": model,
            "raw_response": None,
            "success": False,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Structured output helper (JSON-mode)
# ---------------------------------------------------------------------------

@track_llm(
    provider_extractor=_or_provider_extractor,
    model_extractor=_or_model_extractor,
    usage_extractor=_or_usage_extractor,
    metadata_extractor=_or_meta_extractor,
)
async def openrouter_completion_json(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convenience wrapper that requests JSON output from OpenRouter.

    Returns the same dict as ``openrouter_completion`` with an additional
    ``parsed`` key containing the parsed JSON (or None on parse failure).
    """
    result = await openrouter_completion.__wrapped__(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=base_url,
        response_format={"type": "json_object"},
        extra_headers=extra_headers,
        extra_body=extra_body,
    )

    parsed = None
    if result.get("success") and result.get("text"):
        try:
            parsed = json.loads(result["text"])
        except json.JSONDecodeError as e:
            logger.warning("OpenRouter JSON parse failed: %s", e)

    result["parsed"] = parsed
    return result
