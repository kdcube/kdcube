"""Standalone LangGraph research-assistant prototype.

A single-machine agent with pgvector-backed memory + knowledge base, a subagent
sub-graph, and Postgres-checkpointed conversation state. Framework-independent:
no coupling to any hosting platform.
"""

__all__ = ["config", "deps", "graph", "knowledge", "memory", "subagent"]
