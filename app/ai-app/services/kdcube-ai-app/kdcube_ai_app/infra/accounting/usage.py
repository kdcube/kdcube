# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/usage.py
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import logging, json

logger = logging.getLogger(__name__)

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
        logger.info(f"Usage {result.get('usage')}")
        u = _norm_usage_dict(result.get("usage") or {})
        return ServiceUsage(
            input_tokens=u["prompt_tokens"],
            output_tokens=u["completion_tokens"],
            thinking_tokens=u.get("thinking_tokens", 0),
            total_tokens=u["total_tokens"],
            requests=1,
        )
    except Exception:
        return ServiceUsage(requests=1)


def _parse_queries_arg(queries: Any) -> List[str]:
    # mirrors your normalization logic, but lightweight
    if isinstance(queries, (list, tuple)):
        return [str(q).strip() for q in queries if str(q).strip()]

    s = str(queries or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            arr = json.loads(s)
            return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            return [s]
    return [s]

def ws_usage_extractor(
        result: Any = None,
        _self: Any = None,
        queries: Any = None,
        objective: Optional[str] = None,
        refinement: str = "balanced",
        n: int = 8,
        *args,
        **_kw
) -> "ServiceUsage":
    """
    Signature-agnostic usage extractor.

    Works with both decoration styles:
      - method: ws_usage_extractor(result, self, queries, ...)
      - function: ws_usage_extractor(result, _SERVICE, queries, ...)

    Also tolerates:
      - result passed as kw
      - queries passed as kw
      - missing/shifted positional args
    """
    if result is None and "result" in _kw:
        result = _kw.get("result")

    # If queries not provided explicitly, try to recover from positional args.
    # For both method and function backends, the wrapped callable signature is effectively:
    #   (self_or_service, queries, objective, refinement, n, ...)
    # So in extractor calls that receive original args, queries is usually at index 1.
    if queries is None:
        if len(args) >= 2:
            queries = args[1]
        else:
            queries = _kw.get("queries")

    q_list = _parse_queries_arg(queries)

    # count results
    search_results = 0
    try:
        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, list):
            search_results = len(data)
    except Exception:
        search_results = 0

    return ServiceUsage(
        search_queries=len(q_list),
        search_results=search_results,
        requests=1
    )


def ws_meta_extractor(
        _self: Any = None,
        queries: Any = None,
        objective: Optional[str] = None,
        refinement: str = "balanced",
        n: int = 8,
        freshness: Optional[str] = None,
        country: Optional[str] = None,
        safesearch: str = "moderate",
        reconciling: bool = True,
        fetch_content: bool = True,
        *args,
        **_kw
) -> Dict[str, Any]:
    """
    Signature-agnostic metadata extractor.

    Tries to recover args by position if not passed by name.
    """
    # recover positional where possible
    if queries is None:
        if len(args) >= 2:
            queries = args[1]
        else:
            queries = _kw.get("queries")

    if objective is None:
        if len(args) >= 3:
            objective = args[2]
        else:
            objective = _kw.get("objective")

    if refinement == "balanced":
        # only override if explicitly provided in args/kw
        if len(args) >= 4 and args[3]:
            refinement = args[3]
        else:
            refinement = _kw.get("refinement", refinement)

    if n == 8:
        if len(args) >= 5 and args[4]:
            try:
                n = int(args[4])
            except Exception:
                pass
        else:
            try:
                n = int(_kw.get("n", n))
            except Exception:
                pass

    freshness = _kw.get("freshness", freshness)
    country = _kw.get("country", country)
    safesearch = _kw.get("safesearch", safesearch)
    reconciling = bool(_kw.get("reconciling", reconciling))
    fetch_content = bool(_kw.get("fetch_content", fetch_content))

    q_list = _parse_queries_arg(queries)

    return {
        "objective": objective,
        "refinement": refinement,
        "n": int(n),
        "freshness": freshness,
        "country": country,
        "safesearch": safesearch,
        "reconciling": bool(reconciling),
        "fetch_content": bool(fetch_content),
        "query_variants": q_list,
    }


def ws_provider_extractor(result: Any = None, *_a, **_kw) -> str:
    try:
        # If web_search returns list[dict]
        if isinstance(result, list) and result:
            # try direct fields first
            p = result[0].get("provider") or result[0].get("vendor")
            if p:
                return str(p)

            # optional: majority vote if mixed
            counts = {}
            for r in result:
                pv = r.get("provider") or r.get("vendor")
                if not pv:
                    continue
                counts[pv] = counts.get(pv, 0) + 1
            if counts:
                return str(max(counts.items(), key=lambda kv: kv[1])[0])
    except Exception:
        pass

    # fallback to explicit kw
    p = _kw.get("provider")
    if p:
        return str(p)

    return "unknown"


def ws_model_extractor(
        _self: Any = None,
        *args,
        **kwargs
) -> str:
    """
    Signature-agnostic model extractor for web search.

    Goal here is usually 'which search backend' rather than an LLM model.
    """
    # 1) explicit kw override
    model = kwargs.get("model") or kwargs.get("backend_name")
    if model:
        return str(model)

    # 2) try _self.search_backend (method style)
    try:
        backend = getattr(_self, "search_backend", None)
        if backend:
            return str(getattr(backend, "name", None) or "web-search")
    except Exception:
        pass

    # 3) infer from provider kw if present
    provider = kwargs.get("provider")
    if provider:
        return str(provider)

    return "web-search"