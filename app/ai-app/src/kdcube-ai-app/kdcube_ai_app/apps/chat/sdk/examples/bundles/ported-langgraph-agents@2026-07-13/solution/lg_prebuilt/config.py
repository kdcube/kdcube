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

# CODE EXECUTION (run_python) is a HOST feature, not a standalone-env knob: it is
# configured on the bundle at `config.tools.code_exec` (enabled | runtime |
# timeout_s) and read at runtime via bundle_prop by platform/code_exec.py — see
# config/bundles.template.yaml. It is intentionally absent from this standalone
# standalone Config (no isolated runtime / hosting edge exists offline).

# Provider defaults. `provider` picks which LangChain chat model to build when a
# key is present; the offline stub is used when no key is set for the provider.
DEFAULT_PROVIDER = "openai"           # openai | anthropic
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# How the agent bounds the model's view of a growing conversation. The checkpointer
# keeps the full history; compaction only bounds what the model SEES (and pays for)
# as the conversation grows.
#   "summarize" (default) — LangChain's SummarizationMiddleware: fold older turns
#                           into a summary and keep recent turns verbatim. Runs its
#                           own summarization LLM call, so it needs a summary model;
#                           with none (offline) the turn runs with no middleware.
#   "trim"                — no summarization middleware (offline turns are short).
DEFAULT_CONTEXT_STRATEGY = "summarize"

# Summarization triggers once the accumulated conversation exceeds this many tokens
# (older turns are then folded into a summary). Maps to the middleware's
# ``trigger=("tokens", N)``.
DEFAULT_SUMMARY_TRIGGER_TOKENS = 2000

# How many of the most recent messages the middleware keeps verbatim after it
# summarizes the older ones. Maps to the middleware's ``keep=("messages", N)``.
DEFAULT_SUMMARY_KEEP_MESSAGES = 20

# Output-token ceiling for the answer model. This agent passes WHOLE PAYLOADS as
# tool arguments (`run_python` carries the program text — an HTML page, a script),
# so the ceiling must fit narration + a complete tool call. A ceiling that is too
# low cuts the response MID-TOOL-CALL: the truncated arguments fail tool validation,
# the model sees only "invalid/missing argument", retries into the same wall, and
# the loop burns calls until the graph's recursion limit (observed live: 12 identical
# 1200-token attempts). The model stops on its own well before this on normal turns;
# the ceiling is a safety cap, not a target.
DEFAULT_MAX_TOKENS = 16384


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    provider: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_PROVIDER", DEFAULT_PROVIDER).strip().lower())
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    openai_model: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
    anthropic_model: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL))
    context_strategy: str = field(default_factory=lambda: os.getenv("LG_PREBUILT_CONTEXT_STRATEGY", DEFAULT_CONTEXT_STRATEGY).strip().lower())
    summary_trigger_tokens: int = field(default_factory=lambda: int(os.getenv("LG_PREBUILT_SUMMARY_TRIGGER_TOKENS", DEFAULT_SUMMARY_TRIGGER_TOKENS)))
    summary_keep_messages: int = field(default_factory=lambda: int(os.getenv("LG_PREBUILT_SUMMARY_KEEP_MESSAGES", DEFAULT_SUMMARY_KEEP_MESSAGES)))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LG_PREBUILT_MAX_TOKENS", DEFAULT_MAX_TOKENS)))

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
