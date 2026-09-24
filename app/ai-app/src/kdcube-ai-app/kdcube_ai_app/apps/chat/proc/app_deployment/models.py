# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeployedWidgetSurface(BaseModel):
    alias: str
    method_name: str
    icon: dict[str, str] = Field(default_factory=dict)
    user_types: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    auth: dict[str, Any] | None = None
    enabled: bool = True
    static: bool = False
    artifact_relpath: str | None = None
    build_signature: str | None = None


class AppStaticSurfaceManifest(BaseModel):
    schema_version: int = 1
    tenant: str
    project: str
    bundle_id: str
    source_generation: str
    application_generation: str | None = None
    props_fingerprint: str
    deployment_signature: str
    generated_at: str
    source: dict[str, Any] = Field(default_factory=dict)
    bundle_enabled: bool = True
    bundle_allowed_roles: list[str] = Field(default_factory=list)
    widgets: dict[str, DeployedWidgetSurface] = Field(default_factory=dict)
