"""Dependency container wiring config -> LLM -> stores -> subagent.

One place that constructs the collaborators the graph nodes close over, so
graph.py and subagent.py stay free of construction logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config, get_config
from .knowledge import KnowledgeBase
from .llm import LLMClient, get_llm
from .memory import SemanticMemory


@dataclass
class Deps:
    config: Config
    llm: LLMClient
    memory: SemanticMemory
    knowledge: KnowledgeBase


def build_deps(config: Config | None = None) -> Deps:
    config = config or get_config()
    llm = get_llm(config)
    return Deps(
        config=config,
        llm=llm,
        memory=SemanticMemory(config, llm),
        knowledge=KnowledgeBase(config, llm),
    )
