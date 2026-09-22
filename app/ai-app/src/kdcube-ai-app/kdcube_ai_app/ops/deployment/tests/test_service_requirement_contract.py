from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONFIG_IMPORTING_REQUIREMENTS = (
    "requirements-chat.txt",
    "requirements-chat-ingress.txt",
    "requirements-chat-processor.txt",
    "requirements-dbdeploy.txt",
    "requirements-metric-service.txt",
)
CONNECTION_HUB_REQUIREMENT = "connection-hub>=2026.09.02.0000"
PROJECT_BOARD_HOST_REQUIREMENTS = (
    "requirements-chat.txt",
    "requirements-chat-processor.txt",
)
PROJECT_BOARD_REQUIREMENT = "project-board>=2026.09.22.2241,<2027"


def _requirement(filename: str, distribution: str) -> str:
    lines = (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines()
    requirements = [
        line.strip()
        for line in lines
        if line.strip().startswith(distribution)
    ]
    assert len(requirements) == 1, (
        f"{filename} must declare one {distribution} requirement"
    )
    return requirements[0]


def test_shared_config_services_use_the_same_connection_hub_compatibility_floor() -> None:
    requirements = {
        _requirement(filename, "connection-hub")
        for filename in CONFIG_IMPORTING_REQUIREMENTS
    }
    assert len(requirements) == 1, (
        "shared-config service images use different Connection Hub requirements: "
        f"{requirements}"
    )
    assert requirements == {CONNECTION_HUB_REQUIREMENT}


def test_problem_board_host_images_use_the_same_compatibility_floor() -> None:
    requirements = {
        _requirement(filename, "project-board")
        for filename in PROJECT_BOARD_HOST_REQUIREMENTS
    }
    assert requirements == {PROJECT_BOARD_REQUIREMENT}, (
        "Problem Board host images must use the shared compatibility floor "
        f"{PROJECT_BOARD_REQUIREMENT}; found {requirements}"
    )
