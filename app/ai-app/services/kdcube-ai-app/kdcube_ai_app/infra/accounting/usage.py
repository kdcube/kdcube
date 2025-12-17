# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/usage.py
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import logging, json, os

logger = logging.getLogger(__name__)

# -----------------------------
# Price calculation helpers
# -----------------------------
def price_table():
    """Enhanced price table with separate cache type pricing."""
    sonnet_45 = "claude-sonnet-4-5-20250929"
    haiku_4 = "claude-haiku-4-5-20251001"

    return {
        "llm": [
            {
                "model": sonnet_45,
                "provider": "anthropic",
                "input_tokens_1M": 3.00,
                "output_tokens_1M": 15.00,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 3.00,
                        "read_tokens_1M": 0.30,
                    },
                    "1h": {
                        "write_tokens_1M": 3.75,
                        "read_tokens_1M": 0.30,
                    },
                },
                "cache_write_tokens_1M": 3.00,
                "cache_read_tokens_1M": 0.30,
            },
            {
                "model": haiku_4,
                "provider": "anthropic",
                "input_tokens_1M": 1,
                "output_tokens_1M": 5,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 1,
                        "read_tokens_1M": 0.1,
                    },
                    "1h": {
                        "write_tokens_1M": 2,
                        "read_tokens_1M": 0.1,
                    },
                },
                "cache_write_tokens_1M": 2,
                "cache_read_tokens_1M": 0.1,
            },
            {
                "model": "claude-3-5-haiku-20241022",
                "provider": "anthropic",
                "input_tokens_1M": 0.80,
                "output_tokens_1M": 4.00,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.80,
                        "read_tokens_1M": 0.08,
                    },
                    "1h": {
                        "write_tokens_1M": 1.00,
                        "read_tokens_1M": 0.08,
                    },
                },
                "cache_write_tokens_1M": 0.80,
                "cache_read_tokens_1M": 0.08,
            },
            {
                "model": "claude-3-haiku-20240307",
                "provider": "anthropic",
                "input_tokens_1M": 0.25,
                "output_tokens_1M": 1.25,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.25,
                        "read_tokens_1M": 0.03,
                    },
                    "1h": {
                        "write_tokens_1M": 0.30,
                        "read_tokens_1M": 0.03,
                    },
                },
                "cache_write_tokens_1M": 0.25,
                "cache_read_tokens_1M": 0.03,
            },
            {
                "model": "gpt-4o",
                "provider": "openai",
                "input_tokens_1M": 2.50,
                "output_tokens_1M": 10.00,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 1.25,
            },
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "input_tokens_1M": 0.15,
                "output_tokens_1M": 0.60,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.075,
            },
            {
                "model": "o1",
                "provider": "openai",
                "input_tokens_1M": 15.00,
                "output_tokens_1M": 60.00,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 7.50,
            },
            {
                "model": "o3-mini",
                "provider": "openai",
                "input_tokens_1M": 1.10,
                "output_tokens_1M": 4.40,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.55,
            },
            {
                "model": "gemini-2.5-pro",
                "provider": "google",
                # prompts <= 200k tokens
                "input_tokens_1M": 1.25,          # normal input price
                "output_tokens_1M": 10.00,        # includes thinking tokens when enabled
                "thinking_output_tokens_1M": 10.00,  # same bucket; explicit for clarity
                "cache_write_tokens_1M": 0.0,
                "cache_read_tokens_1M": 0.0,
            },
            {
                "model": "gemini-2.5-pro-long",
                "provider": "google",
                # prompts > 200k tokens
                "input_tokens_1M": 2.50,
                "output_tokens_1M": 15.00,        # includes thinking tokens when enabled
                "thinking_output_tokens_1M": 15.00,
                "cache_write_tokens_1M": 0.0,
                "cache_read_tokens_1M": 0.0,
            },
            {
                "model": "gemini-2.5-flash",
                "provider": "google",
                "input_tokens_1M": 0.15,
                # non-thinking output price
                "output_tokens_1M": 0.60,
                # effective output price when thinking is enabled
                "thinking_output_tokens_1M": 3.50,
                "cache_write_tokens_1M": 0.0,
                "cache_read_tokens_1M": 0.0,
            },
            {
                "model": "gemini-2.5-flash-lite",
                "provider": "google",
                "input_tokens_1M": 0.10,
                "output_tokens_1M": 0.40,
                # Google docs and community posts do not list a separate higher rate
                # for thinking mode on Flash Lite as of late 2025, so mirror output.
                "thinking_output_tokens_1M": 0.40,
                "cache_write_tokens_1M": 0.0,
                "cache_read_tokens_1M": 0.0,
            },
        ],
        "embedding": [
            {
                "model": "text-embedding-3-small",
                "provider": "openai",
                "tokens_1M": 0.02,
            },
            {
                "model": "text-embedding-3-large",
                "provider": "openai",
                "tokens_1M": 0.13,
            },
        ],
        "web_search": [
            # Brave Search tiers
            {
                "provider": "brave",
                "tier": "free",
                "cost_per_1k_requests": 0.00,
                "limits": {
                    "requests_per_second": 1,
                    "requests_per_month": 2000
                }
            },
            {
                "provider": "brave",
                "tier": "base",
                "cost_per_1k_requests": 3.00,
                "limits": {
                    "requests_per_second": 20,
                    "requests_per_month": 20000000
                }
            },
            {
                "provider": "brave",
                "tier": "pro",
                "cost_per_1k_requests": 5.00,
                "limits": {
                    "requests_per_second": 50,
                    "requests_per_month": None  # unlimited
                }
            },
            # DuckDuckGo (free)
            {
                "provider": "duckduckgo",
                "tier": "free",
                "cost_per_1k_requests": 0.00,
                "limits": {
                    "requests_per_second": None,  # no hard limit
                    "requests_per_month": None   # no hard limit
                }
            }
        ]
    }

