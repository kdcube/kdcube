# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Minimal standalone test for the locally served model path (provider "custom"):

    ConfigRequest(custom_model_endpoint=...) -> ModelRouter -> CustomModelClient
        -> models gateway (apps/models_gateway) -> Ollama

Run/debug from IntelliJ like the other examples in this folder: the `cb` venv
interpreter, working directory = this folder (so .env is found), source root
app/ai-app/src/kdcube-ai-app on PYTHONPATH.

Three stages, each isolating one link of the chain:

  1. gateway  — raw HTTP+SSE against the gateway, no SDK at all.
                Fails => gateway or Ollama is the problem.
  2. client   — ModelService.get_client(role) with provider "custom" and
                raw astream chunks printed verbatim.
                Fails (stage 1 fine) => CustomModelClient <-> gateway protocol.
  3. channels — workspace_streamer_v3.stream_with_channels with the react
                channel protocol. This is the exact react-v3 brain seam.
                No channel output (stage 2 fine) => the model does not follow
                the <channel:...> protocol; the platform streams fine but the
                UI has nothing recognized to show.

Config comes from .env in this folder (standalone idiom):

    CUSTOM_MODEL_ENDPOINT=http://localhost:11500/generate
    CUSTOM_MODEL_NAME=qwen3.6:35b
    CUSTOM_MODEL_API_KEY=            # only when the gateway sets GATEWAY_API_KEY

