"""Runtime configuration for the standalone prebuilt-ReAct prototype.

Reads everything from the environment with sensible local-dev defaults. Nothing
here imports an LLM SDK or a database driver — those are loaded lazily in the
modules that need them, so ``import lg_prebuilt_agent.agent`` works without a
live database or API key (useful for inspecting the graph structure and for the
offline stub path).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/lg_prebuilt"

# Provider defaults. `provider` picks which LangChain chat model to build when a
# key is present; the offline stub is used when no key is set for the provider.
DEFAULT_PROVIDER = "openai"           # openai | anthropic
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# The model-input token budget the pre_model_hook trims to each turn. This bounds
# what the model sees (and pays for) as the conversation grows — the checkpointer
# keeps the full history, the hook only trims the *view*.
DEFAULT_CTX_TOKENS = 3000


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    provider: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_PROVIDER", DEFAULT_PROVIDER).strip().lower())
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    openai_model: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
    anthropic_model: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL))
    ctx_tokens: int = field(default_factory=lambda: int(os.getenv("LG_PREBUILT_CTX_TOKENS", DEFAULT_CTX_TOKENS)))

    @property
    def model_name(self) -> str:
        return self.anthropic_model if self.provider == "anthropic" else self.openai_model

    @property
    def api_key(self) -> str | None:
        return self.anthropic_api_key if self.provider == "anthropic" else self.openai_api_key

    @property
    def offline(self) -> bool:
        """No API key for the selected provider -> run the deterministic offline
        stub (canned answers, still exercises the full create_react loop + tools).
        The graph shape stays fully inspectable without spending on an LLM."""
        return not self.api_key


def get_config() -> Config:
    return Config()
