# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/viz/logging_helpers.py
import json
import logging
from textwrap import shorten

log = logging.getLogger("agents")

def _to_str(v, maxlen=20000):
    if v is None: return ""
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, indent=2)
    else:
        s = str(v)
    return shorten(s, width=maxlen, placeholder="…")

def _log_agent_packet_text(agent_name: str, phase: str, internal: str, user: str, resp: str) -> None:
    border = "=" * 80
    log.info(
        "\n%s\n[agent.packet] agent=%s phase=%s\n%s\nInternal thinking:\n%s\n\nUser-facing:\n%s\n\nStructured response:\n%s\n%s",
        border,
        agent_name,
        phase,
        border,
        internal,
        user,
        resp,
        border,
    )

def log_agent_packet(agent_name: str, phase: str, packet: dict):
    """
    Expected packet keys:
      - internal_thinking (markdown)
      - user_thinking     (markdown)
      - agent_response    (structured)
    """
    internal = _to_str(packet.get("internal_thinking", ""))
    user     = _to_str(packet.get("user_thinking", ""))
    resp     = _to_str(packet.get("agent_response", {}))
    _log_agent_packet_text(agent_name, phase, internal, user, resp)

    try:
        # Pretty table with rich, if available
        from rich.table import Table
        from rich.console import Console
        from rich.panel import Panel
        console = Console(width=140, soft_wrap=True)

        tbl = Table(show_header=True, header_style="bold cyan")
        tbl.add_column("Internal thinking", overflow="fold")
        tbl.add_column("User-facing", overflow="fold")
        tbl.add_column("Structured response", overflow="fold")

        tbl.add_row(internal, user, resp)
        console.print(Panel.fit(f"[bold]<{agent_name}:{phase}>[/]", border_style="magenta"))
        console.print(tbl)
    except Exception:
        pass

def log_stream_channels(agent_name: str, phase: str, channels: dict):
    """
    Log raw channel outputs from versatile multi-stream responses.
    channels: {name: raw_text}
    """
    if not isinstance(channels, dict):
        return
    payload = {
        "agent_response": channels
    }
    log_agent_packet(agent_name, phase, payload)


def _reconstruct_raw_channels(channels: dict | None) -> str:
    if not isinstance(channels, dict):
        return ""
    pieces = []
    for name, value in channels.items():
        if isinstance(value, dict):
            text = value.get("text") or ""
        else:
            text = value or ""
        if text is None:
            text = ""
        pieces.append(f"<channel:{name}>{text}</channel:{name}>")
    return "\n".join(pieces)


def log_raw_channel_output(
    agent_name: str,
    phase: str,
    raw_text: str | None,
    *,
    channels: dict | None = None,
) -> None:
    """
    Log the exact raw channel-formatted model output.

    This intentionally does not pretty-print or parse the payload. It is used
    to debug protocol failures where the model emitted duplicate channel
    instances, multiple JSON objects in one channel, markdown fences, or prose
    outside the structured block.
    """
    raw = str(raw_text or "")
    if not raw:
        raw = _reconstruct_raw_channels(channels)
    border = "=" * 80
    log.info(
        "\n%s\n[agent.raw_channels] agent=%s phase=%s len=%s\n%s\n--- raw model output begin ---\n%s\n--- raw model output end ---\n%s",
        border,
        agent_name,
        phase,
        len(raw),
        border,
        raw,
        border,
    )
