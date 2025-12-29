import asyncio
import os, uuid, base64
from typing import List, Callable
from pathlib import Path

from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session, bind_accounting
from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase, ConfigRequest, create_workflow_config,
    create_cached_system_message, create_cached_human_message,
    create_document_message, create_image_message
)
from kdcube_ai_app.storage.storage import create_storage_backend

DEFAULT_MODEL = "claude-3-7-sonnet-20250219" # will be bound if no model specified for role
sonnet_4 = "claude-sonnet-4-20250514"
sonnet_45 = "claude-sonnet-4-5-20250929"
haiku_3 = "claude-3-5-haiku-20241022" # "claude-3-haiku-20240307"
haiku_4 = "claude-haiku-4-5-20251001"
gemini_25_flash = "gemini-2.5-flash" # "haiku"
gemini_25_pro = "gemini-2.5-pro" # "sonnet"

TENANT_ID = None
PROJECT_ID = None
kdcube_storage_backend = None
ms = None

SYSTEM = "my-system"
ROLE_FRIENDLY_ASSISTANT = "friendly-assistant"
ROLE_DOCUMENT_ANALYZER = "document-analyzer"
ROLE_IMAGE_ANALYZER = "image-analyzer"

def configure_env():
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    settings = get_settings()
    global TENANT_ID, PROJECT_ID, kdcube_storage_backend, ms

    TENANT_ID = settings.TENANT
    PROJECT_ID = settings.PROJECT
    KDCUBE_STORAGE_PATH = settings.STORAGE_PATH
    STORAGE_KWARGS = {}
    kdcube_storage_backend = create_storage_backend(KDCUBE_STORAGE_PATH, **STORAGE_KWARGS)

    req = ConfigRequest(
        openai_api_key=settings.OPENAI_API_KEY,
        claude_api_key=settings.ANTHROPIC_API_KEY,
        google_api_key=settings.GOOGLE_API_KEY,
        selected_model=DEFAULT_MODEL,
        role_models={
            ROLE_FRIENDLY_ASSISTANT: {"provider": "google", "model": gemini_25_pro},
            ROLE_DOCUMENT_ANALYZER: {"provider": "anthropic", "model": haiku_4},
            ROLE_IMAGE_ANALYZER: {"provider": "anthropic", "model": haiku_4},
        },
    )

    ms = ModelServiceBase(create_workflow_config(req))


def load_file_as_base64(filepath: str) -> str:
    """Load a file and return base64-encoded string."""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def streaming(ms: ModelServiceBase,
                    agent_name: str,
                    msgs: List[BaseMessage],
                    on_delta_fn: Callable = None):

    client = ms.get_client(agent_name)

    async def on_delta(d):
        print(d, end="", flush=True)

    async def on_thinking(d):
        print(f"\n[THINKING: {d.get('text', '')}]", flush=True)

    if not on_delta_fn:
        on_delta_fn = on_delta

    # Nested call to with_accounting will overwrite the "component name" and merge the new metadata
    with with_accounting(agent_name, metadata={"phase": "test"}):
        ret = await ms.stream_model_text_tracked(
            client,
            msgs,
            on_delta=on_delta_fn,
            on_thinking=on_thinking,
            role=agent_name,
            temperature=1.0,
            max_tokens=500,
            max_thinking_tokens=128,
            debug=True
        )
    print("\n")
    return ret


async def run_with_accounting(tenant_id,
                              project_id,
                              request_id,
                              GLOBAL_COMPONENT,
                              fn: Callable,
                              global_record_metadata=None,
                              global_accounting_attributes=None):

    if not global_accounting_attributes:
        global_accounting_attributes = dict()

    if not global_record_metadata:
        global_record_metadata = dict()

    envelope = build_envelope_from_session(
        session=session,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=request_id,
        component=GLOBAL_COMPONENT,
        metadata=global_record_metadata,
    )

    async with bind_accounting(envelope,
                               storage_backend=kdcube_storage_backend,
                               enabled=True):
        async with with_accounting(GLOBAL_COMPONENT, **global_accounting_attributes):
            data = await fn()
            return data


