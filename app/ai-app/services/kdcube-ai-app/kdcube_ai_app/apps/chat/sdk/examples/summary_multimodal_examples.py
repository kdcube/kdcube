import asyncio
import base64
from pathlib import Path
from typing import Optional, Dict, Any

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, ConfigRequest, create_workflow_config
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends import build_summary_for_tool_output

DEFAULT_MODEL = "claude-3-7-sonnet-20250219"
ROLE_SUMMARIZER = "solver.react.summary"

TENANT_ID = None
PROJECT_ID = None
ms = None

def _load_file_b64(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _build_summary_artifact_inline(text: str) -> Dict[str, Any]:
    return {
        "type": "inline",
        "mime": "text/plain",
        "text": text,
    }


def _build_summary_artifact_file(
    *,
    path: Path,
    mime: str,
    text_surrogate: str,
) -> Optional[Dict[str, Any]]:
    b64 = _load_file_b64(path)
    if not b64:
        return None
    return {
        "type": "file",
        "mime": mime,
        "text": text_surrogate,
        "base64": b64,
        "filename": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
    }


def configure_env() -> ModelServiceBase:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    settings = get_settings()
    global TENANT_ID, PROJECT_ID, ms

    TENANT_ID = settings.TENANT
    PROJECT_ID = settings.PROJECT

    req = ConfigRequest(
        openai_api_key=settings.OPENAI_API_KEY,
        claude_api_key=settings.ANTHROPIC_API_KEY,
        google_api_key=settings.GOOGLE_API_KEY,
        selected_model=DEFAULT_MODEL,
        role_models={
            ROLE_SUMMARIZER: {"provider": "anthropic", "model": "claude-3-5-haiku-20241022"},
        },
    )
    ms = ModelServiceBase(create_workflow_config(req))
    return ms


async def _summarize_one(
    service: ModelServiceBase,
    *,
    tool_id: str,
    output: Any,
    summary_artifact: Optional[Dict[str, Any]],
    label: str,
):
    summary_obj, summary_txt = await build_summary_for_tool_output(
        tool_id=tool_id,
        output=output,
        summary_artifact=summary_artifact,
        use_llm_summary=True,
        llm_service=service,
        bundle_id="examples",
        timezone="UTC",
        call_reason=f"Example summary for {label}",
        tool_inputs={"example": label},
        call_signature=f"{tool_id}(...)",
        param_bindings_for_summary="(example)",
        tool_doc_for_summary="Example tool output for summarization",
        structured=False,
    )

    print("=" * 80)
    print(f"{label} summary")
    print(summary_txt or summary_obj or "(no summary)")


async def run_examples():
    service = configure_env()

    # 1) Inline text artifact
    text_output = {
        "status": "ok",
        "text": "Mermaid diagram rendered; nodes A->B->C",
    }
    text_artifact = _build_summary_artifact_inline(
        "Mermaid diagram description: nodes A, B, C; edges A->B->C"
    )
    await _summarize_one(
        service,
        tool_id="example.tool",
        output=text_output,
        summary_artifact=text_artifact,
        label="inline text",
    )

    # 2) Image artifact (PNG)
    img_path = Path(__file__).parent / "resources/broken_mermaid_diagram.png"
    img_artifact = _build_summary_artifact_file(
        path=img_path,
        mime="image/png",
        text_surrogate="Expected: Mermaid diagram rendered as PNG",
    )
    if img_artifact:
        await _summarize_one(
            service,
            tool_id="example.tool",
            output={"image": "artifact", "note": "Rendered PNG output"},
            summary_artifact=img_artifact,
            label="image (png)",
        )
    else:
        print("Skipping image example: PNG not found")

    # 3) PDF artifact
    pdf_path = Path(__file__).parent / "resources/multi-page-with-tables.pdf"
    pdf_artifact = _build_summary_artifact_file(
        path=pdf_path,
        mime="application/pdf",
        text_surrogate="Expected: PDF report with 3 pages and couple of tables",
    )
    if pdf_artifact:
        await _summarize_one(
            service,
            tool_id="example.tool",
            output={"document": "artifact", "note": "Generated PDF output"},
            summary_artifact=pdf_artifact,
            label="document (pdf)",
        )
    else:
        print("Skipping PDF example: test_document.pdf not found")


if __name__ == "__main__":
    asyncio.run(run_examples())
