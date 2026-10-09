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
def test_the_proc_entrypoint_runs_the_shared_repair_before_dropping_privileges(assembly):
    script = (ROOT / assembly / "docker-entrypoint.sh").read_text()
    call = 'python -m kdcube_ai_app.infra.secrets.ownership_repair "$SECRETS_ROOT" "$SECRETS_OWNER_UID" || true'
    assert call in script and script.index(call) < script.index('exec gosu "$APPUSER"')
    assert 'SECRETS_ROOT="${KDCUBE_SECRETS_RUNTIME_ROOT:-/config/secrets}"' in script
    assert 'SECRETS_OWNER_UID="${KDCUBE_SECRETS_OWNER_UID:-$APPUSER_UID}"' in script
    assert "find " not in script[script.index("# W677"):script.index(call)]  # no path-based shell walk


def test_the_secrets_image_carries_and_runs_the_shared_repair():
    dockerfile = (ROOT / "all_in_one_kdcube" / "Dockerfile_Secrets").read_text()
    assert ("COPY src/kdcube-ai-app/kdcube_ai_app/infra/secrets/ownership_repair.py "
            "/app/kdcube_ai_app/infra/secrets/ownership_repair.py") in dockerfile
    entry = (ROOT / "all_in_one_kdcube" / "secrets" / "secrets_service_entrypoint.py").read_text()
    assert "repair_secrets_ownership" in entry and entry.index("adopt_root_owned_entries()") > 0
