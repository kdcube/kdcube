# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Models gateway — locally served models behind the platform's custom-model
protocol.

A standalone translator used by the existing ``provider: custom`` role path
(`CustomModelClient` in ``infra/service_hub/inventory.py``) to serve from a
local inference runtime — Ollama first.

    CustomModelClient                 this gateway                Ollama
    -----------------                 ------------                ------
    POST /generate                    translate                   POST /api/chat
      {"model",                      ─▶ messages/options         ─▶ {"model","messages",
       "inputs":[{role,content}],
       "parameters":{...}}                                         "stream","options"}
    SSE data:{"delta": ...}        ◀─ per-chunk translate       ◀─ JSONL {"message":{...}}
    SSE data:{"final":true,          usage from                     {"done":true,
              "usage":{...}}         eval counts                     "prompt_eval_count",...}
    data: [DONE]

The platform client transmits the selected model (top-level ``model`` in the
request); the gateway routes it to Ollama, so one gateway instance serves
every locally pulled model. ``GATEWAY_MODEL`` is the fallback for direct
requests that carry no model.

Environment:
    GATEWAY_MODEL       fallback Ollama model tag for requests without a
                        model name (default: qwen3:0.6b)
    GATEWAY_API_KEY     when set, requests must carry `Authorization: Bearer <it>`
    OLLAMA_BASE_URL     default http://localhost:11434
    GATEWAY_KEEP_ALIVE  Ollama keep_alive (default 30m) — keeps weights warm
                        between turns instead of reloading every request
    GATEWAY_NUM_CTX     standalone fallback for the context window (tokens)
                        when a request carries no `parameters.num_ctx` — in a
                        platform deployment the descriptor
                        (services.llm.custom.num_ctx) sends it per request
                        and wins. Ollama's runner default (32768 as of 0.24)
                        SILENTLY truncates longer prompts from the FRONT —
                        the system instruction goes first ("truncating input
                        prompt" in the Ollama server log). Agent-platform
                        prompts run tens of thousands of tokens: size the
                        window to the largest prompt you serve, within the
                        machine's memory (KV cache grows linearly with the
                        window). 0/unset = Ollama default.

Run (host, not containerized — proc containers reach it via
host.docker.internal):
    uvicorn kdcube_ai_app.apps.models_gateway.app:app --port 11500
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, AsyncIterator, Dict, List

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("kdcube.models_gateway")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
GATEWAY_MODEL = os.getenv("GATEWAY_MODEL", "qwen3:0.6b")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
GATEWAY_KEEP_ALIVE = os.getenv("GATEWAY_KEEP_ALIVE", "30m")
# Hybrid-reasoning models (Qwen3*) route reasoning to a separate `thinking`
# field and can spend the whole token budget there. The platform's custom
# protocol carries one delta channel — agent prompts drive their own output
# structure — so thinking is disabled by default. Set GATEWAY_THINK=1 for
# raw experiments; thinking text is dropped either way.
GATEWAY_THINK = os.getenv("GATEWAY_THINK", "0").strip().lower() in ("1", "true", "yes")
GATEWAY_NUM_CTX = int(os.getenv("GATEWAY_NUM_CTX", "0") or 0)

app = FastAPI(title="KDCube Models Gateway", version="0.1.0")


def _require_auth(request: Request) -> None:
    if not GATEWAY_API_KEY:
        return
    header = request.headers.get("authorization") or ""
    if header != f"Bearer {GATEWAY_API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _image_base64(block: Dict[str, Any]) -> str:
    """Extract raw base64 from the platform's image block shapes:
    Anthropic-style {"type":"image","source":{"type":"base64","data":...}}
    and OpenAI-style {"type":"image_url","image_url":{"url":"data:...;base64,..."}}.
    """
    source = block.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        data = source.get("data")
        if isinstance(data, str):
            return data
    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        url = str(image_url.get("url") or "")
        if url.startswith("data:") and ";base64," in url:
            return url.split(";base64,", 1)[1]
    return ""


def _split_content(content: Any) -> tuple[str, List[str]]:
    """Structured content blocks → (text, base64 images). Multimodal models
    served by Ollama take images per message in `images`; text stays in
    `content`. Non-text/non-image blocks are dropped."""
    if isinstance(content, str):
        return content, []
    if isinstance(content, list):
        parts: List[str] = []
        images: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                image = _image_base64(block)
                if image:
                    images.append(image)
                    continue
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts), images
    return str(content or ""), []


def _requested_model(payload: Dict[str, Any]) -> str:
    return str(payload.get("model") or "").strip() or GATEWAY_MODEL


