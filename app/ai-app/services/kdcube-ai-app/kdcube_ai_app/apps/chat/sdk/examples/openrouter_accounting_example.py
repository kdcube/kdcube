"""
OpenRouter Integration — Accounting Validation Example
=======================================================

Demonstrates end-to-end accounting flow for OpenRouter completion calls:

1. Initialise accounting storage and context
2. Make single-turn OpenRouter completion calls
3. Verify that accounting events are correctly emitted
4. Show that the cost calculator can process OpenRouter events

Usage:
    # Set your OpenRouter API key
    export OPENROUTER_API_KEY=sk-or-...

    # Optionally set storage path (defaults to local file)
    export KDCUBE_STORAGE_PATH=file:///tmp/kdcube-data

    python -m kdcube_ai_app.apps.chat.sdk.examples.openrouter_accounting_example
"""

import asyncio
import json
import os
import uuid
from typing import Dict, Any

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.accounting import (
    AccountingSystem,
    with_accounting,
    _get_context,
)
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session, bind_accounting
from kdcube_ai_app.infra.accounting.usage import (
    price_table,
    _find_llm_price,
    compute_llm_equivalent_tokens,
)
from kdcube_ai_app.infra.service_hub.openrouter import (
    openrouter_completion,
    openrouter_completion_json,
)
from kdcube_ai_app.storage.storage import create_storage_backend


# ── Configuration ──

TENANT_ID = None
PROJECT_ID = None
storage_backend = None
SYSTEM = "openrouter-accounting-test"


def configure_env():
    """Load environment and initialise storage."""
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    settings = get_settings()
    global TENANT_ID, PROJECT_ID, storage_backend

    TENANT_ID = settings.TENANT or os.getenv("TENANT_ID", "home")
    PROJECT_ID = settings.PROJECT or os.getenv("DEFAULT_PROJECT_NAME", "demo")
    storage_path = settings.STORAGE_PATH or os.getenv(
        "KDCUBE_STORAGE_PATH", "file:///tmp/kdcube-openrouter-test"
    )
    storage_backend = create_storage_backend(storage_path)


# ── Test Functions ──

async def test_simple_completion():
    """Test 1: Simple text completion via OpenRouter."""
    print("\n" + "=" * 60)
    print("TEST 1: Simple OpenRouter Completion")
    print("=" * 60)

    with with_accounting("test-simple-completion", metadata={"phase": "test"}):
        result = await openrouter_completion(
            model="google/gemini-2.5-flash-preview",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Be concise."},
                {"role": "user", "content": "What is the capital of France? One word."},
            ],
            temperature=0.0,
            max_tokens=50,
        )

    print(f"  Success: {result['success']}")
    print(f"  Model:   {result['model']}")
    print(f"  Text:    {result['text'][:200]}")
    print(f"  Usage:   {result['usage']}")
    return result


async def test_json_completion():
    """Test 2: JSON-mode completion for structured extraction."""
    print("\n" + "=" * 60)
    print("TEST 2: OpenRouter JSON Extraction")
    print("=" * 60)

    sample_text = """
    Meeting Notes - March 10, 2026
    Attendees: Alice Smith, Bob Jones, Carol White
    Topics: Q1 revenue review ($2.4M, up 15%), new product launch timeline
    Action items:
    - Alice: Prepare Q1 report by March 15
    - Bob: Schedule vendor meeting next week
    - Carol: Draft product spec by end of month
    """

    with with_accounting("test-json-extraction", metadata={"phase": "test"}):
        result = await openrouter_completion_json(
            model="google/gemini-2.5-flash-preview",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract structured data from the text. "
                        "Return a JSON object with keys: "
                        "date, attendees (list), topics (list), action_items (list of {assignee, task, deadline})."
                    ),
                },
                {"role": "user", "content": sample_text},
            ],
            temperature=0.0,
            max_tokens=1024,
        )

    print(f"  Success: {result['success']}")
    print(f"  Parsed:  {json.dumps(result.get('parsed'), indent=2) if result.get('parsed') else 'FAILED'}")
    print(f"  Usage:   {result['usage']}")
    return result


