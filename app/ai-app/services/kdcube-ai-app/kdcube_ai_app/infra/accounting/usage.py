# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/usage.py
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass(frozen=True)
class ClientConfigHint:
    provider: str        # "openai" | "anthropic" | "custom" | etc.
    model_name: str      # e.g., "gpt-4o", "claude-3-5-haiku-20241022", "your-custom-model"

@dataclass
class ServiceUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation: Optional[Dict[str, Any]] = None
    total_tokens: int = 0
    embedding_tokens: int = 0
    embedding_dimensions: int = 0
    search_queries: int = 0
    search_results: int = 0
    image_count: int = 0
    image_pixels: int = 0
    audio_seconds: float = 0.0
    requests: int = 0
    cost_usd: Optional[float] = None

    def to_compact_dict(self) -> Dict[str, Any]:
        """Only include fields that are meaningful (non-zero and non-null)."""
        data = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation": self.cache_creation,
            "total_tokens": self.total_tokens,
            "embedding_tokens": self.embedding_tokens,
            "embedding_dimensions": self.embedding_dimensions,
            "search_queries": self.search_queries,
            "search_results": self.search_results,
            "image_count": self.image_count,
            "image_pixels": self.image_pixels,
            "audio_seconds": self.audio_seconds,
            "requests": self.requests,
            "cost_usd": self.cost_usd,
        }
        def keep(k, v):
            if v is None: return False
            if isinstance(v, (int, float)):
                return v != 0
            return True  # strings/bools if ever added
        return {k: v for k, v in data.items() if keep(k, v)}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ServiceUsage":
        """Safe deserialization: missing keys fall back to defaults."""
        d = d or {}
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            cache_creation_tokens=d.get("cache_creation_tokens", 0),
            cache_read_tokens=d.get("cache_read_tokens", 0),
            cache_creation=d.get("cache_creation"),
            total_tokens=d.get("total_tokens", 0),
            embedding_tokens=d.get("embedding_tokens", 0),
            embedding_dimensions=d.get("embedding_dimensions", 0),
            search_queries=d.get("search_queries", 0),
            search_results=d.get("search_results", 0),
            image_count=d.get("image_count", 0),
            image_pixels=d.get("image_pixels", 0),
            audio_seconds=d.get("audio_seconds", 0.0),
            requests=d.get("requests", 0),
            cost_usd=d.get("cost_usd"),  # remains None if absent
        )

def _norm_usage_dict(u: Dict[str, Any]) -> Dict[str, int]:
    """Normalize OpenAI/Anthropic/custom usage into prompt/completion/total."""
    u = u or {}
    prompt = u.get("prompt_tokens") or u.get("input_tokens") or 0
    compl  = u.get("completion_tokens") or u.get("output_tokens") or 0
    cache_creation_input_tokens = u.get("cache_creation_input_tokens") or 0
    cache_read_input_tokens = u.get("cache_read_input_tokens") or 0
    cache_creation = u.get("cache_creation")

    total  = u.get("total_tokens") or (int(prompt) + int(compl))
    try:
        prompt, compl, total = int(prompt), int(compl), int(total)
    except Exception:
        prompt, compl, total = int(prompt or 0), int(compl or 0), int(total or (prompt + compl))
    return {"prompt_tokens": prompt,
            "completion_tokens": compl,
            "total_tokens": total,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            **{"cache_creation": cache_creation if cache_creation else {}}}

def _approx_tokens_by_chars(text: str) -> Dict[str, int]:
    toks = max(1, len(text or "") // 4)
    return {"prompt_tokens": toks, "completion_tokens": 0, "total_tokens": toks}

def _structured_usage_extractor(result, *_a, **_kw) -> ServiceUsage:
    """track_llm usage_extractor for dicts returned by call_model_with_structure."""
    try:
        u = _norm_usage_dict(result.get("usage") or {})
        return ServiceUsage(
            input_tokens=u["prompt_tokens"],
            output_tokens=u["completion_tokens"],
            total_tokens=u["total_tokens"],
            requests=1,
        )
    except Exception:
        return ServiceUsage(requests=1)
