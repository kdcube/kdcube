# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── knowledge_base/seed_data.py ──
# Initial technology profiles and comparison baselines.
# These are loaded into the knowledge base on first bundle init.

from __future__ import annotations

from typing import List, Dict, Any

INITIAL_TECHNOLOGIES: List[Dict[str, Any]] = [
    {
        "name": "Infrastructure sandbox",
        "category": "Infrastructure sandbox",
        "examples": "Docker, gVisor, Firecracker",
        "description": (
            "Container and microVM technologies that isolate compute environments. "
            "Provide process-level, filesystem-level, and network-level isolation."
        ),
        "what_it_does": "Isolates compute environment",
        "what_it_does_not": (
            "Does not know what the agent intends to do; no semantic constraints"
        ),
        "homepage": "https://www.docker.com/",
        "tags": ["infrastructure", "isolation", "containers", "security"],
    },
    {
        "name": "Guardrails wrapper",
        "category": "Guardrails wrapper",
        "examples": "NeMo Guardrails, Guardrails AI, Rebuff",
        "description": (
            "Libraries that filter, validate, or constrain LLM output text. "
            "Apply rules and checks after the model generates a response."
        ),
        "what_it_does": "Filters LLM output text",
        "what_it_does_not": (
            "Does not intercept tool calls or API actions; fires after generation"
        ),
        "homepage": "https://github.com/NVIDIA/NeMo-Guardrails",
        "tags": ["guardrails", "filtering", "safety", "output-validation"],
    },
    {
        "name": "Observability tool",
        "category": "Observability tool",
        "examples": "LangSmith, Helicone, Langfuse, Arize Phoenix",
        "description": (
            "Platforms that log, trace, and visualize agent behavior. "
            "Provide dashboards, replay, and debugging for LLM applications."
        ),
        "what_it_does": "Logs and traces agent behavior",
        "what_it_does_not": (
            "Read-only; logs damage after it occurs; does not block"
        ),
        "homepage": "https://smith.langchain.com/",
        "tags": ["observability", "tracing", "logging", "monitoring"],
    },
    {
        "name": "Agent framework",
        "category": "Agent framework",
        "examples": "LangGraph, LangChain, CrewAI, AutoGen",
        "description": (
            "Orchestration frameworks for building and running AI agents. "
            "Define agent logic, tool calling, and multi-step workflows."
        ),
        "what_it_does": "Orchestrates agent logic and tool calling",
        "what_it_does_not": (
            "Provides no enforcement layer; delegates execution control to the agent"
        ),
        "homepage": "https://www.langchain.com/langgraph",
        "tags": ["framework", "orchestration", "agents", "tool-calling"],
    },
]

INITIAL_COMPARISONS: List[Dict[str, Any]] = [
    {
        "technology": "Infrastructure sandbox",
        "what_it_does": "Isolates compute environment",
        "what_it_does_not": (
            "Does not know what the agent intends to do; no semantic constraints"
        ),
        "kdcube_advantage": (
            "KDCube understands agent intent at the semantic level. "
            "While sandboxes isolate the environment, KDCube intercepts and evaluates "
            "every tool call before execution, enforcing business-level constraints "
            "that containers cannot express."
        ),
        "detailed_comparison": {
            "enforcement_layer": {"sandbox": "Process/OS level", "kdcube": "Semantic/API level"},
            "intent_awareness": {"sandbox": "None", "kdcube": "Full tool-call inspection"},
            "budget_control": {"sandbox": "Resource limits (CPU/RAM)", "kdcube": "Token budgets, API call limits, cost caps"},
            "tenant_isolation": {"sandbox": "Namespace isolation", "kdcube": "Semantic tenant boundaries + namespace isolation"},
        },
    },
    {
        "technology": "Guardrails wrapper",
        "what_it_does": "Filters LLM output text",
        "what_it_does_not": (
            "Does not intercept tool calls or API actions; fires after generation"
        ),
        "kdcube_advantage": (
            "KDCube operates at the tool-call level, not just the text level. "
            "Guardrails validate what the model says; KDCube controls what the model does. "
            "This pre-execution enforcement prevents harmful actions before they happen."
        ),
        "detailed_comparison": {
            "interception_point": {"guardrails": "Post-generation text", "kdcube": "Pre-execution tool calls"},
            "action_control": {"guardrails": "Text filtering only", "kdcube": "Full action interception + enforcement"},
            "timing": {"guardrails": "After generation", "kdcube": "Before execution"},
            "scope": {"guardrails": "LLM output text", "kdcube": "Tool calls, API actions, budgets, workflows"},
        },
    },
    {
        "technology": "Observability tool",
        "what_it_does": "Logs and traces agent behavior",
        "what_it_does_not": (
            "Read-only; logs damage after it occurs; does not block"
        ),
        "kdcube_advantage": (
            "Observability tools tell you what happened; KDCube prevents what shouldn't happen. "
            "While logging is essential for debugging, KDCube adds an active enforcement layer "
            "that blocks unauthorized actions in real time."
        ),
        "detailed_comparison": {
            "mode": {"observability": "Passive (read-only)", "kdcube": "Active (read-write enforcement)"},
            "timing": {"observability": "Post-hoc analysis", "kdcube": "Real-time pre-execution"},
            "prevention": {"observability": "None (logs damage after)", "kdcube": "Blocks before damage occurs"},
            "complementary": {"observability": "Yes — use with KDCube", "kdcube": "Yes — enhanced with observability"},
        },
    },
    {
        "technology": "Agent framework",
        "what_it_does": "Orchestrates agent logic and tool calling",
        "what_it_does_not": (
            "Provides no enforcement layer; delegates execution control to the agent"
        ),
        "kdcube_advantage": (
            "Agent frameworks build agents; KDCube controls them. "
            "Frameworks like LangGraph define the workflow but trust the agent to behave. "
            "KDCube wraps around any framework to enforce budgets, rate limits, "
            "and semantic constraints on every action."
        ),
        "detailed_comparison": {
            "role": {"framework": "Build agent logic", "kdcube": "Enforce agent boundaries"},
            "trust_model": {"framework": "Trusts the agent", "kdcube": "Verifies every action"},
            "integration": {"framework": "Standalone", "kdcube": "Wraps any framework"},
            "enforcement": {"framework": "None built-in", "kdcube": "Budget, rate, tenant, semantic constraints"},
        },
    },
]


def seed_knowledge_base(kb) -> None:
    """Populate the knowledge base with initial technologies and comparisons."""
    for tech in INITIAL_TECHNOLOGIES:
        existing = kb.get_technology(tech["name"])
        if not existing:
            kb.upsert_technology(tech)

    for comp in INITIAL_COMPARISONS:
        tech_name = comp["technology"]
        if not kb.is_cached_today(tech_name):
            kb.save_comparison(tech_name, comp)
