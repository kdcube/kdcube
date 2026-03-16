"""
OpenRouter Integration — Example Agent
=======================================

Demonstrates OpenRouter's data-processing capabilities across multiple
use cases, showing how to build agents that leverage OpenRouter for
single-turn tasks.

Capabilities shown:
  1. Text extraction — pull structured fields from unstructured text
  2. Classification — categorise inputs with confidence scores
  3. Tagging / labelling — multi-label assignment
  4. Summarization — condense long text
  5. Schema generation — infer JSON schemas from sample data
  6. Multi-model comparison — same prompt across different models

Each capability is wrapped in a reusable async function that can be
composed into larger pipelines.

Usage:
    export OPENROUTER_API_KEY=sk-or-...
    export KDCUBE_STORAGE_PATH=file:///tmp/kdcube-data

    python -m kdcube_ai_app.apps.chat.sdk.examples.openrouter_agent_example
"""

import asyncio
import json
import os
import textwrap
import uuid
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.accounting import with_accounting, _get_context
from kdcube_ai_app.infra.accounting.envelope import (
    build_envelope_from_session,
    bind_accounting,
)
from kdcube_ai_app.infra.service_hub.openrouter import (
    openrouter_completion,
    openrouter_completion_json,
)
from kdcube_ai_app.storage.storage import create_storage_backend


# ── Configuration ────────────────────────────────────────────────────────────

TENANT_ID: Optional[str] = None
PROJECT_ID: Optional[str] = None
storage_backend = None

# Models used in examples — all routed through OpenRouter
MODEL_FAST = "google/gemini-2.5-flash-preview"
MODEL_SMART = "anthropic/claude-3.5-sonnet"
MODEL_CHEAP = "meta-llama/llama-3.1-8b-instruct"


def configure_env():
    """Load environment and initialise storage."""
    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())
    settings = get_settings()

    global TENANT_ID, PROJECT_ID, storage_backend
    TENANT_ID = settings.TENANT or os.getenv("TENANT_ID", "home")
    PROJECT_ID = settings.PROJECT or os.getenv("DEFAULT_PROJECT_NAME", "demo")
    storage_path = settings.STORAGE_PATH or os.getenv(
        "KDCUBE_STORAGE_PATH", "file:///tmp/kdcube-openrouter-agent"
    )
    storage_backend = create_storage_backend(storage_path)


# ── Helper ───────────────────────────────────────────────────────────────────

def _print_section(title: str):
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}")


def _print_result(result: Dict[str, Any], max_text: int = 400):
    if not result["success"]:
        print(f"  ERROR: {result['error']}")
        return
    print(f"  Model:  {result['model']}")
    usage = result.get("usage", {})
    print(f"  Tokens: {usage.get('total_tokens', 0)} "
          f"(in={usage.get('input_tokens', 0)}, out={usage.get('output_tokens', 0)})")
    text = result.get("text", "")
    if text:
        print(f"  Output: {text[:max_text]}{'...' if len(text) > max_text else ''}")
    parsed = result.get("parsed")
    if parsed:
        print(f"  Parsed JSON:\n{json.dumps(parsed, indent=2)[:600]}")


# ── Capability 1: Extraction ────────────────────────────────────────────────

SAMPLE_INVOICE = textwrap.dedent("""\
    INVOICE #INV-2026-0042
    Date: March 5, 2026
    Due: April 4, 2026

    Bill To:
      Acme Corp
      123 Industrial Way, Suite 400
      San Francisco, CA 94107
      Contact: Jane Doe (jane@acme.corp)

    Items:
      1. Cloud Hosting (Standard Plan)   $2,400.00/mo  x 1  = $2,400.00
      2. Support Add-on (Premium)         $500.00/mo   x 1  =   $500.00
      3. Data Transfer (500 GB overage)     $0.09/GB   x 500 =    $45.00

    Subtotal: $2,945.00
    Tax (8.5%): $250.33
    Total Due: $3,195.33

    Payment: Wire transfer to First National Bank, Acct #7890-1234
""")


async def demo_extraction(model: str = MODEL_FAST) -> Dict[str, Any]:
    """Extract structured fields from an invoice."""
    _print_section("1. EXTRACTION — Invoice Data")
    with with_accounting("extraction", metadata={"demo": True}):
        result = await openrouter_completion_json(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract all structured information from the invoice. "
                        "Return JSON with: invoice_number, date, due_date, "
                        "bill_to (name, address, contact_name, contact_email), "
                        "line_items (list of {description, unit_price, quantity, total}), "
                        "subtotal, tax_rate, tax_amount, total_due, payment_method."
                    ),
                },
                {"role": "user", "content": SAMPLE_INVOICE},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
    _print_result(result)
    return result


