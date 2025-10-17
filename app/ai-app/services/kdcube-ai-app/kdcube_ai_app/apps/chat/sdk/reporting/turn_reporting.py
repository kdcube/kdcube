# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/reporting/turn_reporting.py

from typing import Tuple, List, Dict, Any


def _format_ms_table(rows: List[Tuple[str, int]], headers: Tuple[str, str] = ("Step", "Time (ms)")) -> str:
    col1 = max(len(headers[0]), max((len(str(r[0])) for r in rows), default=0))
    col2 = max(len(headers[1]), max((len(str(r[1])) for r in rows), default=0))
    sep = f"+-{'-'*col1}-+-{'-'*col2}-+"
    out = [
        sep,
        f"| {headers[0].ljust(col1)} | {headers[1].rjust(col2)} |",
        sep
    ]
    for a, b in rows:
        out.append(f"| {str(a).ljust(col1)} | {str(b).rjust(col2)} |")
    out.append(sep)
    return "\n".join(out)

def _format_ms_table_markdown(rows: List[Dict[str, int]], headers: Tuple[str, str] = ("Step", "Time (ms)")):
    agg = {}
    order = []
    for t in rows:
        title_i = (t.get("title") or t.get("step") or "").strip() or "(untitled)"
        if title_i not in agg:
            agg[title_i] = 0
            order.append(title_i)
        agg[title_i] += int(t.get("elapsed_ms") or 0)

    lines = ["| Step | Time (ms) |", "|---|---:|"]
    for title_i in order:
        lines.append(f"| {title_i} | {agg[title_i]} |")
    md_table = "\n".join(lines)
    return md_table

# Add these to base_workflow.py or a separate formatting module

def _format_cost_table_markdown(cost_breakdown: List[Dict[str, Any]],
                                total_cost: float,
                                show_detailed: bool = True) -> str:
    """
    Format cost breakdown as markdown table(s).

    Args:
        cost_breakdown: List of cost items with service, provider, model, tokens, costs
        total_cost: Total cost in USD
        show_detailed: If True, show separate tables for LLM and embedding with token details
    """
    if not cost_breakdown:
        return f"**Total Cost:** ${total_cost:.6f} USD\n\n_No usage recorded._"

    # Separate by service type
    llm_items = [item for item in cost_breakdown if item.get("service") == "llm"]
    emb_items = [item for item in cost_breakdown if item.get("service") == "embedding"]

    sections = []

    # Header
    sections.append(f"## 💰 Turn Cost Breakdown\n")
    sections.append(f"**Total:** ${total_cost:.6f} USD\n")

    if llm_items:
        sections.append("\n### 🤖 LLM Usage\n")
        if show_detailed:
            sections.append(_format_llm_detailed_table(llm_items))
        else:
            sections.append(_format_llm_summary_table(llm_items))

    if emb_items:
        sections.append("\n### 📊 Embedding Usage\n")
        sections.append(_format_embedding_table(emb_items))

    return "\n".join(sections)


def _format_llm_detailed_table(llm_items: List[Dict[str, Any]]) -> str:
    """Format detailed LLM cost table with cache type breakdown."""
    lines = [
        "| Provider | Model | Input | Cache 5m | Cache 1h | Cache Read | Output | Cost (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|"
    ]

    for item in llm_items:
        provider = item.get("provider", "unknown")
        model = (item.get("model") or "unknown")[:30]

        input_tok = _format_number(item.get("input_tokens", 0))
        output_tok = _format_number(item.get("output_tokens", 0))

        # Check if detailed cache breakdown exists
        cache_5m = item.get("cache_5m_write_tokens", 0)
        cache_1h = item.get("cache_1h_write_tokens", 0)
        cache_read = item.get("cache_read_tokens", 0)

        if cache_5m > 0 or cache_1h > 0:
            # Anthropic with detailed breakdown
            cache_5m_str = _format_number(cache_5m)
            cache_1h_str = _format_number(cache_1h)
            cache_read_str = _format_number(cache_read)
        else:
            # Legacy format (OpenAI or old Anthropic)
            cache_creation = item.get("cache_creation_tokens", 0)
            cache_5m_str = _format_number(cache_creation) if cache_creation > 0 else "-"
            cache_1h_str = "-"
            cache_read_str = _format_number(cache_read) if cache_read > 0 else "-"

        cost = f"${item.get('cost_usd', 0):.6f}"

        lines.append(
            f"| {provider} | {model} | {input_tok} | {cache_5m_str} | {cache_1h_str} | {cache_read_str} | {output_tok} | {cost} |"
        )

    return "\n".join(lines)


