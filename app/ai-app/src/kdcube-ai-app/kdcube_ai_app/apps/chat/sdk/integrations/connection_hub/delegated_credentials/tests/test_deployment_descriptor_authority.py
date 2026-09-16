# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from pathlib import Path

import yaml

from connection_hub.delegated_credentials.access_map import (
    build_delegated_access_map,
)


DELEGABLE_PLATFORM_ROLES = [
    "kdcube:role:registered",
    "kdcube:role:paid",
    "kdcube:role:privileged",
    "kdcube:role:super-admin",
]


def _deployment_descriptor() -> dict:
    for parent in Path(__file__).resolve().parents:
        path = parent / "deployment" / "bundles.yaml"
        if path.is_file():
            return yaml.safe_load(path.read_text(encoding="utf-8"))
    raise AssertionError("ai-app deployment/bundles.yaml not found")


def _connection_hub_oauth() -> dict:
    bundles = (_deployment_descriptor().get("bundles") or {}).get("items") or []
    bundle = next(item for item in bundles if item.get("id") == "connection-hub@1-0")
    return bundle["config"]["connections"]["delegated_credentials"]["oauth"]


def test_default_descriptor_uses_one_role_ceiling_for_application_downscoping() -> None:
    oauth = _connection_hub_oauth()
    capabilities = {
        capability["grant"]: capability
        for capability in oauth["capabilities"]
    }
    all_applications = next(
        resource for resource in oauth["resources"] if resource.get("resource") == "*"
    )

    assert all_applications["admin_only"] is True
    assert all_applications["grants"] == ["kdcube:role:super-admin"]
    assert capabilities["kdcube:role:registered"]["delegable_roles"] == (
        DELEGABLE_PLATFORM_ROLES
    )
    assert capabilities["kdcube:role:paid"]["delegable_roles"] == (
        DELEGABLE_PLATFORM_ROLES[1:]
    )
    assert capabilities["kdcube:role:privileged"]["delegable_roles"] == (
        DELEGABLE_PLATFORM_ROLES[2:]
    )
    assert capabilities["kdcube:role:super-admin"]["delegable_roles"] == [
        "kdcube:role:super-admin"
    ]

    access_map = build_delegated_access_map(
        {"delegated_credentials": {"oauth": oauth}}
    )
    all_applications_view = next(
        resource
        for resource in access_map["resources"]
        if resource.get("resource") == "*"
    )
    assert all_applications_view["grant_union"] == ["kdcube:role:super-admin"]
    assert {
        grant["grant"]: grant["delegable_roles"]
        for grant in access_map["grants"]
        if grant["grant"] in DELEGABLE_PLATFORM_ROLES
    }["kdcube:role:registered"] == DELEGABLE_PLATFORM_ROLES
