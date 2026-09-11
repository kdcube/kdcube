from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kdcube_cli import installer as installer_mod
from kdcube_cli.control import (
    DeploymentTargetRef,
    InvalidDescriptorError,
    LocalDeploymentTarget,
    LocalInitializationRequest,
    LocalPlatformSourceRequest,
    OperationFailedError,
)
from kdcube_cli.control.execution import CommandResult


class SourceRunner:
    def __init__(self, *, fail_clone: bool = False) -> None:
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []
        self.fail_clone = fail_clone

    def run(
        self,
        command,
        *,
        cwd=None,
        env=None,
        timeout=None,
        capture_output=False,
    ) -> CommandResult:
        values = tuple(command)
        self.calls.append((values, cwd))
        if values[:2] == ("git", "clone"):
            if self.fail_clone:
                return CommandResult(returncode=128, stderr="synthetic-secret")
            destination = Path(values[-1])
            (destination / ".git").mkdir(parents=True)
            (destination / "app" / "ai-app" / "deployment").mkdir(parents=True)
            (
                destination / "app" / "ai-app" / "deployment" / "assembly.yaml"
            ).write_text("context: {tenant: default, project: default}\n")
            (
                destination
                / "app"
                / "ai-app"
                / "src"
                / "kdcube-ai-app"
                / "kdcube_ai_app"
            ).mkdir(parents=True)
            return CommandResult(returncode=0)
        if (
            len(values) == 5
            and values[:2] == ("git", "-C")
            and values[3:]
            == (
                "rev-parse",
                "--show-toplevel",
            )
        ):
            candidate = Path(values[2])
            ready = (candidate / ".git").exists()
            return CommandResult(
                returncode=0 if ready else 128,
                stdout=f"{candidate}\n" if ready else "",
            )
        if values == ("git", "show", "origin/main:release.yaml"):
            return CommandResult(
                returncode=0,
                stdout="platform:\n  ref: '2026.09.02.1429'\n",
            )
        if values == ("git", "rev-parse", "HEAD"):
            return CommandResult(returncode=0, stdout="abc123\n")
        return CommandResult(returncode=0)


def _target(tmp_path: Path) -> DeploymentTargetRef:
    return DeploymentTargetRef.local(
        tmp_path / "local__connection-hub",
        tenant="local",
        project="connection-hub",
    )


def test_prepare_source_clones_and_selects_latest_release(tmp_path: Path) -> None:
    runner = SourceRunner()
    target = LocalDeploymentTarget(_target(tmp_path), runner=runner)
    events = []

    prepared = target.prepare_source(event_sink=events.append)

    assert prepared.release_ref == "2026.09.02.1429"
    assert prepared.install_mode == "release"
    assert prepared.descriptor_source.name == "deployment"
    assert (
        "git",
        "clone",
        "https://github.com/kdcube/kdcube.git",
        str(prepared.repo_root),
    ) in {call for call, _cwd in runner.calls}
    assert any(
        call == ("git", "show", "origin/main:release.yaml") for call, _ in runner.calls
    )
    assert any(
        call == ("git", "checkout", "--detach", "2026.09.02.1429")
        for call, _ in runner.calls
    )
    assert [event.message for event in events] == [
        "Cloning the KDCube platform source.",
        "Resolving the latest KDCube release.",
        "Selecting KDCube release 2026.09.02.1429.",
    ]


def test_prepare_source_supports_explicit_release_build_and_upstream(
    tmp_path: Path,
) -> None:
    release_runner = SourceRunner()
    release_target = LocalDeploymentTarget(_target(tmp_path), runner=release_runner)

    release = release_target.prepare_source(
        LocalPlatformSourceRequest(release_ref="2026.09.01.110", build=True)
    )

    assert release.release_ref == "2026.09.01.110"
    assert release.install_mode == "skip"
    assert not any(call[:2] == ("git", "show") for call, _ in release_runner.calls)

    upstream_runner = SourceRunner()
    upstream_ref = DeploymentTargetRef.local(
        tmp_path / "upstream__hub", tenant="upstream", project="hub"
    )
    upstream_target = LocalDeploymentTarget(upstream_ref, runner=upstream_runner)
    upstream = upstream_target.prepare_source(LocalPlatformSourceRequest(upstream=True))

    assert upstream.release_ref == "abc123"
    assert upstream.install_mode == "upstream"
    assert any(
        call == ("git", "checkout", "--detach", "origin/main")
        for call, _ in upstream_runner.calls
    )


def test_prepare_source_refuses_conflicting_selection_and_occupied_directory(
    tmp_path: Path,
) -> None:
    target = LocalDeploymentTarget(_target(tmp_path), runner=SourceRunner())
    with pytest.raises(OperationFailedError):
        target.prepare_source(
            LocalPlatformSourceRequest(release_ref="2026.09.01.110", upstream=True)
        )

    occupied_ref = DeploymentTargetRef.local(
        tmp_path / "occupied__hub", tenant="occupied", project="hub"
    )
    occupied_repo = occupied_ref.workdir / "repo"
    occupied_repo.mkdir(parents=True)
    (occupied_repo / "unrelated.txt").write_text("preserve me\n")
    occupied = LocalDeploymentTarget(occupied_ref, runner=SourceRunner())

    with pytest.raises(OperationFailedError) as captured:
        occupied.prepare_source()

    assert "not a Git repository" in captured.value.summary
    assert (occupied_repo / "unrelated.txt").read_text() == "preserve me\n"


def test_prepare_source_does_not_expose_git_failure_output(tmp_path: Path) -> None:
    target = LocalDeploymentTarget(
        _target(tmp_path), runner=SourceRunner(fail_clone=True)
    )

    with pytest.raises(OperationFailedError) as captured:
        target.prepare_source(
            LocalPlatformSourceRequest(
                repository="https://operator:credential@example.test/private.git"
            )
        )

    assert "credential" not in captured.value.summary
    assert "synthetic-secret" not in captured.value.summary
    assert "credential" not in repr(captured.value.recovery)


