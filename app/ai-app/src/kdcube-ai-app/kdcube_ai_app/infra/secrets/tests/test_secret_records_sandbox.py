# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W670: isolated execution receives the folder-stored per-user and app secrets with the yaml copies' scope."""
from __future__ import annotations

import json
import os

import yaml

from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, SecretsManagerConfig
from kdcube_ai_app.infra.secrets.tests.test_user_secret_files import (
    HUB, OTHER, USER, _app_key, _key, _manager, _root,
)
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore


def test_isolated_execution_receives_the_folder_records_with_the_yaml_copies_scope(tmp_path, monkeypatch):
    import base64

    from kdcube_ai_app.apps.chat.sdk.runtime.isolated import py_code_exec_entry
    from kdcube_ai_app.infra.config import platform_env
    from kdcube_ai_app.infra.secrets import manager as manager_module

    bundle_yaml = tmp_path / "config" / "bundles.secrets.yaml"
    host = _manager(tmp_path, bundle_secrets_yaml=bundle_yaml.as_uri())
    store = UserSecretFileStore(root=_root(tmp_path))
    store.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-app")
    store.set_app(bundle_id=OTHER, key="k", value="synthetic-other-app")
    store.set(user_id=USER, bundle_id=HUB, key="token", value="synthetic-user")
    monkeypatch.setattr(manager_module, "get_secrets_manager", lambda *args, **kwargs: host)
    exported = {"KDCUBE_RUNTIME_SECRETS_YAML_B64": "x", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64": "x"}
    payload = platform_env._secret_records_payload(exported, bundle_id=HUB, descriptor_payload_scope="active_bundle")
    records = json.loads(base64.b64decode(payload))["records"]
    assert records == {_app_key(): "synthetic-app", _key(key="token"): "synthetic-user"}  # other bundle excluded
    everything = json.loads(base64.b64decode(platform_env._secret_records_payload(
        exported, bundle_id=None, descriptor_payload_scope=None)))["records"]
    assert _app_key(bundle=OTHER, key="k") in everything
    assert platform_env._secret_records_payload({}, bundle_id=HUB, descriptor_payload_scope=None) is None

    runtime_dir = tmp_path / "sandbox"
    runtime_dir.mkdir(mode=0o700)
    (runtime_dir / "secrets.yaml").write_text("platform: {}\n")
    (runtime_dir / "assembly.yaml").write_text(yaml.safe_dump(
        {"secrets": {"runtime": {"root": "/config/secrets", "namespaces": ["login-attempts"]}}}))
    monkeypatch.setenv("KDCUBE_RUNTIME_SECRET_RECORDS_B64", payload)
    logged = []
    py_code_exec_entry._materialize_secret_records(runtime_dir, type("L", (), {"log": lambda self, *a: logged.append(a)})())
    assert "KDCUBE_RUNTIME_SECRET_RECORDS_B64" not in os.environ
    assert yaml.safe_load((runtime_dir / "assembly.yaml").read_text())["secrets"]["runtime"]["root"] == str(
        runtime_dir / "secrets")
    sandbox = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="exec", global_secrets_yaml=(runtime_dir / "secrets.yaml").as_uri(),
        runtime_secrets_root=str(runtime_dir / "secrets")))
    import asyncio

    assert asyncio.run(sandbox.get_secret(_app_key())) == "synthetic-app"
    assert asyncio.run(sandbox.get_secret(_key(key="token"))) == "synthetic-user"
    assert not any("synthetic-" in str(entry) for entry in logged)
