"""A subagent implemented as a nested StateGraph.

The main graph delegates a scoped sub-question here. This subagent runs its own
retrieve -> synthesize steps against the shared knowledge base and returns a
compact finding. It is a genuine nested graph, not a helper function — so the
"subagents" story is real and each layer keeps its own state.
"""
from __future__ import annotations

from typing import List, TypedDict

from langgraph.graph import END, START, StateGraph

from .deps import Deps
from .knowledge import KBHit


class SubState(TypedDict):
    sub_question: str
    kb_hits: List[KBHit]
    finding: str


def build_subagent(deps: Deps):
    """Compile and return the subagent sub-graph."""

    def research(state: SubState) -> dict:
        try:
            hits = deps.knowledge.search(state["sub_question"], k=3)
        except Exception as e:  # noqa: BLE001 - degrade to empty context, don't crash
            import sys
            print(f"  [subagent] KB unavailable: {e}", file=sys.stderr)
            hits = []
        return {"kb_hits": hits}

    async def synthesize(state: SubState) -> dict:
        hits = state.get("kb_hits", [])
        context = "\n\n".join(f"- {h.title}: {h.text}" for h in hits) or "(no documents found)"
        system = (
            "You are a focused research subagent. Answer the single sub-question "
            "using only the provided sources. Be concise (2-3 sentences)."
        )
        user = f"Sub-question: {state['sub_question']}\n\nSources:\n{context}"
        finding = await deps.llm.chat(system, user)
        return {"finding": finding}

    g = StateGraph(SubState)
    g.add_node("research", research)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "research")
    g.add_edge("research", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()
