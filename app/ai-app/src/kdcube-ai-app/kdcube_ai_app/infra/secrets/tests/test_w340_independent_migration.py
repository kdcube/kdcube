"""Independent checks of the composed #340 (app + per-user secrets in folders, migration, sandbox payload).

Real migration CLI (subprocess) on a copy of a realistic config folder with BOTH yamls, then read-back through
the secrets-file manager's get_secret, then the isolated-exec records payload per scope and its sandbox round trip.
Synthetic values only.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, SecretsManagerConfig

USER_A, USER_B = "4f2b9c0e-user-a", "google-oauth2|1234567890"
PB, HUB, WS = "problem-board@1-0", "connection-hub@1-0", "workspace@2026-03-31-13-36"

GLOBAL = {
    "platform": {"services": {"openai": {"api_key": "synthetic-openai"},
                              "session_token": {"secret": "synthetic-session"}}},
    "users": {
        USER_A: {"bundles": {HUB: {"secrets": {"oauth": {"google": {"refresh_token": "synthetic-ua-google"}}}},
                             PB: {"secrets": {"pref": "synthetic-ua-pb"}}}},
        USER_B: {"bundles": {HUB: {"secrets": {"token": "synthetic-ub-hub"}}}},
    },
}
BUNDLES = {"bundles": {"version": "1", "items": [
    {"id": PB, "secrets": {"connection_hub": {"peer_proof_secret": "synthetic-pb-proof-" + "p" * 40,
                                              "peer_receipt_secret": "synthetic-pb-receipt-" + "r" * 40}}},
    {"id": HUB, "secrets": {"connections": {"oauth_state_secret": "synthetic-hub-state",
                                            "card_transactions": {"problem_board_receipt_secret": "synthetic-hub-rcpt"}}}},
    {"id": WS, "props_note": "kept", "secrets": {"telemetry_sink": {"auth": {"token": "synthetic-ws-telemetry"}}}},
]}}

EXPECTED_USER = {
    f"users.{USER_A}.bundles.{HUB}.secrets.oauth.google.refresh_token": "synthetic-ua-google",
    f"users.{USER_A}.bundles.{PB}.secrets.pref": "synthetic-ua-pb",
    f"users.{USER_B}.bundles.{HUB}.secrets.token": "synthetic-ub-hub",
}
EXPECTED_APP = {
    f"bundles.{PB}.secrets.connection_hub.peer_proof_secret": "synthetic-pb-proof-" + "p" * 40,
    f"bundles.{PB}.secrets.connection_hub.peer_receipt_secret": "synthetic-pb-receipt-" + "r" * 40,
    f"bundles.{HUB}.secrets.connections.oauth_state_secret": "synthetic-hub-state",
    f"bundles.{HUB}.secrets.connections.card_transactions.problem_board_receipt_secret": "synthetic-hub-rcpt",
    f"bundles.{WS}.secrets.telemetry_sink.auth.token": "synthetic-ws-telemetry",
}


def _config(tmp_path) -> Path:
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    (config / "secrets.yaml").write_text(yaml.safe_dump(GLOBAL, sort_keys=False))
    (config / "bundles.secrets.yaml").write_text(yaml.safe_dump(BUNDLES, sort_keys=False))
    return config


def _cli(config: Path, *extra: str) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, "-m", "kdcube_ai_app.infra.secrets.user_secret_files", "migrate",
                             "--config-dir", str(config), *extra], capture_output=True, text=True,
                            env={**os.environ}, timeout=120)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    return result.returncode, (json.loads(lines[-1]) if lines else {"stderr": result.stderr[-400:]})


def _manager(config: Path) -> SecretsFileSecretsManager:
    return SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc", global_secrets_yaml=(config / "secrets.yaml").as_uri(),
        bundle_secrets_yaml=(config / "bundles.secrets.yaml").as_uri()))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_A_dry_run_changes_nothing_and_counts(tmp_path):
    config = _config(tmp_path)
    before = {name: _digest(config / name) for name in ("secrets.yaml", "bundles.secrets.yaml")}
    code, out = _cli(config, "--dry-run")
    assert code == 0 and out["status"] == "ok" and out["dry_run"] is True, out
    assert out.get("user_found") == len(EXPECTED_USER) and out.get("app_found") == len(EXPECTED_APP), out
    assert {name: _digest(config / name) for name in before} == before
    assert not any((config / "secrets").rglob("*.json")) if (config / "secrets").exists() else True
    text = json.dumps(out)
    assert "synthetic-" not in text


def test_B_apply_moves_both_yamls_keeps_platform_and_item_fields(tmp_path):
    config = _config(tmp_path)
    code, out = _cli(config)
    assert code == 0 and out["status"] == "ok" and out["dry_run"] is False, out
    assert "synthetic-" not in json.dumps(out)
    global_after = yaml.safe_load((config / "secrets.yaml").read_text())
    assert global_after["platform"] == GLOBAL["platform"] and "users" not in global_after
    items = yaml.safe_load((config / "bundles.secrets.yaml").read_text())["bundles"]["items"]
    assert [item["id"] for item in items] == [PB, HUB, WS]
    assert all("secrets" not in item or not item["secrets"] for item in items)
    assert next(item for item in items if item["id"] == WS)["props_note"] == "kept"
    root = config / "secrets"
    files = list(root.rglob("*.json"))
    assert len(files) == len(EXPECTED_USER) + len(EXPECTED_APP)
    for path in files + [p for p in root.rglob("*") if p.is_dir()] + [root]:
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert mode == (0o600 if path.is_file() else 0o700), (path, oct(mode))
    assert "synthetic-" not in "\n".join(str(p) for p in files)  # values never in names


def test_C_get_secret_reads_back_every_value_from_the_folder(tmp_path):
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    manager = _manager(config)
    for key, value in {**EXPECTED_USER, **EXPECTED_APP,
                       "platform.services.openai.api_key": "synthetic-openai"}.items():
        assert asyncio.run(manager.get_secret(key)) == value, key


def test_D_rerun_is_idempotent(tmp_path):
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    snapshot = {p: p.read_bytes() for p in (config / "secrets").rglob("*.json")}
    code, out = _cli(config)
    assert code == 0 and out["status"] == "ok", out
    assert {p: p.read_bytes() for p in (config / "secrets").rglob("*.json")} == snapshot
    manager = _manager(config)
    assert asyncio.run(manager.get_secret(next(iter(EXPECTED_APP)))) == next(iter(EXPECTED_APP.values()))


def _payload(monkeypatch, config, exported, bundle_id, scope):
    from kdcube_ai_app.infra.config import platform_env
    from kdcube_ai_app.infra.secrets import manager as manager_module
    host = _manager(config)
    calls = []

    def configured(*args, **kwargs):
        calls.append(bool(args or kwargs))
        return host
    monkeypatch.setattr(manager_module, "get_secrets_manager", configured)
    raw = platform_env._secret_records_payload(exported, bundle_id=bundle_id, descriptor_payload_scope=scope)
    assert all(calls), "host asked for the manager without settings"  # no call at all when nothing is exported
    return None if raw is None else json.loads(base64.b64decode(raw))["records"]


BOTH = {"KDCUBE_RUNTIME_SECRETS_YAML_B64": "x", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64": "x"}


def test_E1_unscoped_payload_carries_all_users_and_all_apps(tmp_path, monkeypatch):
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    assert _payload(monkeypatch, config, BOTH, None, None) == {**EXPECTED_USER, **EXPECTED_APP}


def test_E2_active_bundle_payload_carries_all_users_and_only_that_bundles_app_secrets(tmp_path, monkeypatch):
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    records = _payload(monkeypatch, config, BOTH, PB, "active_bundle")
    assert records == {**EXPECTED_USER, **{k: v for k, v in EXPECTED_APP.items() if k.startswith(f"bundles.{PB}.")}}


def test_E3_payload_follows_which_yaml_copies_go(tmp_path, monkeypatch):
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    assert _payload(monkeypatch, config, {"KDCUBE_RUNTIME_SECRETS_YAML_B64": "x"}, None, None) == EXPECTED_USER
    assert _payload(monkeypatch, config, {"KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64": "x"}, None, None) == EXPECTED_APP
    assert _payload(monkeypatch, config, {}, None, None) is None


def test_E4_sandbox_round_trip_reads_the_same_values(tmp_path, monkeypatch):
    from kdcube_ai_app.infra.config import platform_env
    from kdcube_ai_app.apps.chat.sdk.runtime.isolated import py_code_exec_entry
    config = _config(tmp_path)
    assert _cli(config)[0] == 0
    _payload(monkeypatch, config, BOTH, PB, "active_bundle")
    raw = platform_env._secret_records_payload(BOTH, bundle_id=PB, descriptor_payload_scope="active_bundle")
    runtime_dir = tmp_path / "sandbox"
    runtime_dir.mkdir(mode=0o700)
    (runtime_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": GLOBAL["platform"]}))
    (runtime_dir / "bundles.secrets.yaml").write_text(yaml.safe_dump({"bundles": {"version": "1", "items": []}}))
    monkeypatch.setenv("KDCUBE_RUNTIME_SECRET_RECORDS_B64", raw)
    logged = []
    py_code_exec_entry._materialize_secret_records(runtime_dir, type("L", (), {"log": lambda self, *a: logged.append(a)})())
    sandbox = _manager(runtime_dir)  # default root: <folder of the materialized yaml>/secrets
    expected = {**EXPECTED_USER, **{k: v for k, v in EXPECTED_APP.items() if k.startswith(f"bundles.{PB}.")}}
    for key, value in expected.items():
        assert asyncio.run(sandbox.get_secret(key)) == value, key
    assert asyncio.run(sandbox.get_secret(f"bundles.{HUB}.secrets.connections.oauth_state_secret")) is None
    assert "KDCUBE_RUNTIME_SECRET_RECORDS_B64" not in os.environ
    assert "synthetic-" not in str(logged)


def test_F_conflicting_destination_refuses_before_any_change(tmp_path):
    config = _config(tmp_path)
    key = f"bundles.{PB}.secrets.connection_hub.peer_proof_secret"
    manager = _manager(config)
    asyncio.run(manager.set_secret(key, "a-different-existing-value-" + "z" * 20))
    before = {name: _digest(config / name) for name in ("secrets.yaml", "bundles.secrets.yaml")}
    code, out = _cli(config)
    assert code != 0 and out["status"] == "refused", out
    assert {name: _digest(config / name) for name in before} == before
    assert "synthetic-" not in json.dumps(out) and "a-different" not in json.dumps(out)


def test_G_bundle_less_user_leaf_refuses_before_any_change(tmp_path):
    config = _config(tmp_path)
    data = yaml.safe_load((config / "secrets.yaml").read_text())
    data["users"][USER_A]["secrets"] = {"loose": "synthetic-loose"}
    (config / "secrets.yaml").write_text(yaml.safe_dump(data))
    before = {name: _digest(config / name) for name in ("secrets.yaml", "bundles.secrets.yaml")}
    code, out = _cli(config)
    assert code != 0 and out["status"] == "refused", out
    assert {name: _digest(config / name) for name in before} == before