def _format_llm_summary_table(llm_items: List[Dict[str, Any]]) -> str:
    """Format simplified LLM cost table (total tokens + cost)."""
    lines = [
        "| Provider | Model | Total Tokens | Cost (USD) |",
        "|---|---|---:|---:|"
    ]

    for item in llm_items:
        provider = item.get("provider", "unknown")
        model = (item.get("model") or "unknown")[:40]

        total_tokens = (
                item.get("input_tokens", 0) +
                item.get("cache_creation_tokens", 0) +
                item.get("cache_read_tokens", 0) +
                item.get("output_tokens", 0)
        )

        cost = f"${item.get('cost_usd', 0):.6f}"

        lines.append(
            f"| {provider} | {model} | {_format_number(total_tokens)} | {cost} |"
        )

    return "\n".join(lines)


def _format_embedding_table(emb_items: List[Dict[str, Any]]) -> str:
    """Format embedding cost table."""
    lines = [
        "| Provider | Model | Tokens | Cost (USD) |",
        "|---|---|---:|---:|"
    ]

    for item in emb_items:
        provider = item.get("provider", "unknown")
        model = (item.get("model") or "unknown")[:40]
        tokens = _format_number(item.get("tokens", 0))
        cost = f"${item.get('cost_usd', 0):.6f}"

        lines.append(
            f"| {provider} | {model} | {tokens} | {cost} |"
        )

    return "\n".join(lines)


def _format_number(n: int) -> str:
    """Format large numbers with comma separators."""
    return f"{n:,}"


def _format_cost_summary_compact(cost_breakdown: List[Dict[str, Any]],
                                 total_cost: float,
                                 weighted_tokens: int,
                                 total_input_tokens: int,
                                 llm_output_sum: int) -> str:
    """
    Ultra-compact cost summary (one-liner style).
    """
    llm_count = sum(1 for item in cost_breakdown if item.get("service") == "llm")
    emb_count = sum(1 for item in cost_breakdown if item.get("service") == "embedding")

    parts = [
        f"**${total_cost:.6f} USD**",
        f"• {_format_number(total_input_tokens)} in",
        f"{_format_number(llm_output_sum)} out",
        f"({_format_number(weighted_tokens)} weighted)"
    ]

    if llm_count:
        parts.append(f"• {llm_count} LLM call{'s' if llm_count > 1 else ''}")
    if emb_count:
        parts.append(f"• {emb_count} embed call{'s' if emb_count > 1 else ''}")

    return " ".join(parts)

def _format_agent_breakdown_markdown(
        agent_costs: Dict[str, Dict[str, Any]],
        total_cost: float
) -> str:
    """
    Format agent-level cost breakdown as markdown.

    Shows:
    - Summary table: agent, total cost, % of turn
    - Detailed per-agent breakdown
    """
    if not agent_costs:
        return ""

    sections = []

    # Header
    sections.append("### 👥 Cost by Agent\n")

    # Summary table
    lines = [
        "| Agent | LLM Tokens | Cost (USD) | % of Turn |",
        "|---|---:|---:|---:|"
    ]

    for agent in sorted(agent_costs.keys(), key=lambda a: agent_costs[a]["total_cost_usd"], reverse=True):
        data = agent_costs[agent]
        cost = data["total_cost_usd"]
        pct = (cost / total_cost * 100) if total_cost > 0 else 0

        tokens = data["tokens"]
        llm_total = tokens["input"] + tokens["output"] + tokens["cache_5m_write"] + tokens["cache_1h_write"] + tokens["cache_read"]

        lines.append(
            f"| {agent} | {_format_number(llm_total)} | ${cost:.6f} | {pct:.1f}% |"
        )

    sections.append("\n".join(lines))

    # Detailed breakdown per agent (optional, can be collapsed)
    sections.append("\n<details>")
    sections.append("<summary>📊 Detailed Token Breakdown by Agent</summary>\n")

    for agent in sorted(agent_costs.keys()):
        data = agent_costs[agent]
        tokens = data["tokens"]

        sections.append(f"\n**{agent}** (${data['total_cost_usd']:.6f})")

        # Token details
        token_lines = []
        if tokens["input"] > 0:
            token_lines.append(f"- Input: {_format_number(tokens['input'])}")
        if tokens["output"] > 0:
            token_lines.append(f"- Output: {_format_number(tokens['output'])}")
        if tokens["cache_5m_write"] > 0:
            token_lines.append(f"- Cache Write (5m): {_format_number(tokens['cache_5m_write'])}")
        if tokens["cache_1h_write"] > 0:
            token_lines.append(f"- Cache Write (1h): {_format_number(tokens['cache_1h_write'])}")
        if tokens["cache_read"] > 0:
            token_lines.append(f"- Cache Read: {_format_number(tokens['cache_read'])}")
        if tokens["embedding"] > 0:
            token_lines.append(f"- Embedding: {_format_number(tokens['embedding'])}")

        if token_lines:
            sections.append("\n".join(token_lines))

    sections.append("\n</details>")

    return "\n".join(sections)
