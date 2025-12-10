# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/portable_spec.py
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List
import json, os

@dataclass
class ModelConfigSpec:
    # mirrors ConfigRequest fields you already have
    openai_api_key: Optional[str] = None
    claude_api_key: Optional[str] = None
    selected_model: Optional[str] = None
    selected_embedder: str = "openai-text-embedding-3-small"
    custom_embedding_endpoint: Optional[str] = None
    custom_embedding_model: Optional[str] = "sentence-transformers/all-MiniLM-L6-v2"
    custom_embedding_size: Optional[int] = 384
    format_fix_enabled: bool = True
    role_models: Optional[Dict[str, Dict[str, str]]] = None
    custom_model_endpoint: Optional[str] = None
    custom_model_api_key: Optional[str] = None
    custom_model_name: Optional[str] = None
    kb_search_endpoint: Optional[str] = None
    agentic_bundle_id: Optional[str] = None
    bundle_storage_url: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None

@dataclass
class CommSpec:
    # produced by ChatCommunicator._export_comm_spec_for_runtime()
    channel: str
    service: Dict[str, Any]
    conversation: Dict[str, Any]
    room: Optional[str] = None
    target_sid: Optional[str] = None
    user_id: Optional[str] = None
    user_type: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None

@dataclass
class IntegrationsSpec:
    # whatever your tools expect (e.g. ctx RAG client endpoint/keys)
    ctx_client: Optional[Dict[str, Any]] = None

@dataclass
class PortableSpec:
    model_config: ModelConfigSpec
    comm: Optional[CommSpec] = None
    integrations: Optional[IntegrationsSpec] = None
    cv_snapshot: Optional[dict] = None
    env_passthrough: Dict[str, str] = field(default_factory=dict)  # minimal set of env you want copied
    contextvars: Optional[dict] = None  # snapshot of other contextvars you want restored
    accounting_storage: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "PortableSpec":
        d = json.loads(s)
        d["model_config"] = ModelConfigSpec(**d["model_config"])
        d["comm"] = CommSpec(**d["comm"]) if d.get("comm") else None
        d["integrations"] = IntegrationsSpec(**d["integrations"]) if d.get("integrations") else None
        return PortableSpec(**d)
