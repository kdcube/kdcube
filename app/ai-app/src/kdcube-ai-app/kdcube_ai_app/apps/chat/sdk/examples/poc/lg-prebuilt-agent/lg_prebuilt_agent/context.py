"""Context management for the prebuilt ReAct agent.

The conversation IS the ``messages`` list, persisted by the checkpointer (Postgres
in the CLI) keyed by ``thread_id``. Left unbounded, that list grows every turn and
every model call would re-send the whole history — unbounded context and cost.

``build_pre_model_hook(config)`` returns a ``pre_model_hook`` for
``create_react_agent``: it runs BEFORE the model node each turn and trims the
messages the model sees to a token budget, keeping the most recent turns plus the
system message. It returns the trimmed set under ``llm_input_messages``, which
bounds the model's *view* WITHOUT deleting anything from the stored history — the
checkpointer still holds every turn, the model just doesn't see all of it.

This is the clean reference default. A fuller alternative is a summarization node
that compacts old turns into a running summary before trimming; that keeps older
context available at a lower token cost. This prototype implements the trim, which
is enough to keep context and spend controlled.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from langchain_core.messages import trim_messages

from .config import Config


def build_pre_model_hook(config: Config) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    max_tokens = config.ctx_tokens

    def pre_model_hook(state: Dict[str, Any]) -> Dict[str, Any]:
        messages = state.get("messages") or []
        try:
            trimmed = trim_messages(
                messages,
                max_tokens=max_tokens,
                # A cheap, dependency-free counter (~4 chars/token) keeps the
                # prototype self-contained. Swap for the model's tokenizer for
                # exact budgeting.
                token_counter=_approx_token_counter,
                strategy="last",         # keep the most RECENT messages
                start_on="human",        # a valid model input starts on a human turn
                include_system=True,     # always keep the system message
                allow_partial=False,
            )
        except Exception:
            # Trimming is best-effort: never fail a turn over context management.
            trimmed = messages
        # Return under llm_input_messages: bound the model's VIEW for this turn,
        # leave the stored history (in the checkpointer) untouched.
        return {"llm_input_messages": trimmed or messages}

    return pre_model_hook


def _approx_token_counter(messages) -> int:
    total = 0
    for m in messages:
        content = getattr(m, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        total += max(1, len(content) // 4)
    return total