# ── Capability 2: Classification ────────────────────────────────────────────

SAMPLE_TICKETS = [
    "My laptop screen is flickering after the latest update.",
    "I'd like to upgrade my plan to Enterprise.",
    "Your product is amazing, saved us hours of work!",
    "URGENT: Production database is down, cannot process orders.",
    "How do I export my data in CSV format?",
    "Cancel my subscription effective immediately.",
]


async def demo_classification(model: str = MODEL_FAST) -> Dict[str, Any]:
    """Classify support tickets by category and priority."""
    _print_section("2. CLASSIFICATION — Support Tickets")
    with with_accounting("classification", metadata={"demo": True}):
        result = await openrouter_completion_json(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify each support ticket. Return JSON:\n"
                        '{"tickets": [{"index": 0, "category": "...", '
                        '"priority": "low|medium|high|critical", '
                        '"sentiment": "positive|neutral|negative", '
                        '"confidence": 0.95}]}\n'
                        "Categories: bug_report, upgrade_request, feedback, "
                        "outage, how_to, cancellation"
                    ),
                },
                {"role": "user", "content": json.dumps(SAMPLE_TICKETS, indent=2)},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
    _print_result(result)
    return result


# ── Capability 3: Tagging / Labelling ───────────────────────────────────────

SAMPLE_ARTICLE = textwrap.dedent("""\
    Researchers at MIT have developed a new battery technology using
    aluminum-sulfur cells that could reduce energy storage costs by 90%.
    The technology uses common materials and operates at moderate temperatures,
    making it suitable for grid-scale deployment. Unlike lithium-ion batteries,
    the aluminum-sulfur cells don't require rare earth minerals. Early tests
    show the cells maintain 95% capacity after 1,000 charge cycles. The team
    has partnered with a major utility company for pilot deployments in 2027.
    Environmental groups have praised the development, noting its potential
    to accelerate the renewable energy transition.
""")


async def demo_tagging(model: str = MODEL_FAST) -> Dict[str, Any]:
    """Assign multiple labels/tags to an article."""
    _print_section("3. TAGGING — Article Labels")
    with with_accounting("tagging", metadata={"demo": True}):
        result = await openrouter_completion_json(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Assign relevant tags to the article. Return JSON:\n"
                        '{"tags": ["tag1", "tag2", ...], '
                        '"primary_topic": "...", '
                        '"entities": [{"name": "...", "type": "org|person|tech|location"}], '
                        '"reading_level": "elementary|intermediate|advanced"}'
                    ),
                },
                {"role": "user", "content": SAMPLE_ARTICLE},
            ],
            temperature=0.0,
            max_tokens=512,
        )
    _print_result(result)
    return result


# ── Capability 4: Summarization ─────────────────────────────────────────────

SAMPLE_REPORT = textwrap.dedent("""\
    Q4 2025 Financial Report — TechVentures Inc.

    Revenue: TechVentures reported total revenue of $48.2M for Q4 2025,
    representing a 23% year-over-year increase. SaaS recurring revenue
    grew 31% to $38.5M, now comprising 80% of total revenue. Professional
    services contributed $9.7M, down 5% from the prior year as the company
    continues its strategic shift toward recurring revenue streams.

    Profitability: Operating income reached $7.2M (15% margin), up from
    $4.1M (10% margin) in Q4 2024. The improvement was driven by economies
    of scale in cloud infrastructure and a 12% reduction in customer
    acquisition costs. Net income was $5.8M ($0.42 per diluted share),
    compared to $2.9M ($0.21 per diluted share) in the year-ago quarter.

    Customer Metrics: Total customers grew to 2,847 (up 18%), with enterprise
    customers (>$100K ARR) reaching 312 (up 28%). Net revenue retention was
    118%, and gross churn decreased to 4.2% from 5.8%. Average contract value
    for new enterprise deals increased 15% to $187K.

    Product: The company launched three major features: AI-powered analytics
    dashboard, automated compliance reporting, and a marketplace for
    third-party integrations. The marketplace now hosts 145 partner
    integrations. R&D spending was $11.3M (23% of revenue).

    Outlook: Management raised full-year 2026 guidance to $210-220M in
    revenue (25-31% growth) and expects to achieve 20% operating margins
    by Q4 2026. The company plans to expand into the APAC market with a
    new Singapore office opening in Q2 2026.
""")


