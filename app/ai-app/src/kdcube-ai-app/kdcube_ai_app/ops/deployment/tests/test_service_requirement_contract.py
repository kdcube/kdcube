from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONFIG_IMPORTING_REQUIREMENTS = (
    "requirements-chat.txt",
    "requirements-chat-ingress.txt",
    "requirements-chat-processor.txt",
    "requirements-dbdeploy.txt",
    "requirements-metric-service.txt",
)
CONNECTION_HUB_REQUIREMENT = "connection-hub>=2026.09.23.0158,<2027"
PROJECT_BOARD_HOST_REQUIREMENTS = (
    "requirements-chat.txt",
    "requirements-chat-processor.txt",
)
PROJECT_BOARD_REQUIREMENT = "project-board>=2026.09.23.0158,<2027"


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


def test_every_fastapi_image_pins_the_same_sse_starlette() -> None:
    # A Project Board source build installs mcp into every image. Where
    # sse-starlette was unpinned the resolver took a release that needs
    # starlette 1.x, fastapi 0.116 refused it, and chat-ingress and the
    # metrics service exited at startup (2026-09-23). Each image that ships
    # fastapi therefore pins sse-starlette to one shared version.
    fastapi_images = [
        filename
        for filename in CONFIG_IMPORTING_REQUIREMENTS
        if any(
            line.strip().startswith("fastapi==")
            for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines()
        )
    ]
    assert fastapi_images, "no image declares fastapi"
    pins = {
        filename: _requirement(filename, "sse-starlette")
        for filename in fastapi_images
    }
    assert len(set(pins.values())) == 1, f"sse-starlette pins differ: {pins}"
    assert all("==" in pin for pin in pins.values()), pins