async def test_classification():
    """Test 3: Text classification."""
    print("\n" + "=" * 60)
    print("TEST 3: OpenRouter Classification")
    print("=" * 60)

    texts = [
        "The new MacBook Pro has incredible performance with the M4 chip.",
        "I'm so frustrated with the customer service, waited 2 hours!",
        "Can you help me reset my password? I forgot it.",
        "Great news! Our team won the championship!",
    ]

    with with_accounting("test-classification", metadata={"phase": "test"}):
        result = await openrouter_completion_json(
            model="google/gemini-2.5-flash-preview",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify each text into one of: product_review, complaint, "
                        "support_request, positive_news. Return JSON: "
                        '{\"classifications\": [{\"text_index\": 0, \"category\": \"...\", \"confidence\": 0.95}]}'
                    ),
                },
                {"role": "user", "content": json.dumps(texts)},
            ],
            temperature=0.0,
            max_tokens=512,
        )

    print(f"  Success: {result['success']}")
    if result.get("parsed"):
        for cls in result["parsed"].get("classifications", []):
            idx = cls.get("text_index", "?")
            cat = cls.get("category", "?")
            print(f"    [{idx}] {cat}")
    print(f"  Usage:   {result['usage']}")
    return result


async def verify_accounting_events():
    """Verify that accounting events were correctly recorded."""
    print("\n" + "=" * 60)
    print("ACCOUNTING VERIFICATION")
    print("=" * 60)

    ctx = _get_context()
    events = ctx.get_cached_events()

    print(f"  Total events captured: {len(events)}")
    for i, ev in enumerate(events):
        ed = ev.to_dict()
        print(f"\n  Event {i + 1}:")
        print(f"    Service type: {ed.get('service_type')}")
        print(f"    Provider:     {ed.get('provider')}")
        print(f"    Model:        {ed.get('model_or_service')}")
        print(f"    Success:      {ed.get('success')}")
        print(f"    Component:    {ed.get('component')}")
        usage = ed.get("usage") or {}
        print(f"    Input tokens:  {usage.get('input_tokens', 0)}")
        print(f"    Output tokens: {usage.get('output_tokens', 0)}")
        print(f"    Total tokens:  {usage.get('total_tokens', 0)}")

    return events


def verify_pricing():
    """Verify that OpenRouter models are found in the price table."""
    print("\n" + "=" * 60)
    print("PRICING VERIFICATION")
    print("=" * 60)

    test_models = [
        ("openrouter", "google/gemini-2.5-flash-preview"),
        ("openrouter", "anthropic/claude-3.5-sonnet"),
        ("openrouter", "meta-llama/llama-3.1-70b-instruct"),
        ("openrouter", "deepseek/deepseek-r1"),
        ("openrouter", "nonexistent/model"),  # should return None
    ]

    for provider, model in test_models:
        price = _find_llm_price(provider, model)
        if price:
            print(
                f"  {model}: "
                f"${price['input_tokens_1M']}/M input, "
                f"${price['output_tokens_1M']}/M output"
            )
        else:
            print(f"  {model}: NOT FOUND (will use missing_price fallback)")

    # Test equivalent token computation with OpenRouter events
    sample_rollup = [
        {
            "service": "llm",
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash-preview",
            "spent": {"input": 500, "output": 100},
        },
        {
            "service": "llm",
            "provider": "openrouter",
            "model": "anthropic/claude-3.5-sonnet",
            "spent": {"input": 1000, "output": 200},
        },
    ]

    equiv = compute_llm_equivalent_tokens(
        sample_rollup,
        ref_provider="anthropic",
        ref_model="claude-sonnet-4-5-20250929",
    )
    print(f"\n  Equivalent tokens (ref=sonnet-4.5): {equiv['llm_equivalent_tokens']}")
    for item in equiv.get("by_model", []):
        print(
            f"    {item['provider']}/{item['model']}: "
            f"{item['equiv_tokens']:.1f} equiv tokens"
        )


# ── Main ──

async def run_all_tests():
    """Run all validation tests."""
    print("\n" + "=" * 60)
    print("  OPENROUTER ACCOUNTING VALIDATION SUITE")
    print("=" * 60)

    # 1. Verify pricing table
    verify_pricing()

    # 2. Run completion tests (only if API key is set)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("\n  OPENROUTER_API_KEY not set — skipping live API tests.")
        print("  Set the env variable and re-run to test live calls.")
        return

    await test_simple_completion()
    await test_json_completion()
    await test_classification()

    # 3. Verify accounting events
    await verify_accounting_events()

    print("\n" + "=" * 60)
    print("  ALL TESTS COMPLETE")
    print("=" * 60)


async def main():
    configure_env()

    session = {
        "user_id": os.getenv("DEMO_USER_ID", "demo-user"),
        "session_id": os.getenv("DEMO_SESSION_ID", "demo-session"),
    }

    request_id = str(uuid.uuid4())
    component = "openrouter-accounting-test"

    envelope = build_envelope_from_session(
        session=session,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        request_id=request_id,
        component=component,
    )

    async with bind_accounting(
        envelope,
        storage_backend=storage_backend,
        enabled=True,
    ):
        async with with_accounting(component, system=SYSTEM):
            await run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
