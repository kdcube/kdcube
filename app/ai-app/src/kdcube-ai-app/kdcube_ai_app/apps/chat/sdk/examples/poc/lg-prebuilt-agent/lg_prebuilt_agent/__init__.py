"""Standalone LangGraph *prebuilt* ReAct agent prototype.

A single-machine tool-using assistant built on the agent everyone builds:
``from langgraph.prebuilt import create_react_agent``. It binds a few plain
LangChain tools (a calculator, a unit converter, a tiny local knowledge search),
streams the final answer token-by-token, keeps short-term memory across turns
with a Postgres checkpointer, and bounds the model's context each turn with a
``pre_model_hook`` that trims the message view.

Framework-independent: no coupling to any hosting platform. This is the kind of
"before" you would later wrap into a managed runtime.
"""

__all__ = ["config", "tools", "llm", "context", "agent", "cli"]