async def demo_summarization(model: str = MODEL_FAST) -> Dict[str, Any]:
    """Produce a structured summary of a financial report."""
    _print_section("4. SUMMARIZATION — Financial Report")
    with with_accounting("summarization", metadata={"demo": True}):
        result = await openrouter_completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the financial report in three sections:\n"
                        "1. Executive Summary (2-3 sentences)\n"
                        "2. Key Metrics (bullet points)\n"
                        "3. Outlook (1-2 sentences)\n"
                        "Be precise with numbers."
                    ),
                },
                {"role": "user", "content": SAMPLE_REPORT},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
    _print_result(result)
    return result


# ── Capability 5: Schema Generation ─────────────────────────────────────────

SAMPLE_DATA = [
    {"name": "Alice", "age": 30, "email": "alice@example.com", "scores": [95, 88, 72]},
    {"name": "Bob", "age": None, "email": "bob@test.org", "scores": [100]},
    {"name": "Carol", "department": "Engineering", "active": True},
]


async def demo_schema_generation(model: str = MODEL_FAST) -> Dict[str, Any]:
    """Infer a JSON Schema from sample data."""
    _print_section("5. SCHEMA GENERATION — JSON Schema Inference")
    with with_accounting("schema-generation", metadata={"demo": True}):
        result = await openrouter_completion_json(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analyze the sample JSON records and produce a JSON Schema (draft 2020-12) "
                        "that validates all of them. Include: type constraints, required fields, "
                        "optional fields (mark nullable), array item types, and format hints "
                        "(e.g. email). Return the schema as JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(SAMPLE_DATA, indent=2)},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
    _print_result(result)
    return result


# ── Capability 6: Multi-Model Comparison ────────────────────────────────────

async def demo_multi_model_comparison() -> List[Dict[str, Any]]:
    """Run the same prompt across multiple OpenRouter models to compare output."""
    _print_section("6. MULTI-MODEL COMPARISON")

    prompt = "Explain what a Bloom filter is in exactly two sentences."
    models = [MODEL_FAST, MODEL_SMART, MODEL_CHEAP]
    results = []

    for model in models:
        print(f"\n  --- {model} ---")
        with with_accounting("multi-model", metadata={"model": model, "demo": True}):
            result = await openrouter_completion(
                model=model,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=256,
            )
        _print_result(result, max_text=300)
        results.append(result)

    return results


# ── Accounting Summary ──────────────────────────────────────────────────────

def print_accounting_summary():
    """Print a summary of all accounting events emitted during the run."""
    _print_section("ACCOUNTING SUMMARY")
    ctx = _get_context()
    events = ctx.get_cached_events()

    total_input = 0
    total_output = 0
    total_cost_est = 0.0

    for ev in events:
        d = ev.to_dict()
        usage = d.get("usage", {})
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        total_input += inp
        total_output += out

    print(f"  Events emitted:  {len(events)}")
    print(f"  Total input:     {total_input:,} tokens")
    print(f"  Total output:    {total_output:,} tokens")
    print(f"  Total tokens:    {total_input + total_output:,}")

    print("\n  Per-call breakdown:")
    for i, ev in enumerate(events):
        d = ev.to_dict()
        usage = d.get("usage", {})
        print(
            f"    [{i+1}] {d.get('component', '?'):20s} "
            f"model={d.get('model_or_service', '?'):40s} "
            f"in={usage.get('input_tokens', 0):>6,} "
            f"out={usage.get('output_tokens', 0):>6,}"
        )


# ── Main ────────────────────────────────────────────────────────────────────

async def run_all_demos():
    """Run all demonstration capabilities."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("\n  OPENROUTER_API_KEY not set — cannot run live demos.")
        print("  Set the env variable and re-run.")
        return

    await demo_extraction()
    await demo_classification()
    await demo_tagging()
    await demo_summarization()
    await demo_schema_generation()
    await demo_multi_model_comparison()

    print_accounting_summary()

    _print_section("ALL DEMOS COMPLETE")


async def main():
    configure_env()

    session = {
        "user_id": os.getenv("DEMO_USER_ID", "demo-user"),
        "session_id": os.getenv("DEMO_SESSION_ID", "demo-session"),
    }

    envelope = build_envelope_from_session(
        session=session,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        request_id=str(uuid.uuid4()),
        component="openrouter-agent-demo",
    )

    async with bind_accounting(
        envelope,
        storage_backend=storage_backend,
        enabled=True,
    ):
        async with with_accounting("openrouter-agent-demo", system="openrouter-agent"):
            await run_all_demos()


if __name__ == "__main__":
    asyncio.run(main())
