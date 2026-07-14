"""Plain LangChain tools the prebuilt ReAct agent binds.

These are ordinary ``@tool`` functions — "bring your own tools". They are
self-contained and dependency-free so the prototype runs on one machine and
degrades offline. When this agent is later hosted, these plain tools keep working
unchanged (they are external to the host and, running no accounted model calls,
are unmetered by design).

Three realistic, self-contained tools:
  - ``calc``        — evaluate a basic arithmetic expression (safe AST eval)
  - ``unit_convert``— convert between a few common units (length + temperature)
  - ``kb_search``   — keyword search over a small seeded local document list
"""
from __future__ import annotations

import ast
import operator as _op
from typing import List

from langchain_core.tools import tool


# ── calculator ─────────────────────────────────────────────────────────────

_ALLOWED_OPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.Pow: _op.pow,
    ast.Mod: _op.mod,
    ast.USub: _op.neg,
    ast.UAdd: _op.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


@tool
def calc(expression: str) -> str:
    """Evaluate a basic arithmetic expression, e.g. "2 + 3 * 4" or "(6 - 1) / 2".

    Supports + - * / % ** and parentheses over numbers. Returns the numeric
    result as a string."""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_safe_eval(tree.body))
    except Exception as e:  # noqa: BLE001 - tool errors return a message, never crash
        return f"could not evaluate {expression!r}: {e}"


# ── unit converter ─────────────────────────────────────────────────────────

_LENGTH_TO_M = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "ft": 0.3048, "in": 0.0254}


@tool
def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    """Convert a value between units. Supported: length (m, km, cm, mm, mi, ft,
    in) and temperature (c, f, k). Example: unit_convert(100, "km", "mi")."""
    f, t = from_unit.strip().lower(), to_unit.strip().lower()
    temps = {"c", "f", "k"}
    try:
        if f in temps and t in temps:
            celsius = {"c": value, "f": (value - 32) / 1.8, "k": value - 273.15}[f]
            out = {"c": celsius, "f": celsius * 1.8 + 32, "k": celsius + 273.15}[t]
            return f"{value} {from_unit} = {round(out, 4)} {to_unit}"
        if f in _LENGTH_TO_M and t in _LENGTH_TO_M:
            out = value * _LENGTH_TO_M[f] / _LENGTH_TO_M[t]
            return f"{value} {from_unit} = {round(out, 6)} {to_unit}"
    except Exception as e:  # noqa: BLE001
        return f"conversion failed: {e}"
    return f"unsupported units {from_unit!r} -> {to_unit!r}"


# ── local knowledge search ─────────────────────────────────────────────────

# A tiny seeded document list, so a fresh install can answer immediately without
# any external service or database.
_DOCS: List[dict] = [
    {"title": "LangGraph checkpointer",
     "text": "LangGraph persists conversation state through a checkpointer keyed by thread_id. "
             "A Postgres checkpointer (AsyncPostgresSaver) survives process restarts; MemorySaver "
             "keeps state only in-process."},
    {"title": "create_agent",
     "text": "langchain.agents.create_agent builds the standard ReAct loop: a 'model' node "
             "that calls the model and a 'tools' node that runs tool calls. The model node loops until "
             "the model returns a message with no tool calls — that final message is the answer."},
    {"title": "SummarizationMiddleware",
     "text": "create_agent accepts middleware. SummarizationMiddleware runs in its own before_model "
             "node and folds older turns into a summary once the conversation passes a token trigger, "
             "keeping recent messages verbatim, so context and cost stay controlled as it grows."},
    {"title": "tool binding",
     "text": "Tools are plain @tool functions bound into the agent. The model decides when to call them; "
             "the tools node executes the calls and feeds results back as ToolMessages."},
]


def _score(query: str, doc: dict) -> int:
    q = query.lower()
    terms = [w for w in q.replace("?", " ").split() if len(w) > 2]
    hay = (doc["title"] + " " + doc["text"]).lower()
    return sum(hay.count(t) for t in terms)


@tool
def kb_search(query: str) -> str:
    """Search the local knowledge base for documents relevant to a query and
    return the top matches (title + text). Use this to ground answers about
    LangGraph, the ReAct loop, tools, and context management."""
    ranked = sorted(_DOCS, key=lambda d: _score(query, d), reverse=True)
    hits = [d for d in ranked if _score(query, d) > 0][:3]
    if not hits:
        return "(no local documents matched)"
    return "\n\n".join(f"[{d['title']}] {d['text']}" for d in hits)


# The plain tools this agent can bind, keyed by tool name (each object's `.name`).
# A registry (not a fixed list) so the host can bind EXACTLY the subset an admin
# declares in config and a user leaves enabled — the per-agent tool picker's ceiling
# (see platform/tool_pick.py). `run_python` is not here: it is built on demand
# (build_run_python_tool) only when the code-exec connection is declared.
def plain_tool_registry() -> dict:
    """name -> LangChain @tool for the vendored plain tools (calc, unit_convert,
    kb_search). A fresh dict per call so callers may bind independent instances."""
    return {"calc": calc, "unit_convert": unit_convert, "kb_search": kb_search}


def build_plain_tools(*, include_code_exec: bool = False) -> list:
    """The full plain-tool set (all three vendored tools), optionally plus
    ``run_python``. A single place so the graph builder and any host stay free of
    tool-construction detail. Used as the OFFLINE / no-picker default; the hosted
    path narrows to the admin-declared, user-enabled subset via
    platform/tool_pick.py.

    ``include_code_exec`` appends the platform ``run_python`` tool — a
    model-callable code-execution tool whose produced files are hosted into
    conversation storage like a user attachment. Additive, and inert unless a
    code-exec scope is active (see platform/code_exec)."""
    tools = list(plain_tool_registry().values())
    if include_code_exec:
        # Package-relative, lazy: the standalone plain tools stay dependency-free
        # and this import only happens when the host enables code execution.
        from ...platform.code_exec_tool import build_run_python_tool

        tools.append(build_run_python_tool())
    return tools