def _to_ollama_request(payload: Dict[str, Any], *, stream: bool) -> Dict[str, Any]:
    inputs = payload.get("inputs") or []
    parameters = dict(payload.get("parameters") or {})
    messages: List[Dict[str, Any]] = []
    for m in inputs:
        if not isinstance(m, dict):
            continue
        text, images = _split_content(m.get("content"))
        message: Dict[str, Any] = {"role": str(m.get("role") or "user"), "content": text}
        if images:
            message["images"] = images
        messages.append(message)
    options: Dict[str, Any] = {}
    if parameters.get("temperature") is not None:
        options["temperature"] = float(parameters["temperature"])
    if parameters.get("top_p") is not None:
        options["top_p"] = float(parameters["top_p"])
    if parameters.get("max_new_tokens") is not None:
        options["num_predict"] = int(parameters["max_new_tokens"])
    # Deployment descriptor (services.llm.custom.num_ctx) arrives per request;
    # the gateway env is the standalone fallback.
    request_num_ctx = int(parameters.get("num_ctx") or 0)
    if request_num_ctx > 0:
        options["num_ctx"] = request_num_ctx
    elif GATEWAY_NUM_CTX > 0:
        options["num_ctx"] = GATEWAY_NUM_CTX
    return {
        "model": _requested_model(payload),
        "messages": messages,
        "stream": stream,
        "keep_alive": GATEWAY_KEEP_ALIVE,
        "think": GATEWAY_THINK,
        "options": options,
    }


def _usage(chunk: Dict[str, Any]) -> Dict[str, int]:
    prompt = int(chunk.get("prompt_eval_count") or 0)
    completion = int(chunk.get("eval_count") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            version = (await client.get(f"{OLLAMA_BASE_URL}/api/version")).json()
        except Exception as exc:
            return {"ok": False, "model": GATEWAY_MODEL, "ollama": str(exc)}
    return {"ok": True, "model": GATEWAY_MODEL, "ollama": version}


@app.post("/generate")
async def generate(request: Request):
    _require_auth(request)
    payload = await request.json()
    parameters = payload.get("parameters") or {}
    if parameters.get("stream"):
        return StreamingResponse(
            _stream(payload, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return await _invoke(payload)


async def _invoke(payload: Dict[str, Any]) -> JSONResponse:
    body = _to_ollama_request(payload, stream=False)
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=body)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"ollama: {resp.text[:500]}")
        data = resp.json()
    text = ((data.get("message") or {}).get("content")) or ""
    return JSONResponse({
        "id": f"gw-{uuid.uuid4()}",
        "response": text,
        "model": _requested_model(payload),
        "usage": _usage(data),
    })


class _ClientGone(Exception):
    """The caller dropped the connection — abandon the upstream call."""


async def _lines_watching_disconnect(resp, request: Request) -> AsyncIterator[str]:
    """Yield Ollama's stream lines, aborting when the client disconnects.

    During prompt evaluation Ollama sends nothing for minutes, so a plain
    `async for` would only discover a vanished client at the first token —
    the orphaned request keeps burning the machine. Poll the client
    connection while waiting; raising here unwinds the httpx stream context,
    which cancels the generation inside Ollama.
    """
    it = resp.aiter_lines().__aiter__()
    while True:
        nxt = asyncio.ensure_future(it.__anext__())
        while not nxt.done():
            await asyncio.wait({nxt}, timeout=1.0)
            if not nxt.done() and await request.is_disconnected():
                nxt.cancel()
                try:
                    await nxt
                except (asyncio.CancelledError, StopAsyncIteration, Exception):
                    pass
                raise _ClientGone()
        try:
            yield nxt.result()
        except StopAsyncIteration:
            return


async def _stream(payload: Dict[str, Any], request: Request) -> AsyncIterator[str]:
    body = _to_ollama_request(payload, stream=True)
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=body) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", errors="ignore")[:500]
                    yield f'data: {json.dumps({"error": f"ollama: {detail}"})}\n\n'
                    yield "data: [DONE]\n\n"
                    return
                async for line in _lines_watching_disconnect(resp, request):
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    piece = ((chunk.get("message") or {}).get("content")) or ""
                    if piece:
                        yield f'data: {json.dumps({"delta": piece})}\n\n'
                    if chunk.get("done"):
                        usage = _usage(chunk)
                        break
    except _ClientGone:
        logger.info("[models_gateway] client disconnected — upstream generation cancelled")
        return
    except Exception as exc:
        logger.warning("[models_gateway] stream failed: %s", exc, exc_info=True)
        yield f'data: {json.dumps({"error": str(exc)})}\n\n'
    final = {"delta": "", "final": True, "usage": usage, "model": _requested_model(payload)}
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"
