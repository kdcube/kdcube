# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Trusted deployment composition for the common scoped runtime-secret API.

Only generated service inputs select custody roots and namespace grants.
Ordinary door credentials, request data and health responses grant nothing.
No store is opened during import or before HTTP scope authorization.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import FastAPI

from kdcube_ai_app.infra.secrets.runtime_contract import RuntimeScopePolicy, valid_namespace
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore
from kdcube_ai_app.infra.secrets.runtime_http import install_runtime_routes
from kdcube_ai_app.infra.secrets.runtime_vault import RuntimeVaultStore


def _namespaces(raw: str | None) -> tuple[str, ...]:
    try:
        if type(raw) is not str or len(raw.encode("utf-8")) > 8192:
            return ()
        payload = json.loads(raw)
        if (type(payload) is not list or len(payload) > 64
                or any(not valid_namespace(value) for value in payload)
                or len(set(payload)) != len(payload)):
            return ()
        return tuple(payload)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return ()


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
    namespaces = _namespaces(environ.get("KDCUBE_SECRETS_RUNTIME_NAMESPACES"))
    root = environ.get("KDCUBE_SECRETS_RUNTIME_ROOT")
    ordinary_path = environ.get("SECRETS_STORE_PATH", "/run/kdcube-secrets/store.json")
    policy = RuntimeScopePolicy(environ.get("KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"))

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
        if store.qualify() is not None:
            raise RuntimeError("runtime_secret_storage_unavailable")
        return store

    install_runtime_routes(app, policy=policy, store_factory=factory)
