# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Trusted deployment composition for the common scoped runtime-secret API.

The mounted assembly descriptor selects custody roots and namespaces. Only
the default policy is supplied by the temporary, per-launch service overlay.
Ordinary door credentials, request data and health responses grant nothing.
No store is opened during import or before HTTP scope authorization.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import FastAPI
import yaml

from kdcube_ai_app.infra.secrets.runtime_contract import (
    RuntimeScopePolicy, runtime_section_namespaces, valid_namespace,
)
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore
from kdcube_ai_app.infra.secrets.runtime_http import install_runtime_routes
from kdcube_ai_app.infra.secrets.runtime_vault import RuntimeVaultStore


def runtime_configuration(environ: Mapping[str, str]) -> tuple[tuple[str, ...], str, str]:
    """Read trusted composition; missing or malformed descriptors grant nothing.

    Old namespace/defaulted env projections are deliberately ignored. A declared
    policy wins over env; explicit namespaces without a policy stay closed.
    """
    try:
        path = Path(environ.get("ASSEMBLY_YAML_DESCRIPTOR_PATH") or "/config/assembly.yaml")
        assembly = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(assembly, dict):
            return (), "", ""
        secrets = assembly.get("secrets", {})
        if not isinstance(secrets, dict):
            return (), "", ""
        section = secrets.get("runtime")
        namespaces = runtime_section_namespaces(section)
        if (len(namespaces) > 64 or any(not valid_namespace(value) for value in namespaces)
                or len(set(namespaces)) != len(namespaces)):
            return (), "", ""
        runtime = section if isinstance(section, dict) else {}
        root = runtime.get("root") or "/config/secrets"
        if type(root) is not str or not Path(root).is_absolute():
            return (), "", ""
        if "scope_policy" in runtime:
            policy = json.dumps(runtime["scope_policy"], allow_nan=False)
        elif section is None or (isinstance(section, dict) and section.get("namespaces") is None):
            policy = environ.get("KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY", "")
        else:
            policy = ""
        return namespaces, root, policy
    except (OSError, yaml.YAMLError, ValueError, TypeError, UnicodeError, RecursionError):
        return (), "", ""


def install_configured_runtime_routes(
    app: FastAPI, *, environ: Mapping[str, str],
    broker_factory: Callable | None = None, application: str = "kdcube-runtime",
) -> None:
    """Bind the selected server topology, never a caller-selected backend.

    The file server uses its dedicated configured persistent root. The native
    broker server supplies its existing authenticated broker. Every operation
    requires fresh storage qualification, not just the qualification endpoint.
    This is source wiring, not proof of installed volumes or IAM boundaries.
    """
    namespaces, root, raw_policy = runtime_configuration(environ)
    ordinary_path = environ.get("SECRETS_STORE_PATH", "/run/kdcube-secrets/store.json")
    policy = RuntimeScopePolicy(raw_policy)

    def factory(namespace: str):
        if namespace not in namespaces:
            raise RuntimeError("runtime_secret_scope_forbidden")
        if broker_factory is not None:
            store = RuntimeVaultStore(broker=broker_factory(), application=application,
                                      namespace=namespace, authorized_namespaces=namespaces)
        else:
            if not root or Path(root).resolve() in Path(ordinary_path).resolve().parents:
                raise RuntimeError("runtime_secret_storage_unavailable")
            store = RuntimeFileStore(root=root, namespace=namespace,
                                     authorized_namespaces=namespaces)
        # The common boundary performs fresh qualification after authorized
        # construction and before every record operation, sync or async.
        return store

    install_runtime_routes(app, policy=policy, store_factory=factory)
