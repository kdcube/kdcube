"""Runtime configuration for the standalone research-assistant prototype.

Reads everything from the environment with sensible local-dev defaults.
Nothing here imports an LLM SDK or a database driver — those are loaded lazily
in the modules that need them, so `import lg_solution.graph` works without a
live database or API key (useful for inspecting the graph structure).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


# Embedding width. Must match the OpenAI embedding model below *and* the
# pgvector column dimension. The offline stub produces vectors of this width too,
# so the vector stores stay usable (against a real DB) even without an API key.
DEFAULT_EMBED_DIM = 1536

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/lg_solution"
DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

# Long-term memory backend (see solution/memory.py). Two ways of doing LangGraph
# long-term memory, config-selectable so the demo shows both:
#   "custom"          -> a hand-rolled async pgvector table (SemanticMemory)
#   "langgraph_store" -> LangGraph's native AsyncPostgresStore (StoreMemory)
DEFAULT_MEMORY_BACKEND = "custom"

# ── conversation compaction (the `compact` node) ─────────────────────────────
# lg-solution's answer node already sends only the CURRENT turn's system+user to
# the model (no history replay), so its model-input context is bounded per turn.
# The `compact` node adds bounded multi-turn CONTINUITY: it folds the checkpointed
# `messages` history into a running summary injected into the answer prompt.
#   "summarize" (default) — LangMem summarize_messages on a DISTINCT accounted
#                           summary role; degrades to trim without a summary model.
#   "trim"                — keep only the most recent turns, no extra model call.
DEFAULT_CONTEXT_STRATEGY = "summarize"

# Summarize the prior conversation only once it exceeds this many tokens; below it,
# the recent turns are passed through verbatim (nothing to compact yet).
DEFAULT_SUMMARY_TRIGGER_TOKENS = 1500

# Token budget for the running summary itself, and for the recent-turns view kept
# verbatim alongside it (also the trim budget on the offline/degraded path).
DEFAULT_SUMMARY_MAX_TOKENS = 256
DEFAULT_CTX_TOKENS = 2000

# Output-token ceiling for the answer model. A ceiling below a full answer cuts the
# response mid-generation SILENTLY (the turn "succeeds" with an amputated answer),
# so it is a generous safety cap, not a target — the model stops on its own.
DEFAULT_MAX_TOKENS = 8192


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    chat_model: str = field(default_factory=lambda: os.getenv("LG_CHAT_MODEL", DEFAULT_CHAT_MODEL))
    embed_model: str = field(default_factory=lambda: os.getenv("LG_EMBED_MODEL", DEFAULT_EMBED_MODEL))
    embed_dim: int = field(default_factory=lambda: int(os.getenv("LG_EMBED_DIM", DEFAULT_EMBED_DIM)))
    memory_backend: str = field(default_factory=lambda: os.getenv("LG_MEMORY_BACKEND", DEFAULT_MEMORY_BACKEND))
    context_strategy: str = field(default_factory=lambda: os.getenv("LG_CONTEXT_STRATEGY", DEFAULT_CONTEXT_STRATEGY).strip().lower())
    summary_trigger_tokens: int = field(default_factory=lambda: int(os.getenv("LG_SUMMARY_TRIGGER_TOKENS", DEFAULT_SUMMARY_TRIGGER_TOKENS)))
    summary_max_tokens: int = field(default_factory=lambda: int(os.getenv("LG_SUMMARY_MAX_TOKENS", DEFAULT_SUMMARY_MAX_TOKENS)))
    ctx_tokens: int = field(default_factory=lambda: int(os.getenv("LG_CTX_TOKENS", DEFAULT_CTX_TOKENS)))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LG_MAX_TOKENS", DEFAULT_MAX_TOKENS)))

    @property
    def offline(self) -> bool:
        """No API key -> run in stub mode (deterministic embeddings + canned
        answers). The graph shape stays fully inspectable and, if a DB is
        reachable, the vector stores still work end to end."""
        return not self.openai_api_key


def get_config() -> Config:
    return Config()