# ==================== TEST EXAMPLES ====================

async def _test_simple_cached_message():
    """Test 1: Simple text message with caching."""
    print("\n" + "="*60)
    print("TEST 1: Simple Cached Message")
    print("="*60)

    msgs = [
        create_cached_system_message("You are concise.", cache_last=True),
        create_cached_human_message("Say hi!")
    ]

    return await streaming(ms, ROLE_FRIENDLY_ASSISTANT, msgs)


async def _test_multipart_cached_system():
    """Test 2: Multi-part system message with selective caching."""
    print("\n" + "="*60)
    print("TEST 2: Multi-part System Message with Selective Caching")
    print("="*60)

    msgs = [
        create_cached_system_message([
            {"type": "text", "text": "You are a helpful Java teacher.", "cache": False},
            {"type": "text", "text": """
            Core Java concepts to cover:
            - Object-oriented programming (classes, inheritance, polymorphism)
            - Data structures (ArrayList, HashMap, LinkedList)
            - Exception handling and error management
            - File I/O and streams
            - Multithreading basics
            - Collections framework
            """, "cache": True},  # Cache the long reference content
            {"type": "text", "text": "Create beginner-friendly learning plans.", "cache": False}
        ]),
        create_cached_human_message(
            "I need to learn Java for a gaming project. Give me 5 actions for the next 5 days."
        )
    ]

    return await streaming(ms, ROLE_FRIENDLY_ASSISTANT, msgs)


async def _test_pdf_document_analysis():
    """Test 3: PDF document analysis with caching."""
    print("\n" + "="*60)
    print("TEST 3: PDF Document Analysis")
    print("="*60)

    # Example: Create a dummy PDF or use existing one
    pdf_path = "test_document.pdf"

    # Check if file exists, otherwise skip
    if not Path(pdf_path).exists():
        print(f"⚠️  Skipping: {pdf_path} not found")
        print("To test PDF analysis, create a test PDF file at:", pdf_path)
        return None

    pdf_b64 = load_file_as_base64(pdf_path)

    msgs = [
        create_cached_system_message([
            {"type": "text", "text": "You are a document analyst. Extract key information accurately."},
            {"type": "text", "text": "Focus on: dates, amounts, parties involved, and action items."}
        ]),
        create_document_message(
            "Summarize this document in 3 bullet points",
            pdf_b64,
            media_type="application/pdf",
            cache_document=True  # Cache the PDF for repeated queries
        )
    ]

    return await streaming(ms, ROLE_DOCUMENT_ANALYZER, msgs)


async def _test_image_analysis():
    """Test 4: Image analysis with caching."""
    print("\n" + "="*60)
    print("TEST 4: Image Analysis")
    print("="*60)

    # Example: Use existing image or create test one
    image_path = "resources/Gemini_Generated_Image_9x51np9x51np9x51.png"

    if not Path(image_path).exists():
        print(f"⚠️  Skipping: {image_path} not found")
        print("To test image analysis, add an image file at:", image_path)
        return None

    img_b64 = load_file_as_base64(image_path)

    msgs = [
        create_cached_system_message("You are a visual analyst. Describe images clearly and concisely."),
        create_image_message(
            "What do you see in this image?",
            img_b64,
            media_type="image/png",
            cache_image=True
        )
    ]

    return await streaming(ms, ROLE_IMAGE_ANALYZER, msgs)


