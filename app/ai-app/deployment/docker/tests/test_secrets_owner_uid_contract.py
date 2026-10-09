"""W677: every container that touches the secrets folder agrees on one owner uid; chat-proc's entrypoint
(root, before gosu) hands root-owned entries to that owner at every start and leaves other owners alone."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLIES = ("all_in_one_kdcube", "custom-ui-managed-infra")
OWNER_ENV = "KDCUBE_SECRETS_OWNER_UID=${KDCUBE_SECRETS_OWNER_UID:-1000}"


@pytest.mark.parametrize("assembly", ASSEMBLIES)
@pytest.mark.parametrize("service", ["kdcube-secrets", "chat-ingress", "chat-proc"])
def test_every_secrets_container_gets_the_owner_uid(assembly, service):
    services = yaml.safe_load((ROOT / assembly / "docker-compose.yaml").read_text())["services"]
    assert OWNER_ENV in services[service]["environment"]


@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_the_proc_entrypoint_reowns_only_root_owned_entries_before_dropping_privileges(assembly):
    script = (ROOT / assembly / "docker-entrypoint.sh").read_text()
    repair = script.index('SECRETS_ROOT="${KDCUBE_SECRETS_RUNTIME_ROOT:-/config/secrets}"')
    assert repair < script.index('exec gosu "$APPUSER"')
    assert 'SECRETS_OWNER_UID="${KDCUBE_SECRETS_OWNER_UID:-$APPUSER_UID}"' in script
    assert ('find "$SECRETS_ROOT" -xdev -uid 0 ! -type l \\( -type d -o -type f \\) '
            '-exec chown "$SECRETS_OWNER_UID" {} +') in script
    block = script[repair:script.index("\nfi\n", repair)]
    assert "chmod" not in block  # the repair re-owns only; modes are never touched


# Review P1: the startup repair refuses any target that is not an explicit secrets directory, before any
# traversal. The shell block is executed for real with a fake `find` that only records whether it ran.
def _repair_block(assembly):
    script = (ROOT / assembly / "docker-entrypoint.sh").read_text()
    start = script.index('SECRETS_ROOT="${KDCUBE_SECRETS_RUNTIME_ROOT:-/config/secrets}"')
    return script[start:script.index("\nfi\n", script.index('find "$SECRETS_ROOT"')) + 4]


@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_the_shell_repair_runs_only_for_an_explicit_secrets_directory(assembly, tmp_path):
    import os
    import subprocess

    bin_dir, marker = tmp_path / "bin", tmp_path / "find-ran"
    bin_dir.mkdir()
    (bin_dir / "find").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (bin_dir / "find").chmod(0o755)
    good = tmp_path / "config" / "secrets"
    good.mkdir(parents=True)
    (tmp_path / "alias").symlink_to(good)
    cases = {"/": False, "relative/secrets": False, "/etc": False, f"{good}/..": False,
             str(tmp_path / "alias"): False, str(tmp_path / "missing" / "x"): False, f"{good}/": True, str(good): True}
    for target, runs in cases.items():
        marker.unlink(missing_ok=True)
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "KDCUBE_SECRETS_RUNTIME_ROOT": target, "APPUSER_UID": "1000"}
        subprocess.run(["sh", "-c", _repair_block(assembly)], env=env, check=True, timeout=20)
        assert marker.exists() is runs, (assembly, target)


def test_the_python_repair_refuses_unsafe_targets_before_walking(tmp_path, monkeypatch):
    import importlib.util

    path = ROOT / "all_in_one_kdcube" / "secrets" / "secrets_service_entrypoint.py"
    spec = importlib.util.spec_from_file_location("secrets_entrypoint_p1", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    good = tmp_path / "config" / "secrets"
    good.mkdir(parents=True)
    (tmp_path / "alias").symlink_to(good)
    for target in ("/", "", "relative/secrets", "/etc", f"{good}/..", str(tmp_path / "alias"), str(tmp_path / "missing" / "x")):
        assert entry.safe_repair_root(target) is False, target
    assert entry.safe_repair_root(str(good)) and entry.safe_repair_root(f"{good}/")

    def forbidden(*args, **kwargs):
        raise AssertionError("must not traverse or chown")

    monkeypatch.setattr(entry.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entry.os, "walk", forbidden)
    monkeypatch.setattr(entry.os, "chown", forbidden)
    for target in ("/", "/etc", str(tmp_path / "alias")):
        assert entry.adopt_root_owned_entries(target, "1000") == 0
