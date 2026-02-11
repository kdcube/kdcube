# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/message_utils.py
# Shared multimodal normalization for Anthropic/OpenAI/Gemini

from typing import Any, Optional, List
from langchain_core.messages import BaseMessage


def extract_message_blocks(msg: BaseMessage) -> Optional[list]:
    addkw = getattr(msg, "additional_kwargs", {}) or {}
    blocks = addkw.get("message_blocks")
    if not blocks and isinstance(getattr(msg, "content", None), list):
        blocks = msg.content
    return blocks


def normalize_blocks(blocks: list, default_cache_ctrl: dict | None = None) -> list:
    """
    Normalize blocks into a common shape:
      - text: {"type": "text", "text": "..."}
      - image/document: {"type": "...", "source": {"type": "base64", "media_type": "...", "data": "..."}}
    """
    norm: list[dict] = []

    for b in blocks or []:
        if not isinstance(b, dict):
            norm.append({"type": "text", "text": str(b)})
            continue

        btype = b.get("type", "text")
        if btype == "text":
            text = b.get("text") or b.get("content", "")
            blk = {"type": "text", "text": str(text)}
        elif btype in ("image", "document"):
            src = b.get("source") or {}
            media_type = src.get("media_type") or b.get("media_type")
            data = src.get("data") or b.get("data")
            default_media = "image/png" if btype == "image" else "application/pdf"
            blk = {
                "type": btype,
                "source": {
                    "type": "base64",
                    "media_type": media_type or default_media,
                    "data": data,
                },
            }
        else:
            content = b.get("content")
            if isinstance(content, list):
                norm.extend(normalize_blocks(content, default_cache_ctrl))
                continue
            text = b.get("text")
            if text is None and isinstance(content, str):
                text = content
            if text is not None:
                blk = {"type": "text", "text": str(text)}
            else:
                norm.append(b)
                continue

        if "cache_control" in b:
            blk["cache_control"] = b["cache_control"]
        elif default_cache_ctrl:
            blk["cache_control"] = default_cache_ctrl

        norm.append(blk)

    return norm


def blocks_to_text(blocks: list) -> str:
    norm = normalize_blocks(blocks)
    return "\n\n".join(
        b.get("text", "")
        for b in norm
        if isinstance(b, dict) and b.get("type") == "text"
    )


def blocks_to_openai_content(blocks: list) -> list[dict]:
    norm = normalize_blocks(blocks)
    content: list[dict] = []

    for b in norm:
        btype = b.get("type", "text")
        if btype == "text":
            content.append({"type": "text", "text": b.get("text", "")})
            continue

        if btype == "image":
            src = b.get("source") or {}
            data = src.get("data")
            media_type = src.get("media_type") or "image/png"
            if data:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            else:
                content.append({"type": "text", "text": f"[image omitted: {media_type}]"})
            continue

        if btype == "document":
            src = b.get("source") or {}
            media_type = src.get("media_type") or "application/pdf"
            content.append({"type": "text", "text": f"[document omitted: {media_type}]"})
            continue

        content.append({"type": "text", "text": str(b)})

    return content


def blocks_to_gemini_parts(blocks: list) -> list[dict]:
    norm = normalize_blocks(blocks)
    parts: list[dict] = []

    for b in norm:
        btype = b.get("type", "text")
        if btype == "text":
            parts.append({"text": b.get("text", "")})
            continue

        if btype == "image":
            src = b.get("source") or {}
            data = src.get("data")
            media_type = src.get("media_type") or "image/png"
            if data:
                parts.append({"inline_data": {"mime_type": media_type, "data": data}})
            else:
                parts.append({"text": f"[image omitted: {media_type}]"})
            continue

        if btype == "document":
            src = b.get("source") or {}
            media_type = src.get("media_type") or "application/pdf"
            parts.append({"text": f"[document omitted: {media_type}]"})
            continue

        parts.append({"text": str(b)})

    return parts

def normalize_tool_definition(tools: List[dict]) -> dict:
    """
    Convert provider-agnostic tool definitions to normalized format.

    Input format:
    {
        "name": "web_search",
        "description": "Search the web",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    }
    """
    normalized = {}
    for tool in tools or []:
        normalized[tool["name"]] = {
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or tool.get("input_schema", {}),
        }
    return normalized


def tools_to_anthropic_format(tools: List[dict]) -> List[dict]:
    """Convert to Anthropic tool format."""
    anthropic_tools = []
    for tool in tools or []:
        anthropic_tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters") or tool.get("input_schema", {})
        })
    return anthropic_tools


def tools_to_openai_format(tools: List[dict]) -> List[dict]:
    """Convert to OpenAI tool format."""
    openai_tools = []
    for tool in tools or []:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or tool.get("input_schema", {})
            }
        })
    return openai_tools


def tools_to_gemini_format(tools: List[dict]) -> List[dict]:
    """Convert to Gemini tool format."""
    # Gemini uses function_declarations
    gemini_tools = []
    for tool in tools or []:
        gemini_tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or tool.get("input_schema", {})
        })
    return gemini_tools