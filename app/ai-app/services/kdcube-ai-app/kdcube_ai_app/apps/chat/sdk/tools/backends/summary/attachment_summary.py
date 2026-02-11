import re
from typing import Any, Dict, List, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.runtime.files_and_attachments import artifact_block_for_summary
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends import _modality_system_instructions
import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers
from kdcube_ai_app.infra.service_hub.inventory import create_cached_system_message, create_cached_human_message

def _attachment_summary_system_prompt() -> str:
    return (
        "You are summarizing a USER-PROVIDED ATTACHMENT.\n"
        "Goal: produce a compact, telegraphic, embedding-friendly inventory of the attachment content.\n"
        "Use any provided context (user prompt and other attachments) to resolve references, but do NOT assume.\n"
        "\n"
        "Output a TELEGRAPHIC, SECTIONED TEXT (NO JSON). Use pipes to separate sections.\n"
        "Format:\n"
        "semantic:<...> | structural:<...> | inventory:<...> | anomalies:<...> | safety:<...> | lookup_keys:<...> | filename:<...> | artifact_name:<...>\n"
        "\n"
        "Fields:\n"
        "- semantic: what the attachment is about; intent; domains; scope; key facts/samples/schema.\n"
        "- structural: file type, visible structure (tables/code/JSON/YAML/XML/diagrams), counts if visible.\n"
        "- inventory: notable fragments or sections to help retrieval.\n"
        "- anomalies: problems in the content (malformed, ambiguous, missing fields, garbled).\n"
        "- safety: benign/suspicious (+short reason if suspicious).\n"
        "- lookup_keys: 5-12 compact key phrases for retrieval.\n"
        "- filename: a short, unique, filesystem-safe name for this attachment (no spaces).\n"
        "- artifact_name: short, human-readable ID to use in paths (no spaces, unique enough).\n"
        "\n"
        "Rules:\n"
        "- Keep it short; telegraphic; no prose.\n"
        "- Mention attachment filename and mime.\n"
        "- If content is empty/unreadable, say so in structural/anomalies.\n"
    )

def _attachment_summary_prompt(modality_kind: Optional[str]) -> str:
    return _attachment_summary_system_prompt() + _modality_system_instructions(modality_kind)

def _extract_tagged_value(text: str, key: str) -> str:
    if not text:
        return ""
    m = re.search(rf"(?:^|[|\\s]){re.escape(key)}:([^|\\s]+)", text)
    if not m:
        return ""
    return (m.group(1) or "").strip()

def ensure_attachment_artifact_names(items: List[Dict[str, Any]]) -> None:
    used: Dict[str, int] = {}
    for a in items or []:
        if not isinstance(a, dict):
            continue
        summary = (a.get("summary") or "").strip()
        raw_name = (
            a.get("artifact_name")
            or _extract_tagged_value(summary, "artifact_name")
            or a.get("filename")
            or _extract_tagged_value(summary, "filename")
            or "attachment"
        )
        base = str(raw_name).strip() or "attachment"
        base = re.sub(r"[\\s./:]+", "_", base)
        base = re.sub(r"[^A-Za-z0-9_-]+", "", base) or "attachment"
        base = base.lower()
        count = used.get(base, 0) + 1
        used[base] = count
        a["artifact_name"] = base if count == 1 else f"{base}_{count}"

async def summarize_user_attachment(
        *,
        svc: Any,
        attachment: Dict[str, Any],
        user_prompt: str = "",
        other_attachments: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 600,
        max_attachment_chars: int = 12000,
        max_peer_chars: int = 2000,
) -> Optional[str]:
    if svc is None or not isinstance(attachment, dict):
        return None

    filename = (attachment.get("filename") or "attachment").strip()
    mime = (attachment.get("mime") or attachment.get("mime_type") or "application/octet-stream").strip()
    size = attachment.get("size") or attachment.get("size_bytes") or ""
    text = attachment.get("text") or ""
    read_error = attachment.get("read_error")
    if max_attachment_chars and len(text) > max_attachment_chars:
        text = text[:max_attachment_chars] + "\n...[truncated]"

    summary_artifact = {
        "type": "file",
        "mime": mime,
        "text": text,
        "base64": attachment.get("base64"),
        "filename": filename,
        "size_bytes": attachment.get("size_bytes") or size,
        "read_error": read_error,
    }
    artifact_block, artifact_meta, modality_kind = artifact_block_for_summary(summary_artifact)

    meta_line = f"filename={filename}; mime={mime}"
    if size != "":
        meta_line += f"; size={size}"

    blocks = [f"ATTACHMENT_META:\n{meta_line}"]
    if artifact_meta:
        blocks.append(artifact_meta)
    if text:
        blocks.append(f"ATTACHMENT_TEXT:\n{text}")
    else:
        blocks.append("ATTACHMENT_TEXT:\n<empty_or_unavailable>")
    if read_error:
        blocks.append(f"READ_ERROR:\n{read_error}")

    if user_prompt:
        blocks.append(f"USER_PROMPT:\n{user_prompt}")

    peers = [p for p in (other_attachments or []) if isinstance(p, dict) and p is not attachment]
    if peers:
        peer_lines: List[str] = []
        for p in peers:
            pname = (p.get("filename") or "attachment").strip()
            pmime = (p.get("mime") or p.get("mime_type") or "application/octet-stream").strip()
            psummary = (p.get("summary") or "").strip()
            ptext = (p.get("text") or "")
            if not psummary and ptext:
                if max_peer_chars and len(ptext) > max_peer_chars:
                    ptext = ptext[:max_peer_chars] + "\n...[truncated]"
                psummary = ptext
            if psummary:
                peer_lines.append(f"{pname} ({pmime}): {psummary}")
        if peer_lines:
            blocks.append("OTHER_ATTACHMENTS:\n" + "\n".join(peer_lines))

    system_prompt = _attachment_summary_prompt(modality_kind)
    user_msg = "\n\n".join(blocks).strip()

    from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json

    message_blocks: List[dict] = []
    if artifact_block:
        message_blocks.append({**artifact_block, "cache": True})
    message_blocks.append({"type": "text", "text": user_msg})

    role = "attachment.summary"
    result = await stream_agent_to_json(
        svc,
        client_name="attachment.summary",
        client_role="attachment.summary",
        sys_prompt=create_cached_system_message(system_prompt, cache_last=True),
        messages=[create_cached_human_message(message_blocks)],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    logging_helpers.log_agent_packet(role, "summary", result)
    summary = (result.get("agent_response") or "").strip()
    if not summary:
        return None
    if size != "":
        summary = f"{summary} | size:{size}"
    return summary

async def summarize_user_attachments_for_turn_log(
        *,
        svc: Any,
        user_text: str,
        user_attachments: List[Dict[str, Any]],
        max_ctx_chars: int = 12000,
        max_tokens: int = 600,
) -> List[Dict[str, Any]]:
    items = list(user_attachments or [])
    if not items:
        return []

    ensure_attachment_artifact_names(items)

    total_chars = len(user_text or "")
    total_chars += sum(len(a.get("text") or "") for a in items if isinstance(a, dict))
    include_context = total_chars <= max_ctx_chars

    prompt = user_text if include_context else ""
    peers = items if include_context else []

    for a in items:
        if not isinstance(a, dict):
            continue
        if a.get("summary"):
            continue
        summary = await summarize_user_attachment(
            svc=svc,
            attachment=a,
            user_prompt=prompt,
            other_attachments=peers,
            max_tokens=max_tokens,
        )
        if summary:
            a["summary"] = summary

    ensure_attachment_artifact_names(items)
    return items