def load_accounting_services_config() -> Dict[str, Any]:
    """Load ACCOUNTING_SERVICES from environment variable."""
    config_str = os.environ.get("ACCOUNTING_SERVICES", "{}")
    try:
        return json.loads(config_str)
    except json.JSONDecodeError:
        return {}

def get_web_search_tier(provider: str, config: Optional[Dict[str, Any]] = None) -> str:
    """Get the tier for a web_search provider."""
    if config is None:
        config = load_accounting_services_config()

    web_search_config = config.get("web_search", {})
    provider_config = web_search_config.get(provider, {})

    # Default tiers
    defaults = {
        "brave": "base",
        "duckduckgo": "free"
    }

    return provider_config.get("tier", defaults.get(provider, "free"))

@dataclass(frozen=True)
class ClientConfigHint:
    provider: str        # "openai" | "anthropic" | "custom" | etc.
    model_name: str      # e.g., "gpt-4o", "claude-3-5-haiku-20241022", "your-custom-model"

@dataclass
class ServiceUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
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
            "thinking_tokens": self.thinking_tokens,
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
            thinking_tokens=d.get("thinking_tokens", 0),
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

    thinking = u.get("thinking_tokens") or 0
    visible_out = u.get("visible_output_tokens") or 0

    total  = u.get("total_tokens") or (int(prompt) + int(compl))
    try:
        prompt, compl, total = int(prompt), int(compl), int(total)
        thinking = int(thinking)
        visible_out = int(visible_out)
    except Exception:
        prompt, compl, total = int(prompt or 0), int(compl or 0), int(total or (prompt + compl))
        thinking = int(thinking or 0)
        visible_out = int(visible_out or 0)
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": compl,
        "total_tokens": total,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "input_tokens": int(u.get("input_tokens") or prompt),
        "output_tokens": int(u.get("output_tokens") or compl),
        "thinking_tokens": thinking,
        **{"cache_creation": cache_creation if cache_creation else {}}
    }
    if visible_out:
        out["visible_output_tokens"] = visible_out
    return out