def test_initialize_simple_auth_override_stages_before_noninteractive_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    source = repo / "app" / "ai-app" / "deployment"
    source.mkdir(parents=True)
    (source / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "context": {"tenant": "default", "project": "default"},
                "auth": {"type": "bundle"},
            }
        )
    )
    (repo / "app" / "ai-app" / "src" / "kdcube-ai-app" / "kdcube_ai_app").mkdir(
        parents=True
    )
    (repo / "app" / "ai-app" / "deployment" / "docker" / "all_in_one_kdcube").mkdir(
        parents=True
    )
    (
        repo
        / "app"
        / "ai-app"
        / "deployment"
        / "docker"
        / "all_in_one_kdcube"
        / "docker-compose.yaml"
    ).write_text("services: {}\n")
    workdir = tmp_path / "local__connection-hub"
    observed: dict[str, object] = {}

    def stage_descriptor_directory(
        target, *, source_dir, ai_app_root, require_complete
    ):
        target.mkdir(parents=True, exist_ok=True)
        assembly = yaml.safe_load((source_dir / "assembly.yaml").read_text())
        assembly_path = target / "assembly.yaml"
        assembly_path.write_text(yaml.safe_dump(assembly))
        return {"assembly": assembly, "assembly_path": assembly_path}

    def run_setup(_console, *, repo_root, workdir, **kwargs):
        observed["assembly"] = yaml.safe_load(
            (workdir / "config" / "assembly.yaml").read_text()
        )
        installer_mod.report_unfilled_descriptor_slots(
            workdir / "config",
            ["<SYNTHETIC_SLOT>"],
            console=_console,
        )
        (workdir / "config" / ".env").write_text("KDCUBE_UI_PORT=5173\n")

    monkeypatch.setattr(
        installer_mod, "stage_descriptor_directory", stage_descriptor_directory
    )
    monkeypatch.setattr(installer_mod, "run_setup", run_setup)
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir, tenant="local", project="connection-hub"),
        repo_root=repo,
    )

    events = []
    result = target.initialize(
        LocalInitializationRequest(
            descriptor_source=source,
            release_ref="2026.09.02.1429",
            auth_type="simple",
        ),
        event_sink=events.append,
    )

    assert result.changed is True
    assert observed["assembly"]["auth"]["type"] == "simple"
    assert any("SYNTHETIC_SLOT" in event.message for event in events)
    assert all("[bold" not in event.message for event in events)
    assert all("[yellow" not in event.message for event in events)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_initialize_rejects_unsupported_noninteractive_auth_override(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "app" / "ai-app" / "deployment"
    source.mkdir(parents=True)
    (source / "assembly.yaml").write_text("auth: {type: bundle}\n")
    workdir = tmp_path / "local__connection-hub"
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir, tenant="local", project="connection-hub"),
        repo_root=repo,
    )

    with pytest.raises(InvalidDescriptorError) as captured:
        target.initialize(
            LocalInitializationRequest(
                descriptor_source=source,
                auth_type="cognito",
            )
        )

    assert "supports bundle or simple auth" in str(captured.value)


def test_initialize_bundle_auth_stages_google_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "app" / "ai-app" / "deployment"
    source.mkdir(parents=True)
    (source / "assembly.yaml").write_text(
        yaml.safe_dump({"auth": {"type": "simple", "idp": "simple"}})
    )
    workdir = tmp_path / "local__connection-hub"
    observed: dict[str, object] = {}

    def stage_descriptor_directory(
        target, *, source_dir, ai_app_root, require_complete
    ):
        target.mkdir(parents=True, exist_ok=True)
        assembly = yaml.safe_load((source_dir / "assembly.yaml").read_text())
        assembly_path = target / "assembly.yaml"
        assembly_path.write_text(yaml.safe_dump(assembly))
        return {"assembly": assembly, "assembly_path": assembly_path}

    def run_setup(_console, *, workdir, **_kwargs):
        observed["assembly"] = yaml.safe_load(
            (workdir / "config" / "assembly.yaml").read_text()
        )
        (workdir / "config" / ".env").write_text("KDCUBE_UI_PORT=5173\n")

    monkeypatch.setattr(
        installer_mod, "stage_descriptor_directory", stage_descriptor_directory
    )
    monkeypatch.setattr(installer_mod, "run_setup", run_setup)
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir, tenant="local", project="connection-hub"),
        repo_root=repo,
    )

    target.initialize(
        LocalInitializationRequest(
            descriptor_source=source,
            auth_type="bundle",
            auth_provider="google",
            auth_client_id="client.apps.googleusercontent.com",
            bootstrap_admin_email="admin@example.com",
        )
    )

    auth = observed["assembly"]["auth"]
    assert auth["type"] == "bundle"
    assert "idp" not in auth
    assert auth["bundle"] == {
        "provider": "google",
        "client_id": "client.apps.googleusercontent.com",
        "bootstrap_admin_email": "admin@example.com",
    }


def test_initialize_bundle_auth_requires_google_client_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "app" / "ai-app" / "deployment"
    source.mkdir(parents=True)
    (source / "assembly.yaml").write_text("auth: {type: bundle}\n")
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(
            tmp_path / "local__connection-hub",
            tenant="local",
            project="connection-hub",
        ),
        repo_root=repo,
    )

    with pytest.raises(InvalidDescriptorError) as captured:
        target.initialize(
            LocalInitializationRequest(
                descriptor_source=source,
                auth_type="bundle",
            )
        )

    assert "requires a Google OAuth client id" in str(captured.value)
