from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import yaml

DOCKER_ROOT = Path(__file__).resolve().parents[1]
AI_APP_ROOT = DOCKER_ROOT.parents[1]
SECRETS_ROOT = DOCKER_ROOT / "all_in_one_kdcube" / "secrets"


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secrets_image_selects_ephemeral_or_host_vault_server(monkeypatch):
    entrypoint = _load_script(
        "test_secrets_service_entrypoint",
        SECRETS_ROOT / "secrets_service_entrypoint.py",
    )

    monkeypatch.delenv("KDCUBE_SECRETS_SERVICE_BACKEND", raising=False)
    assert entrypoint.selected_server_path(None) == "/app/secrets_server.py"
    assert entrypoint.selected_server_path("ephemeral") == "/app/secrets_server.py"
    assert (
        entrypoint.selected_server_path("host_vault")
        == "/app/host_vault/broker_server.py"
    )

    try:
        entrypoint.selected_server_path("unknown")
    except ValueError as exc:
        assert "unsupported secrets service backend" in str(exc)
    else:
        raise AssertionError("unknown backend must fail closed")


def test_secret_injection_supports_stdin_without_a_value_argument(monkeypatch):
    secretsctl = _load_script("test_secretsctl", SECRETS_ROOT / "secretsctl.py")
    calls = []
    monkeypatch.setattr(
        secretsctl,
        "_post_set",
        lambda key, value, **kwargs: calls.append((key, value, kwargs)),
    )
    monkeypatch.setattr(
        secretsctl.sys, "argv", ["secretsctl.py", "set", "fixture.key", "--stdin"]
    )
    monkeypatch.setattr(secretsctl.sys, "stdin", io.StringIO("fixture-secret"))

    assert secretsctl.main() == 0
    assert calls == [("fixture.key", "fixture-secret", {"expected_generation": None})]
    assert "fixture-secret" not in secretsctl.sys.argv


def test_secret_injection_create_only_uses_generation_zero(monkeypatch):
    secretsctl = _load_script("test_secretsctl_create", SECRETS_ROOT / "secretsctl.py")
    calls = []
    monkeypatch.setattr(
        secretsctl,
        "_post_set",
        lambda key, value, **kwargs: calls.append((key, value, kwargs)),
    )
    monkeypatch.setattr(
        secretsctl.sys,
        "argv",
        ["secretsctl.py", "set", "fixture.key", "--stdin", "--if-absent"],
    )
    monkeypatch.setattr(secretsctl.sys, "stdin", io.StringIO("fixture-secret"))

    assert secretsctl.main() == 0
    assert calls == [("fixture.key", "fixture-secret", {"expected_generation": 0})]
    assert "fixture-secret" not in secretsctl.sys.argv


def test_secret_verification_reads_digest_from_stdin(monkeypatch):
    secretsctl = _load_script("test_secretsctl_verify", SECRETS_ROOT / "secretsctl.py")
    digest = "a" * 64
    calls = []
    monkeypatch.setattr(
        secretsctl,
        "_verify",
        lambda key, value: calls.append((key, value)) or "different",
    )
    monkeypatch.setattr(
        secretsctl.sys,
        "argv",
        ["secretsctl.py", "verify", "fixture.key", "--sha256-stdin"],
    )
    monkeypatch.setattr(secretsctl.sys, "stdin", io.StringIO(digest))

    assert secretsctl.main() == 4
    assert calls == [("fixture.key", digest)]
    assert digest not in secretsctl.sys.argv


def test_secretsctl_marks_backend_unavailability_as_transient(monkeypatch):
    secretsctl = _load_script(
        "test_secretsctl_transient",
        SECRETS_ROOT / "secretsctl.py",
    )
    monkeypatch.setattr(
        secretsctl,
        "_verify",
        lambda *_args: (_ for _ in ()).throw(
            urllib.error.HTTPError(
                "http://127.0.0.1:7777/verify",
                503,
                "unavailable",
                {},
                None,
            )
        ),
    )
    monkeypatch.setattr(
        secretsctl.sys,
        "argv",
        ["secretsctl.py", "verify", "fixture.key", "--sha256-stdin"],
    )
    monkeypatch.setattr(secretsctl.sys, "stdin", io.StringIO("a" * 64))

    assert secretsctl.main() == 5


def test_secrets_image_contains_both_backends_and_defaults_to_ephemeral():
    dockerfile = (DOCKER_ROOT / "all_in_one_kdcube" / "Dockerfile_Secrets").read_text(
        encoding="utf-8"
    )

    assert "host-vault-requirements.txt" in dockerfile
    assert "secrets_service_entrypoint.py" in dockerfile
    assert "host_vault/broker_server.py" in dockerfile
    assert "ENV KDCUBE_SECRETS_SERVICE_BACKEND=ephemeral" in dockerfile
    assert "ENV PYTHONPATH=/app" in dockerfile
    assert 'CMD ["python", "/app/secrets_service_entrypoint.py"]' in dockerfile


def test_local_compose_mounts_identity_only_into_secrets_broker():
    for compose_path in (
        DOCKER_ROOT / "all_in_one_kdcube" / "docker-compose.yaml",
        DOCKER_ROOT / "custom-ui-managed-infra" / "docker-compose.yaml",
    ):
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = compose["services"]
        broker = services["kdcube-secrets"]
        serialized_broker = yaml.safe_dump(broker)

        assert (
            "KDCUBE_SECRETS_SERVICE_BACKEND=${KDCUBE_SECRETS_SERVICE_BACKEND:-ephemeral}"
            in broker["environment"]
        )
        assert "kdcube-host-vault" in broker["networks"]
        assert "host.docker.internal:host-gateway" in broker["extra_hosts"]
        assert "ports" not in broker
        assert serialized_broker.count("/run/kdcube-host-vault-identity/") == 3
        assert all(item["read_only"] is True for item in broker["volumes"])
        assert broker["healthcheck"]["test"][0:2] == ["CMD", "python"]
        assert services["chat-ingress"]["depends_on"]["kdcube-secrets"] == {
            "condition": "service_healthy"
        }
        assert services["chat-proc"]["depends_on"]["kdcube-secrets"] == {
            "condition": "service_healthy"
        }
        assert "SECRETS_ADMIN_TOKEN=${SECRETS_ADMIN_TOKEN:-}" in services[
            "chat-ingress"
        ]["environment"]
        assert "SECRETS_ADMIN_TOKEN=${SECRETS_ADMIN_TOKEN:-}" in services[
            "chat-proc"
        ]["environment"]
        assert set(services["chat-proc"]["networks"]) >= {
            "kdcube-internal",
            "kdcube-secrets",
        }

        for service_name, service in services.items():
            if service_name == "kdcube-secrets":
                continue
            serialized = yaml.safe_dump(service)
            assert "host-vault-client.key" not in serialized
            assert "KDCUBE_HOST_VAULT_ADDR" not in serialized

        assert compose["networks"]["kdcube-secrets"]["internal"] is True
        assert compose["networks"]["kdcube-host-vault"] == {"driver": "bridge"}
