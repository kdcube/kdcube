"""The chat model the prebuilt ReAct agent runs on, with an offline stub.

``build_chat_model(config)`` returns a LangChain ``BaseChatModel``:

  - online  — ``ChatOpenAI`` or ``ChatAnthropic`` (per ``config.provider``),
              streaming, ready for ``create_react_agent``.
  - offline — ``StubChatModel``: a deterministic, tool-aware fake that drives the
              REAL create_react loop (it decides to call ``calc`` on arithmetic
              questions, then answers from the tool result) so the whole graph
              shape — agent -> tools -> agent -> answer — runs without an API key.

The stub matches how real tool-calling models behave in the ReAct loop: a
tool-deciding turn emits an empty-content message carrying a tool call; the final
turn emits the answer text and no tool call. That invariant is what the streaming
layer relies on to stream only the final answer (see ``cli.py`` /, when hosted,
``platform/stream_adapter.py``).
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from .config import Config

_ARITH = re.compile(r"[-+*/().\d\s]*\d\s*[-+*/]\s*\d[-+*/().\d\s]*")


class StubChatModel(BaseChatModel):
    """Deterministic offline model that still exercises the full ReAct loop."""

    @property
    def _llm_type(self) -> str:
        return "lg-prebuilt-stub"

    # create_react_agent binds tools onto the model; the stub ignores the schema
    # and decides tool use heuristically, so it stays dependency-free.
    def bind_tools(self, tools: Any, **kwargs: Any) -> "StubChatModel":
        return self

    @staticmethod
    def _last_human(messages: List[BaseMessage]) -> str:
        for m in reversed(messages):
            if getattr(m, "type", None) == "human":
                c = getattr(m, "content", "")
                return c if isinstance(c, str) else str(c)
        return ""

    @staticmethod
    def _last_tool_result(messages: List[BaseMessage]) -> Optional[str]:
        # Only a tool result from the CURRENT turn counts: scan back from the end
        # and stop at the last human message, so a prior turn's tool result (still
        # in the checkpointed history) never bleeds into this turn's answer.
        for m in reversed(messages):
            if getattr(m, "type", None) == "human":
                return None
            if isinstance(m, ToolMessage):
                return str(m.content)
        return None

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        tool_result = self._last_tool_result(messages)
        question = self._last_human(messages)

        # No tool run yet + an arithmetic-looking question -> decide to call calc.
        if tool_result is None and _ARITH.search(question or ""):
            expr = _ARITH.search(question).group(0).strip().rstrip("?").strip()
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "calc",
                    "args": json.dumps({"expression": expr}),
                    "id": "stub-calc-1",
                    "index": 0,
                }],
            )
            yield ChatGenerationChunk(message=chunk)
            return

        # Final answer turn (no tool call): stream text tokens.
        if tool_result is not None:
            parts = ["[offline stub] ", "The tool returned ", str(tool_result), ". ",
                     "No API key is set, so this is a canned answer."]
        else:
            lines = (question or "").strip().splitlines()
            head = lines[0][:120] if lines else "(no question text)"
            parts = ["[offline stub] ", "No API key set, so this is a canned answer.\n",
                     f"You asked: {head}"]
        for part in parts:
            yield ChatGenerationChunk(message=AIMessageChunk(content=part))

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        parts: List[str] = []
        tool_calls = None
        async for chunk in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            msg = chunk.message
            parts.append(msg.content or "")
            if getattr(msg, "tool_call_chunks", None):
                # Collapse streamed tool_call_chunks into a full tool_calls list.
                tc = msg.tool_call_chunks[0]
                tool_calls = [{"name": tc["name"], "args": json.loads(tc["args"]), "id": tc["id"]}]
        message = AIMessage(content="".join(parts), tool_calls=tool_calls or [])
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("StubChatModel is async-only; use ainvoke()/astream().")


def build_chat_model(config: Config) -> BaseChatModel:
    """The chat model for the agent: a real LangChain model online, the stub
    offline. Constructed lazily so importing this module needs no provider SDK."""
    if config.offline:
        return StubChatModel()
    if config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # lazy

        return ChatAnthropic(
            model=config.anthropic_model,
            api_key=config.anthropic_api_key,
            temperature=0.2,
            streaming=True,
        )
    from langchain_openai import ChatOpenAI  # lazy

    return ChatOpenAI(
        model=config.openai_model,
        api_key=config.openai_api_key,
        temperature=0.2,
        streaming=True,
    )
