from pathlib import Path

import yaml


DOCKER_ROOT = Path(__file__).resolve().parents[1]
LEGACY_LOCAL_ONLY_DIRS = {"all_in_one"}
COMPOSE_FILES = tuple(
    path
    for path in sorted(DOCKER_ROOT.glob("*/docker-compose.yaml"))
    if path.parent.name not in LEGACY_LOCAL_ONLY_DIRS
)

PERSISTENT_INFRA_SERVICES = {
    "all_in_one_kdcube": ("postgres-db", "redis"),
    "local-infra-stack": ("postgres-db", "redis"),
}


def test_local_compose_services_bound_docker_managed_logs():
    assert COMPOSE_FILES
    assert {path.parent.name for path in COMPOSE_FILES} >= {
        "all_in_one_kdcube",
        "custom-ui-managed-infra",
        "local-infra-stack",
    }
    for compose_path in COMPOSE_FILES:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = compose.get("services") or {}
        assert services, compose_path
        for service_name, service in services.items():
            logging = service.get("logging") or {}
            assert logging.get("driver") == "json-file", (compose_path, service_name)
            assert logging.get("options") == {
                "max-size": "20m",
                "max-file": "3",
            }, (compose_path, service_name)


def test_persistent_local_infra_returns_after_docker_restart():
    for compose_name, service_names in PERSISTENT_INFRA_SERVICES.items():
        compose_path = DOCKER_ROOT / compose_name / "docker-compose.yaml"
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = compose["services"]

        for service_name in service_names:
            assert services[service_name].get("restart") == "unless-stopped", (
                compose_path,
                service_name,
            )

        assert services["postgres-setup"].get("restart") == "no", compose_path
