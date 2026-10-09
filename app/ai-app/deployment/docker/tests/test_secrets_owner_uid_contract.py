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