def _approx_tokens_by_chars(text: str) -> Dict[str, int]:
    toks = max(1, len(text or "") // 4)
    return {"prompt_tokens": toks, "completion_tokens": 0, "total_tokens": toks}

def _structured_usage_extractor(result, *_a, **_kw) -> ServiceUsage:
    """track_llm usage_extractor for dicts returned by call_model_with_structure."""
    try:
        usage = None
        if isinstance(result, dict):
            usage = result.get("usage")
        else:
            usage = getattr(result, "usage", None)

        u = _norm_usage_dict(usage or {})
        return ServiceUsage(
            input_tokens=u.get("prompt_tokens", 0),
            output_tokens=u.get("completion_tokens", 0),
            thinking_tokens=u.get("thinking_tokens", 0),
            total_tokens=u.get("total_tokens", (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))),
            requests=1,
        )
    except Exception:
        return ServiceUsage(requests=1)

# ----------------------------- Web Search Extractors (for backend.search_many()) -----------------------------

def ws_provider_extractor(result, *args, **kwargs) -> str:
    """
    Extract provider from backend.search_many() call.
    Args:
        result: List[List[Dict]] - search results
        args[0]: self (backend instance)
    """
    if args:
        backend = args[0]
        return getattr(backend, "provider", None) or getattr(backend, "name", "unknown")
    return "unknown"


def ws_model_extractor(*args, **kwargs) -> str:
    """
    Extract model/service name from backend.search_many().
    Same as provider for search backends.
    """
    if args:
        backend = args[0]
        return getattr(backend, "provider", None) or getattr(backend, "name", "unknown")
    return "unknown"


def ws_usage_extractor(result, *args, **kwargs) -> ServiceUsage:
    """
    Extract usage from backend.search_many() with success tracking.

    Backend tracks successful queries (non-429, non-error) in self._last_successful_queries.
    Only those queries count toward usage/billing.

    Args:
        result: List[List[Dict]] - search results per query
        args[0]: self (backend instance)
    """
    backend = args[0] if args else None

    # Backend tracks which queries actually succeeded (not rate-limited)
    successful_queries = getattr(backend, '_last_successful_queries', [])

    # Count total results returned across all queries
    total_results = 0
    if isinstance(result, list):
        for query_results in result:
            if isinstance(query_results, list):
                total_results += len(query_results)

    return ServiceUsage(
        search_queries=len(successful_queries),  # Only successful queries count!
        search_results=total_results,
        requests=1
    )


def ws_meta_extractor(*args, **kwargs) -> Dict[str, Any]:
    """
    Extract metadata from backend.search_many() call.

    Shows attempted vs successful for transparency in accounting.
    """
    backend = args[0] if args else None
    queries = args[1] if len(args) > 1 else kwargs.get("queries", [])
    per_query_max = kwargs.get("per_query_max")
    freshness = kwargs.get("freshness")
    country = kwargs.get("country")
    safesearch = kwargs.get("safesearch", "moderate")

    # Get successful queries from backend's tracking
    successful_queries = getattr(backend, '_last_successful_queries', [])
    queries_list = list(queries) if queries else []

    return {
        "queries_attempted": len(queries_list),
        "queries_successful": len(successful_queries),
        "query_variants": successful_queries,  # Only successful ones
        "per_query_max": int(per_query_max) if per_query_max else 0,
        "freshness": freshness,
        "country": country,
        "safesearch": safesearch,
    }

_USAGE_KEYS = [
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "cache_creation",
    "total_tokens",
    "embedding_tokens",
    "embedding_dimensions",
    "search_queries",
    "search_results",
    "image_count",
    "image_pixels",
    "audio_seconds",
    "requests",
    "cost_usd",
]

# -----------------------------
# Usage accumulation helpers
# -----------------------------
_BUCKETS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}