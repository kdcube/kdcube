# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# ops/deployment/sql/deploy_project.py
from kdcube_ai_app.ops.deployment.sql.db_deployment import (run as provision, SYSTEM_COMPONENT, SYSTEM_SCHEMA,
                                                            PROJECT_COMPONENT, CONTROL_PLANE_COMPONENT)
from kdcube_ai_app.apps.chat.sdk.config import get_settings

def ensure_control_plane():
    """
    Ensure the control_plane schema is deployed (global, not tenant/project-specific).
    This should be called once before any project deployments.
    """
    provision("deploy", CONTROL_PLANE_COMPONENT, app="control_plane")

def deprovision_control_plane():
    provision("delete", CONTROL_PLANE_COMPONENT, app="control_plane")

def step_provision(tenant, project, app: str = "knowledge_base"):
    """
    Deploy a project-specific schema for a given tenant/project.
    Automatically ensures control_plane is deployed first.
    """
    # Always ensure control plane exists first
    ensure_control_plane()

    # Then deploy project-specific components
    # provision("deploy", SYSTEM_COMPONENT)  # Uncomment if needed
    provision("deploy", PROJECT_COMPONENT, tenant, project.replace("-", "_"), app)


def step_deprovision(tenant, project, app: str = "knowledge_base"):
    """
    Remove a project-specific schema.
    Note: Does NOT touch control_plane (shared across all projects).
    """
    provision("delete", PROJECT_COMPONENT, tenant, project.replace("-", "_"), app)


if __name__ == "__main__":
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    import os
    # One-shot schema deployment can legitimately run well past the generic
    # runtime DB statement timeout due to index creation on existing tables.
    # Keep it overrideable, but default this job to no timeout.
    os.environ.setdefault("POSTGRES_STATEMENT_TIMEOUT_MS", "0")
    if not os.environ.get("POSTGRES_PORT"):
        os.environ["POSTGRES_PORT"] = "5436"
    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT

    # Deploy control plane first (idempotent)
    ensure_control_plane()
    # deprovision_control_plane()

    # Deploy project schemas
    step_provision(tenant, project, "chatbot")
    step_provision(tenant, project, "knowledge_base")
    # step_deprovision(tenant, project, "knowledge_base")
