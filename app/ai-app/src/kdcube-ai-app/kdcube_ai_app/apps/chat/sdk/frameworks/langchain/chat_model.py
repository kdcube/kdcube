# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""A LangChain chat model backed by KDCube's model service.

`KDCubeChatModel` is a drop-in `BaseChatModel` that routes every completion
through `ModelServiceBase.stream_model_text_tracked`, so a ported
LangChain/LangGraph agent keeps using `model.astream(...)` / `model.ainvoke(...)`
unchanged while the platform accounts the call automatically and the tokens still
stream live.

It is provider-agnostic and bundle-agnostic: hand it a `models_service` handle
plus a role/params and it behaves like any other streaming chat model, including
surfacing `on_chat_model_stream` events through `graph.astream_events(...)`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Type, Union

# Diagnostic logger for the tool-calling path: shows whether tools are actually
# forwarded to the accounted model call and whether the provider emits tool
# events. INFO-level and temporary — the tool-call bridge is otherwise silent.
LOGGER = logging.getLogger("kdcube.langchain.chat_model")

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool


def _normalize_tool(tool: Any) -> Dict[str, Any]:
    """Convert one LangChain-style tool spec to the platform's tool dict.

    The platform's model service (``ModelServiceBase.stream_model_text``) consumes
    a normalized ``{"name", "description", "parameters"}`` list and then applies
    the provider-specific mapping itself (``tools_to_anthropic_format`` /
    ``tools_to_openai_format`` in ``message_utils``). So here we only need to bring
    an arbitrary tool spec (``BaseTool``, a pydantic model, a plain function, or a
    dict) into that shared normalized shape. ``convert_to_openai_tool`` handles
    every accepted input form; we then unwrap its ``function`` envelope.
    """
    oai = convert_to_openai_tool(tool)
    fn = oai.get("function", oai)
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "parameters": fn.get("parameters", {}),
    }


def _tool_event_to_chunk(ev: Dict[str, Any]) -> Optional[ChatGenerationChunk]:
    """Translate a platform tool-stream event into a streaming ``tool_call_chunks``.

    ``stream_model_text_tracked`` surfaces two provider-agnostic tool events via
    ``on_tool_result_event``:

    - ``{"type":"tool.start", index, id, name}`` — a tool call begins; id+name known.
    - ``{"type":"tool.arguments_delta", index, delta}`` — a slice of the JSON args.

    Both map onto LangChain's streaming tool-call representation, a
    ``tool_call_chunks`` entry ``{"name","args","id","index"}``. Emitting them as
    ``AIMessageChunk`` chunks lets LangGraph / ``create_react_agent`` accumulate
    them (by ``index``, concatenating ``args``) into the final message's
    ``.tool_calls``. The completing ``tool.use`` event is intentionally ignored: it
    would double-count, since start + arguments_delta already reconstruct the call.
    """
    etype = ev.get("type")
    if etype == "tool.start":
        tcc = {
            "name": ev.get("name") or "",
            "args": "",
            "id": ev.get("id") or "",
            "index": ev.get("index", 0),
        }
        return ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[tcc]))
    if etype == "tool.arguments_delta":
        tcc = {
            "name": None,
            "args": ev.get("delta") or "",
            "id": None,
            "index": ev.get("index", 0),
        }
        return ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[tcc]))
    return None


def _complete_tool_use_to_chunk(ev: Dict[str, Any]) -> ChatGenerationChunk:
    """Turn a COMPLETE ``tool.use`` event — a whole tool call delivered at once,
    not as streamed deltas — into a single ``tool_call_chunks`` entry.

    Some models/providers surface a tool call only as one ``tool.use`` (the full
    name + parsed input) with no preceding ``tool.start`` / ``tool.arguments_delta``
    stream. Without emitting a chunk here, such a turn yields ZERO chunks and
    LangChain's ``agenerate_from_stream`` raises "No generations found in stream",
    crashing the turn before it can even route to the tool. The caller only invokes
    this when the tool-call index was NOT already streamed, so it never
    double-counts a streamed call's arguments. ``input`` is the parsed args dict;
    ``tool_call_chunks`` want the args as a JSON string.
    """
    args = ev.get("input")
    if isinstance(args, str):
        args_str = args
    else:
        try:
            args_str = json.dumps(args or {})
        except Exception:
            args_str = "{}"
    tcc = {
        "name": ev.get("name") or "",
        "args": args_str,
        "id": ev.get("id") or "",
        "index": ev.get("index", 0),
    }
    return ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[tcc]))


