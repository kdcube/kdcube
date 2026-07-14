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


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    chat_model: str = field(default_factory=lambda: os.getenv("LG_CHAT_MODEL", DEFAULT_CHAT_MODEL))
    embed_model: str = field(default_factory=lambda: os.getenv("LG_EMBED_MODEL", DEFAULT_EMBED_MODEL))
    embed_dim: int = field(default_factory=lambda: int(os.getenv("LG_EMBED_DIM", DEFAULT_EMBED_DIM)))

    @property
    def offline(self) -> bool:
        """No API key -> run in stub mode (deterministic embeddings + canned
        answers). The graph shape stays fully inspectable and, if a DB is
        reachable, the vector stores still work end to end."""
        return not self.openai_api_key


def get_config() -> Config:
    return Config()
