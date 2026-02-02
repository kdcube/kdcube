# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import ingest_user_attachments, attachment_blocks
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.infra.service_hub.inventory import Config, create_cached_system_message, create_cached_human_message
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError

BUNDLE_ID = "example.streaming_and_storage"


def _attachment_artifacts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        out.append({
            "mime": (a.get("mime") or a.get("mime_type") or "").strip() or None,
            "filename": (a.get("filename") or "").strip() or None,
            "size_bytes": a.get("size_bytes") or a.get("size"),
            "path": a.get("path") or a.get("key") or a.get("source_path") or None,
            "hosted_uri": a.get("hosted_uri"),
            "summary": a.get("summary"),
        })
    return out


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class StreamingStorageEntrypoint(BaseEntrypoint):
    """Minimal bundle that demonstrates streaming + bundle storage."""

    BUNDLE_ID = BUNDLE_ID

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.update({
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
        })
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        emit = AIBEmitters(self.comm)
        tenant = state.get("tenant")
        project = state.get("project")
        user = state.get("user")
        conversation_id = state.get("conversation_id")
        turn_id = state.get("turn_id")
        user_text = (state.get("text") or "").strip()

        store = AIBundleStorage(tenant=tenant, project=project, ai_bundle_id=BUNDLE_ID)
        conv_store = ConversationStore(self.settings.STORAGE_PATH)

        attachments = await ingest_user_attachments(
            attachments=state.get("attachments") or [],
            store=conv_store,
        )

        prompt_md = user_text or "(empty prompt)"
        prompt_path = f"turns/{turn_id}/prompt.md"
        turn_path = f"turns/{turn_id}/turn.json"
        store.write(prompt_path, prompt_md, mime="text/markdown")

        system_msg = create_cached_system_message([
            {
                "type": "text",
                "text": (
                    "You are a streaming demo assistant. Output ONLY channel-tagged content.\n\n"
                    "Required output protocol:\n"
                    "<channel:thinking>...brief private thoughts...</channel:thinking>\n"
                    "<channel:answer>...final answer for the user...</channel:answer>\n\n"
                    "Keep the answer short and acknowledge the user input."  # minimal demo
                ),
                "cache": True,
            }
        ])

        user_blocks: List[Dict[str, Any]] = [{"type": "text", "text": user_text, "cache": True}]
        user_blocks += attachment_blocks(
            attachments,
            include_summary_text=True,
            include_text=False,
            include_modal=True,
        )
        user_msg = create_cached_human_message(user_blocks)

        channels = [
            ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
            ChannelSpec(name="answer", format="markdown", replace_citations=False, emit_marker="answer"),
        ]

        async def _emit_delta(**kwargs):
            # AIBEmitters.delta does not accept "channel"; it routes by marker.
            kwargs.pop("channel", None)
            await emit.delta(**kwargs)

        start_ts = datetime.datetime.utcnow().isoformat() + "Z"

        await emit.step(step="streaming", status="started", title="Streaming demo")
        try:
            results, meta = await stream_with_channels(
                self.models_service,
                messages=[system_msg, user_msg],
                role="answer.generator.simple",
                channels=channels,
                emit=_emit_delta,
                agent="answer.generator.simple",
                artifact_name="assistant.answer",
                max_tokens=800,
                temperature=0.3,
                return_full_raw=True,
            )
            service_error = (meta or {}).get("service_error")
            if service_error:
                raise ServiceException(ServiceError.model_validate(service_error))
        except Exception as exc:
            await emit.error(message=str(exc), agent="answer.generator.simple")
            raise
        finally:
            await emit.step(step="streaming", status="completed", title="Streaming demo")

        turn_payload = {
            "ts": start_ts,
            "request_id": state.get("request_id"),
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "user": {
                "id": user,
                "prompt": {"mime": "text/markdown", "path": prompt_path},
                "attachments": _attachment_artifacts(attachments),
            },
            "assistant": {
                "thinking": (results.get("thinking").raw if results.get("thinking") else ""),
                "answer": (results.get("answer").raw if results.get("answer") else ""),
            },
        }

        store.write(turn_path, json.dumps(turn_payload, indent=2, ensure_ascii=True), mime="application/json")

        return {
            "answer": turn_payload["assistant"]["answer"],
            "turn_artifact": turn_path,
        }