class KDCubeChatModel(BaseChatModel):
    """Stream completions through KDCube's accounted model service.

    Construction:
        KDCubeChatModel(models_service=ms, role="my.role",
                        temperature=0.2, max_tokens=1200)

    ``models_service`` is a ``ModelServiceBase`` (typed ``Any`` so this module
    stays free of a hard import and so pydantic performs no validation on it).
    """

    # Declared as pydantic fields so BaseChatModel's constructor accepts them.
    models_service: Any = None
    role: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1200

    @property
    def _llm_type(self) -> str:
        return "kdcube-chat"

    # -- tool binding -------------------------------------------------------

    def bind_tools(
        self,
        tools: Sequence[Union[Dict[str, Any], Type, Callable, BaseTool]],
        *,
        tool_choice: Optional[Union[str, dict]] = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, BaseMessage]:
        """Bind tools so ``create_react_agent`` (and any tool-using agent) can run.

        Follows the standard LangChain pattern (as in
        ``ChatOpenAI.bind_tools``): normalize the tool specs, then stash them —
        plus ``tool_choice`` — as bound kwargs via ``Runnable.bind``. Those kwargs
        flow into ``_astream`` / ``_agenerate`` on every subsequent call, where they
        are forwarded to the accounted platform streaming call. Tools are normalized
        to the platform's shared ``{"name","description","parameters"}`` shape; the
        model service applies the provider-specific mapping itself.
        """
        formatted = [_normalize_tool(t) for t in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    # -- streaming ----------------------------------------------------------

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Bridge ``stream_model_text_tracked``'s ``on_delta`` callback into an
        ``asyncio.Queue`` and re-yield each token as a ``ChatGenerationChunk``.

        ``stream_model_text_tracked`` is a single coroutine that drives an
        ``async on_delta(text)`` callback per token and returns a final dict.
        To yield those tokens as they arrive, the model call runs as a child task
        (the producer) that pushes tokens onto a queue while this generator (the
        consumer) drains and yields them.

        Accounting note: ``asyncio.create_task`` snapshots the *current*
        ``contextvars.Context`` at creation time (PEP 567 / CPython 3.11). We
        create the producer here — inside the turn's bound accounting context —
        so the child task's context copy includes the accounting envelope, and
        ``stream_model_text_tracked``'s ``@track_llm`` records against this turn.
        The child only needs to *read* that envelope; nothing has to propagate
        back out, so a copied context is correct (and safer than mutating the
        parent's). The producer runs in the same event loop as this generator —
        no thread/executor hop — which is what accounting requires.
        """
        ms = self.models_service
        client = ms.get_client(self.role, self.temperature)
        client_cfg = ms.describe_client(client, role=self.role)

        # ``tools`` / ``tool_choice`` arrive as bound kwargs from ``bind_tools``
        # (via ``Runnable.bind``). When no tools are bound the call below is
        # identical to the text-only path — no tool kwargs are passed.
        tools = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice")

        # [tool-debug] Whether tools reached this accounted call. tools_bound=0 on a
        # turn that should call a tool => the binding never propagated (the model
        # narrates instead of calling). Non-zero here but no `tool event` line below
        # => the provider chose not to call (model/tools_support), not the bridge.
        LOGGER.info(
            "[kdcube-chat] _astream role=%s tools_bound=%d tool_names=%s tool_choice=%s",
            self.role,
            len(tools or []),
            [str((t or {}).get("name") or "") for t in (tools or [])],
            tool_choice,
        )

        queue: asyncio.Queue = asyncio.Queue()
        _DONE = object()

        async def on_delta(text: str) -> None:
            if text:
                await queue.put(ChatGenerationChunk(message=AIMessageChunk(content=text)))

        # Tool-call indices already delivered as streamed deltas (tool.start /
        # tool.arguments_delta). A model may instead deliver a tool call as ONE
        # complete `tool.use`; we synthesize a chunk from it ONLY when its index
        # was not streamed, so a streamed call is never double-counted by its
        # trailing `tool.use`.
        streamed_tc_indices: set = set()
        # Evidence trail for the interruption diagnosis below: per tool-call index,
        # the name and the reconstructed argument JSON (streamed deltas concatenate;
        # a complete tool.use overwrites). If the response is cut at the output
        # budget, this is exactly "what the model did before it was interrupted".
        tool_call_evidence: Dict[int, Dict[str, Any]] = {}

        def _note_evidence(index: int, name: Optional[str], args_piece: str, *, append: bool) -> None:
            row = tool_call_evidence.setdefault(index, {"name": "", "args": ""})
            if name:
                row["name"] = name
            row["args"] = (row["args"] + args_piece) if append else args_piece

        async def on_tool_event(ev: Dict[str, Any]) -> None:
            LOGGER.info(
                "[kdcube-chat] tool event role=%s type=%s name=%s",
                self.role, ev.get("type"), ev.get("name"),
            )
            etype = ev.get("type")
            idx_ev = int(ev.get("index", 0) or 0)
            if etype == "tool.start":
                _note_evidence(idx_ev, ev.get("name"), "", append=True)
            elif etype == "tool.arguments_delta":
                _note_evidence(idx_ev, None, str(ev.get("delta") or ""), append=True)
            elif etype == "tool.use":
                args = ev.get("input")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args or {}, ensure_ascii=False)
                    except Exception:
                        args = str(args)
                _note_evidence(idx_ev, ev.get("name"), args, append=False)
            if etype in ("tool.start", "tool.arguments_delta"):
                streamed_tc_indices.add(ev.get("index", 0))
                chunk = _tool_event_to_chunk(ev)
            elif etype == "tool.use" and ev.get("index", 0) not in streamed_tc_indices:
                # A complete, non-streamed tool call — synthesize its chunk so the
                # stream is non-empty and the tool call reaches the agent loop.
                chunk = _complete_tool_use_to_chunk(ev)
            else:
                chunk = None
            if chunk is not None:
                await queue.put(chunk)

        produce_kwargs: Dict[str, Any] = dict(
            on_delta=on_delta,
            role=self.role,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            client_cfg=client_cfg,
        )
        if tools:
            produce_kwargs["tools"] = tools
            produce_kwargs["tool_choice"] = tool_choice
            produce_kwargs["on_tool_result_event"] = on_tool_event

        async def _produce() -> Any:
            try:
                return await ms.stream_model_text_tracked(
                    client,
                    list(messages),
                    **produce_kwargs,
                )
            finally:
                await queue.put(_DONE)

        producer = asyncio.create_task(_produce())
        yielded_any = False
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                text = item.message.content or ""
                if run_manager is not None:
                    await run_manager.on_llm_new_token(text, chunk=item)
                yielded_any = True
                yield item
            # Re-raise any producer failure and let @track_llm finalize.
            result = await producer
            # A model-call error is returned as a SOFT result (text="Model call
            # failed…", service_error=…) rather than raised. Surface it as a real
            # failure so the turn ERRORS (the door emits chat.error) instead of
            # rendering the error text as the assistant's answer in the timeline.
            if isinstance(result, dict) and result.get("service_error"):
                from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError
                raise ServiceException(ServiceError.model_validate(result["service_error"]))
            # A response that spent the WHOLE output budget was cut mid-generation.
            # Benign-ish for prose (a visibly amputated answer); poisonous on a
            # tool-call turn — the truncated argument JSON fails tool validation
            # downstream with a misleading "missing/invalid argument", the model
            # retries the identical call into the same ceiling, and the loop burns
            # calls until the graph recursion limit (observed live:
            # run_python(code=<full HTML page>) × 12 at exactly max_tokens each).
            # The stop reason does not survive the bridge, so THIS is the one place
            # that can explain the interruption — and it must explain it to BOTH
            # audiences, never leave them guessing:
            #   - the MODEL: an in-band notice appended to its own (interrupted)
            #     message, so next round it sees WHAT happened and HOW to proceed,
            #     instead of confabulating around "missing argument";
            #   - the LOG: the evidence — budget, spend, and each reconstructed
            #     tool call's name, argument size, and the tail where the cut hit.
            if isinstance(result, dict) and self.max_tokens:
                out_tokens = int((result.get("usage") or {}).get("output_tokens") or 0)
                if out_tokens >= int(self.max_tokens):
                    evidence = []
                    for i in sorted(tool_call_evidence):
                        row = tool_call_evidence[i]
                        args_str = row.get("args") or ""
                        evidence.append(
                            f"tool_call[{i}] {row.get('name') or '?'} args={len(args_str):,} chars"
                            f" tail={args_str[-160:]!r}"
                        )
                    LOGGER.warning(
                        "[kdcube-chat] role=%s INTERRUPTED: response spent the full output "
                        "budget (max_tokens=%d, output_tokens=%d) and was cut mid-generation. %s",
                        self.role, int(self.max_tokens), out_tokens,
                        ("Reconstructed tool calls (truncated as delivered): " + "; ".join(evidence))
                        if evidence else "No tool call in flight (prose answer amputated).",
                    )
                    if tool_call_evidence:
                        # Tell the MODEL, in its own message, in plain terms it can
                        # act on. The agent loop suppresses tool-turn text from the
                        # user-visible answer, but the note is checkpointed into
                        # history, so the next round reads it right beside the
                        # truncated call and the tool's validation error.
                        notice = (
                            "\n[Notice: this response was interrupted — it reached the "
                            f"maximum output length ({int(self.max_tokens)} tokens) while the tool call "
                            "arguments were still being written. The tool call above is "
                            "INCOMPLETE as delivered; any 'missing/invalid argument' error is "
                            "a consequence of the interruption, not of what was intended. Do "
                            "not repeat the identical call — produce a smaller payload (e.g. "
                            "shorter code, or build the output in several smaller steps).]"
                        )
                        chunk = ChatGenerationChunk(message=AIMessageChunk(content=notice))
                        if run_manager is not None:
                            await run_manager.on_llm_new_token(notice, chunk=chunk)
                        yielded_any = True
                        yield chunk
            # Robustness: a model turn that produced NO chunks (no text delta and no
            # tool event) leaves an EMPTY stream, which LangChain's
            # `agenerate_from_stream` rejects with "No generations found in stream" —
            # crashing the whole turn (and, in an agent loop, wasting the turn). An
            # empty model response must never crash a turn: emit one final chunk (the
            # returned text if any, else empty) so the stream is well-formed and the
            # caller simply gets an empty answer. This is a backstop; the common
            # tool-only case is already covered by synthesizing a chunk from
            # `tool.use` above.
            if not yielded_any:
                fallback_text = ""
                if isinstance(result, dict):
                    fallback_text = str(result.get("text") or "")
                LOGGER.info(
                    "[kdcube-chat] role=%s produced NO chunks — emitting fallback "
                    "chunk (text_len=%d) to avoid an empty stream",
                    self.role, len(fallback_text),
                )
                yield ChatGenerationChunk(message=AIMessageChunk(content=fallback_text))
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(BaseException):
                    await producer

    # -- non-streamed -------------------------------------------------------

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Non-streamed completion: drain ``_astream`` and collapse to one message.

        Routing through the same streaming bridge keeps a single accounted code
        path, so ``ainvoke`` and ``astream`` bill identically. Chunks are merged
        with ``AIMessageChunk`` addition, which concatenates content and accumulates
        ``tool_call_chunks`` by index — so when the model made tool calls the final
        ``AIMessage`` exposes ``.tool_calls`` (routing the ReAct loop to the tools
        node), and otherwise it carries plain content.
        """
        merged: Optional[AIMessageChunk] = None
        async for chunk in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            merged = chunk.message if merged is None else merged + chunk.message

        if merged is None:
            message: AIMessage = AIMessage(content="")
        else:
            message = AIMessage(
                content=merged.content,
                additional_kwargs=merged.additional_kwargs,
                tool_calls=list(merged.tool_calls),
                invalid_tool_calls=list(merged.invalid_tool_calls),
                id=merged.id,
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        # Required by BaseChatModel's ABC. This adapter is async-only because the
        # underlying accounted streaming path is a coroutine; callers use
        # ``ainvoke``/``astream``.
        raise NotImplementedError(
            "KDCubeChatModel is async-only; use ainvoke()/astream()."
        )
