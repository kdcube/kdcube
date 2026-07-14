"""Build the prebuilt ReAct agent.

``build_agent`` returns the compiled graph from
``langgraph.prebuilt.create_react_agent`` — the standard ReAct loop everyone
builds:

    START -> agent -> (tools -> agent)* -> END

The ``agent`` node calls the model; if the model returns tool calls, the ``tools``
node runs them and control loops back to ``agent``. The loop ends when the model
returns a message with NO tool calls — that final message is the answer.

Everything is injectable so the same graph builder serves the standalone CLI and,
later, a hosting platform: pass your own ``model`` (e.g. an accounted one),
``tools`` (plain or MCP-loaded), and ``checkpointer`` without touching this file.
"""
from __future__ import annotations

from typing import Any, List, Optional

from langgraph.prebuilt import create_react_agent

from .config import Config, get_config
from .context import build_pre_model_hook
from .llm import build_chat_model
from .tools import build_plain_tools

SYSTEM_PROMPT = (
    "You are a concise, helpful assistant. Use the available tools when they help "
    "answer accurately — the calculator for arithmetic, the unit converter for "
    "conversions, and the knowledge search for questions about LangGraph and this "
    "agent's own design. When you have enough to answer, answer directly and cite "
    "any knowledge-base titles in brackets."
)

# The prebuilt ReAct graph's node names (stable across the create_react loop):
# 'agent' is the LOOPING model node, 'tools' runs tool calls.
AGENT_NODE = "agent"
TOOLS_NODE = "tools"


def build_agent(
    config: Optional[Config] = None,
    *,
    model: Any = None,
    tools: Optional[List[Any]] = None,
    checkpointer: Any = None,
):
    """Build and compile the prebuilt ReAct agent.

    - ``model``       — a LangChain chat model; defaults to the standalone model
                        (real online, deterministic stub offline).
    - ``tools``       — the tool list to bind; defaults to the plain local tools.
    - ``checkpointer``— short-term memory across turns; ``None`` is valid (the
                        structure can be inspected without one).
    """
    config = config or get_config()
    model = model if model is not None else build_chat_model(config)
    tools = tools if tools is not None else build_plain_tools()

    return create_react_agent(
        model,
        tools,
        checkpointer=checkpointer,
        prompt=SYSTEM_PROMPT,
        # Bound the model's per-turn context view (the checkpointer keeps the full
        # history; the hook trims what the model sees). See context.py.
        pre_model_hook=build_pre_model_hook(config),
    )