Note the host: outside docker it is localhost; inside the chat-proc container
the same gateway is http://host.docker.internal:11500/generate.
"""

from __future__ import annotations

import asyncio
import json
import os

import aiohttp

from kdcube_ai_app.apps.chat.sdk.streaming.workspace_streamer_v3 import (
    ChannelSpec,
    stream_with_channels,
)
from kdcube_ai_app.infra.service_hub.inventory import (
    ConfigRequest,
    ModelServiceBase,
    create_cached_human_message,
    create_cached_system_message,
    create_workflow_config,
)

ROLE = "custom-model-test"

ENDPOINT = os.getenv("CUSTOM_MODEL_ENDPOINT", "http://localhost:11500/generate")
MODEL_NAME = os.getenv("CUSTOM_MODEL_NAME", "qwen3.6:35b")
API_KEY = os.getenv("CUSTOM_MODEL_API_KEY", "")

PROMPT = "Name three prime numbers and say why they are prime, briefly."


def configure_env() -> ModelServiceBase:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv())
    req = ConfigRequest(
        custom_model_endpoint=ENDPOINT,
        custom_model_api_key=API_KEY or None,
        custom_model_name=MODEL_NAME,
        role_models={
            ROLE: {"provider": "custom", "model": MODEL_NAME},
        },
    )
    return ModelServiceBase(create_workflow_config(req))


# ---------------------------------------------------------------- stage 1

async def stage_1_gateway_raw() -> None:
    """Speak the custom protocol to the gateway directly — no SDK."""
    print("\n" + "=" * 60)
    print(f"STAGE 1: raw gateway probe — POST {ENDPOINT}")
    print("=" * 60)

    payload = {
        "inputs": [{"role": "user", "content": PROMPT}],
        "parameters": {"stream": True, "max_new_tokens": 200, "temperature": 0.3},
    }
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    deltas = 0
    async with aiohttp.ClientSession() as http:
        async with http.post(ENDPOINT, json=payload, headers=headers) as resp:
            print(f"[http] status={resp.status}")
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    print("\n[sse] [DONE]")
                    break
                event = json.loads(data)
                if "delta" in event:
                    deltas += 1
                    print(event["delta"], end="", flush=True)
                else:
                    print(f"\n[sse] {event}")
    print(f"[stage 1] OK — {deltas} delta events")


# ---------------------------------------------------------------- stage 2

async def stage_2_client_astream(ms: ModelServiceBase) -> None:
    """The platform client, chunks printed verbatim — no channel parsing."""
    print("\n" + "=" * 60)
    print(f"STAGE 2: CustomModelClient.astream — role={ROLE}")
    print("=" * 60)

    client = ms.get_client(ROLE)
    print(f"[client] {type(client).__name__} endpoint={getattr(client, 'endpoint', '?')}")

    msgs = [
        create_cached_system_message("You are concise."),
        create_cached_human_message(PROMPT),
    ]
    chunks = 0
    # client.astream merges kwargs verbatim into the gateway "parameters"
    # object — the protocol name is max_new_tokens (svc.stream_model_text
    # does this translation for you; here we speak to the client directly).
    async for chunk in client.astream(msgs, max_new_tokens=200, temperature=0.3):
        chunks += 1
        if isinstance(chunk, dict) and "delta" in chunk:
            print(chunk["delta"], end="", flush=True)
        else:
            print(f"\n[chunk] {chunk}")
    print(f"\n[stage 2] OK — {chunks} chunks")


# ---------------------------------------------------------------- stage 3

async def stage_3_react_channels(ms: ModelServiceBase) -> None:
    """The react-v3 seam: channel-tagged streaming through workspace_streamer_v3."""
    print("\n" + "=" * 60)
    print(f"STAGE 3: workspace_streamer_v3.stream_with_channels — role={ROLE}")
    print("=" * 60)

    system_msg = create_cached_system_message([
        {
            "type": "text",
            "text": (
                "You are a gate agent. Output ONLY channel-tagged content.\n\n"
                "Required output protocol:\n"
                "<channel:thinking>...private reasoning...</channel:thinking>\n"
                "<channel:output>{\"conversation_title\": \"...\"}</channel:output>\n\n"
                "The conversation_title must be <= 6 words."
            ),
        }
    ])
    user_msg = create_cached_human_message(PROMPT)

    channels = [
        ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="output", format="json", replace_citations=False, emit_marker="answer"),
    ]

    async def emit(**kwargs):
        print(
            f"[delta] marker={kwargs.get('marker')} channel={kwargs.get('channel')}"
            f" :: {kwargs.get('text')}"
        )

    results, meta = await stream_with_channels(
        ms,
        messages=[system_msg, user_msg],
        role=ROLE,
        channels=channels,
        emit=emit,
        agent=ROLE,
        artifact_name="gate.output",
        max_tokens=600,
        temperature=0.2,
        return_full_raw=True,
    )

    print("\n--- CHANNEL RESULTS ---")
    for name, res in results.items():
        print(f"{name} :: error={res.error!r}")
        print((res.raw or "").strip() or "(empty)")
    print("\n--- FULL RAW (what the model actually produced) ---")
    print(meta.get("raw", "") or "(empty)")
    if meta.get("service_error"):
        print("\n--- SERVICE ERROR ---")
        print(meta["service_error"])
    print("[stage 3] done")


# ---------------------------------------------------------------- stage 4

async def stage_4_react_decision(ms: ModelServiceBase) -> None:
    """The real react-v3 decision call: react_decision_stream_v2 builds the
    same system instruction the react runtime builds (protocol + workspace
    guide + tool catalog + admin customization) and streams the same
    thinking/action/code/summary channels.

    REACT_PROMPT_PAD_TOKENS (env, default 0) pads the instruction with
    reference-note text to simulate a real deployment's prompt size (the
    workspace app's decision prompt runs ~60K tokens). Use it to verify the
    serving window: if the prompt exceeds the gateway's GATEWAY_NUM_CTX (or
    Ollama's default), Ollama TRUNCATES FROM THE FRONT — the protocol
    instruction is lost and the model answers as plain text. Watch the
    Ollama server log for `msg="truncating input prompt"`.
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import (
        build_decision_system_text,
        react_decision_stream_v2,
    )

    pad_tokens = int(os.getenv("REACT_PROMPT_PAD_TOKENS", "0") or 0)
    additional = None
    if pad_tokens:
        para = (
            "Reference note {i}: deployment conventions the agent must keep in "
            "mind — naming, file layout, citation style, and channel hygiene "
            "as configured by the administrator for this workspace. "
        )
        reps = max(1, pad_tokens // 30)
        additional = "\n".join(para.format(i=i) for i in range(reps))

    build_kwargs = dict(
        adapters=[],
        workspace_implementation="custom",
        additional_instructions=additional,
        multi_action_mode="on",
    )
    system_text = build_decision_system_text(**build_kwargs, skill_consumer=ROLE)
    est_tokens = len(system_text) // 4

    print("\n" + "=" * 60)
    print(f"STAGE 4: react_decision_stream_v2 — role={ROLE}")
    print(f"system instruction: {len(system_text)} chars ≈ {est_tokens} tokens"
          f" (pad={pad_tokens})")
    print("=" * 60)

    out = await react_decision_stream_v2(
        ms,
        agent_name=ROLE,
        user_blocks=[{"type": "text", "text": "hey"}],
        max_tokens=1500,
        **build_kwargs,
    )

    print("\n--- DECISION RESULT ---")
    full_raw = out.get("raw") or ""
    log = out.get("log") or {}
    print(f"thinking :: {(out.get('internal_thinking') or '').strip()!r}")
    print(f"agent_response :: {out.get('agent_response')!r}")
    print(f"error :: {log.get('error')!r} protocol_shape_error :: {log.get('protocol_shape_error')!r}")
    print(f"raw length :: {len(full_raw)}")
    followed_protocol = "<channel:" in (full_raw or "")
    print(f"channel protocol followed :: {followed_protocol}")
    if not followed_protocol and full_raw:
        print("!! plain text came back — the serving window truncated the "
              "instruction (check the Ollama server log) or the model "
              "ignored the protocol.")
    print("[stage 4] done")


async def main():
    ms = configure_env()
    await stage_1_gateway_raw()
    await stage_2_client_astream(ms)
    await stage_3_react_channels(ms)
    await stage_4_react_decision(ms)


if __name__ == "__main__":
    asyncio.run(main())