async def _test_multimodal_comparison():
    """Test 5: Multi-document/image comparison with caching."""
    print("\n" + "="*60)
    print("TEST 5: Multimodal Document Comparison")
    print("="*60)

    # Check for test files
    pdf1_path = "report_q1.pdf"
    pdf2_path = "report_q2.pdf"

    if not (Path(pdf1_path).exists() and Path(pdf2_path).exists()):
        print(f"⚠️  Skipping: Need {pdf1_path} and {pdf2_path}")
        print("To test comparison, create two PDF files for comparison")
        return None

    pdf1_b64 = load_file_as_base64(pdf1_path)
    pdf2_b64 = load_file_as_base64(pdf2_path)

    msgs = [
        create_cached_system_message(
            "You are a financial analyst. Compare documents and highlight key differences."
        ),
        create_cached_human_message([
            {"type": "text", "text": "Compare these two quarterly reports:"},
            {"type": "document", "data": pdf1_b64, "media_type": "application/pdf", "cache": False},
            {"type": "document", "data": pdf2_b64, "media_type": "application/pdf", "cache": True},
            {"type": "text", "text": "What changed between Q1 and Q2?"}
        ])
    ]

    return await streaming(ms, ROLE_DOCUMENT_ANALYZER, msgs)


async def _test_knowledge_base_with_cached_kb():
    """Test 6: Knowledge base document cached across queries."""
    print("\n" + "="*60)
    print("TEST 6: Cached Knowledge Base")
    print("="*60)

    kb_pdf_path = "knowledge_base.pdf"

    if not Path(kb_pdf_path).exists():
        print(f"⚠️  Skipping: {kb_pdf_path} not found")
        print("To test KB caching, create a knowledge base PDF")
        return None

    kb_b64 = load_file_as_base64(kb_pdf_path)

    # System message with cached KB document
    msgs = [
        create_cached_system_message([
            {"type": "text", "text": "You are a Q&A assistant. Use the knowledge base below:"},
            {
                "type": "document",
                "data": kb_b64,
                "media_type": "application/pdf",
                "cache": True  # ✅ Cache KB - reused across multiple user queries
            },
            {"type": "text", "text": "Answer based only on the KB. Say 'not found' if unsure."}
        ]),
        create_cached_human_message("What is the refund policy?")
    ]

    result1 = await streaming(ms, ROLE_DOCUMENT_ANALYZER, msgs)

    # Second query - KB is still cached!
    print("\n--- Second query (KB cached) ---")
    msgs[1] = create_cached_human_message("What are the shipping options?")

    result2 = await streaming(ms, ROLE_DOCUMENT_ANALYZER, msgs)

    return result1


async def run_all_tests():
    """Run all test examples."""

    print("\n" + "🧪 "*30)
    print("MULTIMODAL + CACHING TEST SUITE")
    print("🧪 "*30)

    # Simple tests (always run)
    await _test_simple_cached_message()
    await _test_multipart_cached_system()

    # File-based tests (run if files exist)
    await _test_pdf_document_analysis()
    await _test_image_analysis()
    await _test_multimodal_comparison()
    await _test_knowledge_base_with_cached_kb()

    print("\n" + "✅ "*30)
    print("ALL TESTS COMPLETE")
    print("✅ "*30)


if __name__ == "__main__":

    configure_env()

    session = {
        "user_id": os.getenv("DEMO_USER_ID", "demo-user"),
        "session_id": os.getenv("DEMO_SESSION_ID", "demo-session"),
    }

    GLOBAL_COMPONENT = "test-multimodal-caching"
    service_identity = "test-service-multimodal"
    request_id = str(uuid.uuid4())

    record_metadata = {
        "service_identity": service_identity,
    }

    accounting_attributes = {
        "system": SYSTEM,
    }

    # Choose which test to run:
    # fn = _test_simple_cached_message
    # fn = _test_multipart_cached_system
    # fn = _test_pdf_document_analysis
    fn = _test_image_analysis
    # fn = _test_multimodal_comparison
    # fn = _test_knowledge_base_with_cached_kb
    # fn = run_all_tests  # Run all tests

    asyncio.run(run_with_accounting(
        TENANT_ID,
        PROJECT_ID,
        request_id,
        GLOBAL_COMPONENT,
        fn,
        record_metadata,
        accounting_attributes
    ))