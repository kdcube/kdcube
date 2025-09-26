# kdcube_ai_app/apps/knowledge_base/modules/enrichment.py
from __future__ import annotations
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, ConfigRequest, create_workflow_config

class SegmentMetadata(BaseModel):
    summary: str = Field(description="1-2 sentence summary.")
    keywords: List[str] = Field(description="5-7 key topics.")
    hypothetical_questions: List[str] = Field(description="3-5 questions the chunk could answer.")
    table_summary: Optional[str] = Field(default=None, description="Only if the chunk is a table.")


def create_embedding_text(meta: SegmentMetadata, content: str) -> str:
    kws = ", ".join(meta.keywords or [])
    return f"Summary: {meta.summary}\nKeywords: {kws}\nContent: {content[:1000]}\n"

def _build_prompt(chunk_text: str, is_table: bool = False, is_image: bool = False) -> Tuple[str, str]:
    sys = (
        "You are an expert in data documenting and indexing. Return STRICT JSON with this json schema: "
        f"{SegmentMetadata.model_json_schema()}"
        "No commentary. No markdown fences."
    )
    image = "This chunk is a DIAGRAM or an IMAGE. Summarize infographics.\n" if is_table else ""
    table = "This chunk is a TABLE. Summarize insights.\n" if is_table else ""

    usr = f"{table}{image}Chunk:\n---\n{chunk_text[:3000]}\n---\nReturn ONLY JSON."
    return sys, usr

class EnrichmentModule(ProcessingModule):
    @property
    def stage_name(self) -> str:
        return "enrichment"

    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs) -> Dict[str, Any]:
        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Enrichment already exists for {resource_id} v{version}, skipping")
            return self.get_results(resource_id, version) or {}

        # model service
        ms: Optional[ModelServiceBase] = kwargs.get("model_service")
        role = kwargs.get("role_name") or "segment_enrichment"
        if not ms:
            req = ConfigRequest(
                openai_api_key=kwargs.get("openai_api_key"),
                claude_api_key=kwargs.get("claude_api_key"),
                selected_model=kwargs.get("selected_model") or "claude-3-7-sonnet-20250219",
                role_models={role: kwargs.get("segment_enrichment_role",
                                              {"provider": "anthropic", "model": "claude-3-7-sonnet-20250219"})},
            )
            ms = ModelServiceBase(create_workflow_config(req))

        segmentation_module = self.pipeline.get_module("segmentation")
        all_segments = segmentation_module.get_segments_by_type(resource_id, version, SegmentType.RETRIEVAL)
        print(json.dumps(all_segments, indent=2))

        ok = 0
        total = len(all_segments)
        for segment in all_segments:

            segment_id = segment["segment_id"]
            content = segment["text"]
            if content.startswith("# Untitled"):
                content = content[len("# Untitled"):].lstrip()
            heading = (segment.get("metadata") or {}).get("heading", "").strip()
            subheading = (segment.get("metadata") or {}).get("subheading", "").strip()
            title = heading or subheading or ""
            if not content.startswith(f"# {title}"):
                content = f"# {title}\n\n{content}"
            is_table = segment.get("metadata", {}).get("is_table", False)

            sys, usr = _build_prompt(content, is_table)
            client = ms.get_client(role)
            cfg = ms.describe_client(client, role=role)
            # client = ms.get_client(role, temperature=0.0)
            async def on_delta(*_a, **_k):
                self.logger.debug(f"Delta: {_a} {_k}")

            res = await ms.stream_model_text_tracked(role="segment_enrichment",
                                                     client=client,
                                                     messages=[SystemMessage(content=sys), HumanMessage(content=usr)],
                                                     temperature=0.0,
                                                     client_cfg=cfg,
                                                     on_delta=on_delta)
            raw = res.get("text") or ""

            parsed = None
            try:
                parsed = SegmentMetadata.model_validate_json(raw)
            except Exception:
                fix = await ms.format_fixer.fix_format(raw_output=raw, expected_format="ChunkMetadata", input_data=usr, system_prompt=sys)
                if fix.get("success"):
                    try:
                        parsed = SegmentMetadata.model_validate(fix["data"])
                    except Exception:
                        parsed = None

            payload = {
                "segment_id": segment_id,
                "success": bool(parsed),
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}:segment:{segment_id}"
            }
            if parsed:
                ok += 1
                payload.update({
                    "metadata": parsed.model_dump(),
                    "is_table": is_table,
                    "embedding_text": create_embedding_text(parsed, content),
                    "retrieval_doc": self._compose_retrieval_doc(parsed, is_table, content),
                })
            else:
                payload["error"] = "Could not parse model output into ChunkMetadata"

            self._save_json(self.stage_name, resource_id, version, f"segment_{segment_id}_enrichment.json", payload)

        summary = {
            "resource_id": resource_id,
            "version": version,
            "segments_total": total,
            "segments_enriched": ok,
            "segments_failed": total - ok,
            "timestamp": datetime.now().isoformat(),
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
        }
        self.save_results(resource_id, version, summary)
        return summary


    def _compose_retrieval_doc(self,
                               meta: SegmentMetadata,
                               is_table: bool,
                               content: str,
                               content_max_sym: int = -1) -> str:
        # lines = [
        #     f"Summary: {meta.summary}",
        #     "Keywords: " + ", ".join(meta.keywords or [])
        # ]
        lines = []
        if is_table and meta.table_summary:
            lines.append(f"Table summary: {meta.table_summary}")
            lines.append("Content:")
        # Handle None or negative values to mean "no truncation"
        if content_max_sym is None or content_max_sym < 0:
            lines.append(content)
        else:
            lines.append(content[:content_max_sym])
        return "\n".join(lines)

    def _read_json(self, stage: str, rid: str, ver: str, name: str, subfolder: Optional[str] = None):
        s = self.storage.get_stage_content(stage, rid, ver, name, subfolder=subfolder, as_text=True)
        return json.loads(s) if s else None

    def _save_json(self, stage: str, rid: str, ver: str, name: str, obj: Dict[str, Any], subfolder: Optional[str] = None):
        self.storage.save_stage_content(stage, rid, ver, name, json.dumps(obj, indent=2), subfolder=subfolder)
