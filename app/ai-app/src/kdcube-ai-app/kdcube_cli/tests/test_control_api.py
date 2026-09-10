import json
from pathlib import Path

import pytest
import yaml

from kdcube_cli import cli as cli_mod
from kdcube_cli.control import initialization as initialization_control
from kdcube_cli.control import (
    AmbiguousApplicationSurfaceError,
    AmbiguousTargetError,
    ApplicationRef,
    ControlEvent,
    ControlEventKind,
    DeploymentTarget,
    DeploymentTargetRef,
    DockerUnavailableError,
    EndpointDeploymentTarget,
    InvalidDescriptorError,
    LocalDeploymentTarget,
    LocalInitializationRequest,
    LocalStartRequest,
    LocalStopRequest,
    OperationFailedError,
    OperationResult,
    SurfaceKind,
    SurfaceSelector,
    TargetCapability,
    UnsupportedCapabilityError,
    discover_local_targets,
    resolve_local_workdir,
)
from kdcube_cli.control.execution import CommandResult
from kdcube_cli.control.local import InstallerRuntimeInitializer


class FakeRunner:
    def __init__(self, *, running_services="chat-proc\nweb-proxy\n", missing=False):
        self.calls = []
        self.running_services = running_services
        self.missing = missing

    def run(
        self,
        command,
        *,
        cwd=None,
        env=None,
        timeout=None,
        capture_output=False,
    ):
        self.calls.append(
            {
                "command": tuple(command),
                "cwd": cwd,
                "env": dict(env or {}),
                "timeout": timeout,
                "capture_output": capture_output,
            }
        )
        if self.missing and command[:2] == ["docker", "info"]:
            raise FileNotFoundError("docker")
        if "ps" in command:
            return CommandResult(returncode=0, stdout=self.running_services)
        return CommandResult(returncode=0, stdout="26.1.0\n")


class FakeInitializer:
    def __init__(self):
        self.calls = []

    def prepare(self, *, target, repo_root, request, event_sink):
        self.calls.append((target, repo_root, request, event_sink))
        config_dir = target.workdir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("KDCUBE_UI_PORT=5174\n")


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    docker_dir = repo / "app" / "ai-app" / "deployment" / "docker" / "all_in_one_kdcube"
    docker_dir.mkdir(parents=True)
    (docker_dir / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "app" / "ai-app" / "src" / "kdcube-ai-app" / "kdcube_ai_app").mkdir(
        parents=True
    )
    return repo


def _make_runtime(tmp_path: Path, *, widgets=("connections_settings",)):
    repo = _make_repo(tmp_path)
    workdir = tmp_path / "runtimes" / "demo-tenant__demo-project"
    config = workdir / "config"
    config.mkdir(parents=True)
    frontend = config / "frontend.json"
    frontend.write_text(json.dumps({"routesPrefix": "/platform"}))
    (config / ".env").write_text(
        "\n".join(
            [
                "KDCUBE_UI_PORT=5174",
                "KDCUBE_COMPOSE_MODE=all-in-one",
                f"KDCUBE_LOGS_DIR={workdir / 'logs'}",
                f"PATH_TO_FRONTEND_CONFIG_JSON={frontend}",
                "HOST_BUNDLES_PATH=/host/bundles",
                "BUNDLES_ROOT=/bundles",
                "",
            ]
        )
    )
    _write_yaml(
        config / "assembly.yaml",
        {
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "platform": {"ref": "2026.09.02.1429"},
        },
    )
    widget_config = {
        alias: {"enabled": True, "src_folder": f"ui/widgets/{alias}"}
        for alias in widgets
    }
    _write_yaml(
        config / "bundles.yaml",
        {
            "bundles": {
                "version": "1",
                "default_bundle_id": "connection-hub@1-0",
                "items": [
                    {
                        "id": "connection-hub@1-0",
                        "name": "Connection Hub",
                        "repo": "https://github.com/example/apps.git",
                        "ref": "2026.09.02.1410",
                        "config": {
                            "surfaces": {
                                "as_provider": {
                                    "mcp": {"remote_mcp_proxy": {"auth": {"mode": "managed"}}}
                                }
                            },
                            "ui": {"widgets": widget_config},
                        },
                    }
                ],
            }
        },
    )
    (config / "install-meta.json").write_text(
        json.dumps(
            {
                "tenant": "demo-tenant",
                "project": "demo-project",
                "platform_ref": "2026.09.02.1429",
                "install_mode": "release",
                "repo_root": str(repo),
            }
        )
    )
    return repo, workdir


def test_local_discovery_and_resolution_report_ambiguous_targets(tmp_path):
    root = tmp_path / "runtimes"
    for name in ("tenant-a__project-a", "tenant-b__project-b"):
        config = root / name / "config"
        config.mkdir(parents=True)
        (config / ".env").write_text("KDCUBE_UI_PORT=80\n")

    targets = discover_local_targets(root)

    assert [target.workdir.name for target in targets] == [
        "tenant-a__project-a",
        "tenant-b__project-b",
    ]
    with pytest.raises(AmbiguousTargetError) as captured:
        resolve_local_workdir(root)
    assert captured.value.code.value == "target.ambiguous"
    assert captured.value.candidates == tuple(str(target.workdir) for target in targets)


def test_local_status_resolves_connection_hub_inventory_and_surfaces(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        lock_file=tmp_path / "cli-lock.json",
    )

    status = target.status(probe_runtime=False)
    app = target.application_status(ApplicationRef("connection-hub@1-0"))
    widget = target.resolve_surface(
        app.reference,
        SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings"),
    )
    mcp = target.resolve_surface(
        app.reference,
        SurfaceSelector(kind=SurfaceKind.MCP, alias="remote_mcp_proxy"),
    )

    assert status.reference.tenant == "demo-tenant"
    assert status.reference.project == "demo-project"
    assert status.release.platform_ref == "2026.09.02.1429"
    assert status.default_application_id == "connection-hub@1-0"
    assert status.public_base_url == "http://localhost:5174"
    assert widget.url == (
        "http://localhost:5174/api/integrations/bundles/demo-tenant/demo-project/"
        "connection-hub@1-0/public/widgets/connections_settings"
    )
    assert widget.openable is True
    assert mcp.url.endswith("/connection-hub@1-0/public/mcp/remote_mcp_proxy")
    assert mcp.openable is False
    assert target.application_url(app.reference) == widget.url
    opened = []
    result = target.open_application(
        app.reference,
        opener=lambda url: opened.append(url) or True,
    )
    assert result.url == widget.url
    assert opened == [widget.url]


def test_repository_connection_hub_descriptor_resolves_product_surfaces(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    descriptor_path = Path(__file__).resolve().parents[4] / "deployment" / "bundles.yaml"
    (workdir / "config" / "bundles.yaml").write_text(descriptor_path.read_text())
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        lock_file=tmp_path / "cli-lock.json",
    )

    app = target.application_status(ApplicationRef("connection-hub@1-0"))

    assert {surface.surface_id for surface in app.surfaces} >= {
        "widget:connections_settings",
        "mcp:remote_mcp_proxy",
    }
    assert target.application_url(
        app.reference,
        SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings"),
    ).endswith(
        "/connection-hub@1-0/public/widgets/connections_settings"
    )


def test_local_status_preserves_unwrapped_bundle_catalog_compatibility(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    descriptor_path = workdir / "config" / "bundles.yaml"
    wrapped = yaml.safe_load(descriptor_path.read_text())
    descriptor_path.write_text(yaml.safe_dump(wrapped["bundles"], sort_keys=False))
    target = LocalDeploymentTarget(DeploymentTargetRef.local(workdir), repo_root=repo)

    status = target.status(probe_runtime=False)

    assert status.default_application_id == "connection-hub@1-0"
    assert [app.reference.bundle_id for app in status.applications] == [
        "connection-hub@1-0"
    ]


def test_local_status_does_not_expose_repository_url_credentials(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    descriptor_path = workdir / "config" / "bundles.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text())
    entry = descriptor["bundles"]["items"][0]
    entry.pop("ref")
    entry["repo"] = "https://operator:descriptor-secret@example.test/private.git"
    descriptor_path.write_text(yaml.safe_dump(descriptor, sort_keys=False))
    target = LocalDeploymentTarget(DeploymentTargetRef.local(workdir), repo_root=repo)

    application = target.application_status(ApplicationRef("connection-hub@1-0"))

    assert application.source_ref is None
    assert "descriptor-secret" not in repr(application)


def test_local_default_application_url_rejects_multiple_widgets(tmp_path):
    repo, workdir = _make_runtime(tmp_path, widgets=("connections_settings", "connection_cards"))
    target = LocalDeploymentTarget(DeploymentTargetRef.local(workdir), repo_root=repo)

    with pytest.raises(AmbiguousApplicationSurfaceError) as captured:
        target.application_url(ApplicationRef("connection-hub@1-0"))

    assert captured.value.code.value == "application.surface_ambiguous"


def test_endpoint_target_has_no_workdir_and_fails_closed_for_management():
    target = EndpointDeploymentTarget(
        DeploymentTargetRef.endpoint_target(
            "https://runtime.example/",
            tenant="acme",
            project="prod",
        )
    )
    selector = SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings")

    assert target.reference.workdir is None
    assert target.capabilities.supports(TargetCapability.OPEN)
    assert not target.capabilities.supports(TargetCapability.STATUS)
    assert target.application_url(ApplicationRef("connection-hub@1-0"), selector) == (
        "https://runtime.example/api/integrations/bundles/acme/prod/connection-hub@1-0/"
        "public/widgets/connections_settings"
    )
    assert target.describe().reachable is None
    assert target.describe().initialized is None
    diagnostic = target.describe().diagnostics[0]
    assert diagnostic.summary == (
        "This target resolves application endpoints. Its target-control capabilities "
        "are endpoint discovery and browser opening."
    )
    assert diagnostic.recovery["management"] == (
        "Use an authenticated client for a management API exposed by the deployment."
    )
    with pytest.raises(UnsupportedCapabilityError) as captured:
        target.status()
    assert captured.value.code.value == "target.unsupported_capability"


def test_downstream_target_double_needs_no_workdir_or_docker_contract():
    class EndpointTargetDouble:
        reference = DeploymentTargetRef.endpoint_target(
            "https://runtime.example",
            tenant="acme",
            project="prod",
        )
        capabilities = EndpointDeploymentTarget(reference).capabilities

        def describe(self):
            return EndpointDeploymentTarget(self.reference).describe()

        def resolve_surface(self, application, selector):
            return EndpointDeploymentTarget(self.reference).resolve_surface(
                application, selector
            )

        def application_url(self, application, selector):
            return EndpointDeploymentTarget(self.reference).application_url(
                application, selector
            )

        def open_application(self, application, selector, *, opener):
            return EndpointDeploymentTarget(self.reference).open_application(
                application, selector, opener=opener
            )

    target = EndpointTargetDouble()

    assert isinstance(target, DeploymentTarget)
    assert target.reference.workdir is None
    assert target.application_url(
        ApplicationRef("connection-hub@1-0"),
        SurfaceSelector(kind=SurfaceKind.WIDGET, alias="connections_settings"),
    ).startswith("https://runtime.example/")


def test_local_start_and_stop_use_typed_lifecycle_and_lock(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    runner = FakeRunner()
    lock_file = tmp_path / "cli-lock.json"
    events = []
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=runner,
        lock_file=lock_file,
    )

    started = target.start(LocalStartRequest(build=True), event_sink=events.append)
    stopped = target.stop(LocalStopRequest(remove_volumes=False), event_sink=events.append)

    assert started.running is True
    assert started.url == "http://localhost:5174/platform/chat"
    assert stopped.running is False
    assert not lock_file.exists()
    compose_commands = [call["command"] for call in runner.calls if "compose" in call["command"]]
    assert any(command[-2:] == ("-d", "--build") for command in compose_commands)
    assert any("down" in command and "--remove-orphans" in command for command in compose_commands)
    assert [event.kind for event in events] == [
        ControlEventKind.COMMAND,
        ControlEventKind.COMMAND,
    ]


def test_local_start_activates_proxy_login_for_delegated_auth(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    _write_yaml(
        workdir / "config" / "assembly.yaml",
        {
            "auth": {
                "type": "delegated",
                "proxy_login": {"enabled": True},
            }
        },
    )
    runner = FakeRunner()
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=runner,
        lock_file=tmp_path / "cli-lock.json",
    )

    target.start()

    up_command = next(call["command"] for call in runner.calls if "up" in call["command"])
    assert ("--profile", "proxylogin") == up_command[4:6]


def test_local_start_removes_proxy_login_after_it_is_disabled(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    runner = FakeRunner(running_services="chat-proc\nproxylogin\nweb-proxy\n")
    events = []
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=runner,
        lock_file=tmp_path / "cli-lock.json",
    )

    target.start(event_sink=events.append)

    compose_commands = [call["command"] for call in runner.calls if "compose" in call["command"]]
    remove_command = next(command for command in compose_commands if "rm" in command)
    up_command = next(command for command in compose_commands if "up" in command)
    assert remove_command[-4:] == ("rm", "--stop", "--force", "proxylogin")
    assert "--profile" not in up_command
    assert [event.kind for event in events] == [
        ControlEventKind.COMMAND,
        ControlEventKind.COMMAND,
    ]


def test_local_start_rejects_proxy_login_for_server_side_login(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    _write_yaml(
        workdir / "config" / "assembly.yaml",
        {
            "auth": {
                "type": "bundle",
                "proxy_login": {"enabled": True},
            }
        },
    )
    runner = FakeRunner()
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=runner,
        lock_file=tmp_path / "cli-lock.json",
    )

    with pytest.raises(OperationFailedError, match="proxy_login.enabled=true"):
        target.start()

    assert not any("compose" in call["command"] for call in runner.calls)


def test_local_lifecycle_distinguishes_unavailable_docker(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=FakeRunner(missing=True),
    )

    with pytest.raises(DockerUnavailableError) as captured:
        target.start()

    assert captured.value.code.value == "docker.unavailable"


def test_local_lifecycle_failure_is_structured_and_omits_process_output(tmp_path):
    class FailingComposeRunner(FakeRunner):
        def run(self, command, **kwargs):
            if "up" in command:
                return CommandResult(
                    returncode=17,
                    stdout="sensitive-stdout",
                    stderr="sensitive-stderr",
                )
            return super().run(command, **kwargs)

    repo, workdir = _make_runtime(tmp_path)
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=FailingComposeRunner(),
        lock_file=tmp_path / "cli-lock.json",
    )

    with pytest.raises(OperationFailedError) as captured:
        target.start()

    assert captured.value.code.value == "operation.failed"
    assert captured.value.returncode == 17
    assert "sensitive" not in captured.value.summary


def test_local_start_refuses_incomplete_host_vault_identity_before_compose(tmp_path):
    repo, workdir = _make_runtime(tmp_path)
    _write_yaml(
        workdir / "config" / "assembly.yaml",
        {
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "platform": {
                "services": {
                    "proc": {"exec": {"py_code_exec_network_mode": "auto"}}
                }
            },
            "secrets": {
                "provider": "secrets-service",
                "service": {
                    "backend": "host-vault",
                    "host_vault": {
                        "address": "host.docker.internal:7781",
                        "server_name": "host.docker.internal",
                        "identity_dir": str(tmp_path / "missing-identity"),
                    },
                },
            },
        },
    )
    runner = FakeRunner()
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir),
        repo_root=repo,
        runner=runner,
        lock_file=tmp_path / "cli-lock.json",
    )

    with pytest.raises(OperationFailedError) as captured:
        target.start()

    assert "Host-vault startup preflight failed" in captured.value.summary
    assert "is incomplete" in captured.value.summary
    assert not any("compose" in call["command"] for call in runner.calls)


def test_local_initialize_uses_typed_request_without_console(tmp_path):
    repo = _make_repo(tmp_path)
    workdir = tmp_path / "runtimes" / "acme__lab"
    initializer = FakeInitializer()
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir, tenant="acme", project="lab"),
        repo_root=repo,
        initializer=initializer,
    )
    request = LocalInitializationRequest(
        descriptor_source=tmp_path / "descriptors",
        release_ref="2026.09.02.1429",
    )

    result = target.initialize(request)

    assert result.operation == "initialize"
    assert result.changed is True
    assert initializer.calls[0][2] == request
    assert initializer.calls[0][3] is None


def test_installer_initializer_maps_invalid_descriptor(tmp_path):
    repo = _make_repo(tmp_path)
    workdir = tmp_path / "runtimes" / "acme__lab"
    descriptors = tmp_path / "descriptors"
    descriptors.mkdir()
    (descriptors / "assembly.yaml").write_text("- not-a-mapping\n")
    target = DeploymentTargetRef.local(workdir, tenant="acme", project="lab")

    with pytest.raises(InvalidDescriptorError) as captured:
        InstallerRuntimeInitializer().prepare(
            target=target,
            repo_root=repo,
            request=LocalInitializationRequest(descriptor_source=descriptors),
            event_sink=None,
        )

    assert captured.value.code.value == "descriptor.invalid"


def test_default_initializer_runs_prepare_only_without_console_or_prompt(
    monkeypatch, tmp_path
):
    repo = _make_repo(tmp_path)
    workdir = tmp_path / "runtimes" / "acme__lab"
    descriptors = tmp_path / "descriptors"
    _write_yaml(
        descriptors / "assembly.yaml",
        {
            "context": {"tenant": "default", "project": "default"},
            "platform": {"ref": "2026.09.02.1429"},
        },
    )
    observed = {}

    def fake_run_setup(console, **kwargs):
        observed["noninteractive"] = initialization_control.os.environ.get(
            "KDCUBE_CLI_NONINTERACTIVE"
        )
        observed["prepare_only"] = initialization_control.os.environ.get(
            "KDCUBE_INIT_PREPARE_ONLY"
        )
        observed["tenant"] = initialization_control.os.environ.get("KDCUBE_PRESET_TENANT")
        observed["project"] = initialization_control.os.environ.get("KDCUBE_PRESET_PROJECT")
        observed["kwargs"] = kwargs
        console.print("prepared")
        config_dir = kwargs["workdir"] / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("KDCUBE_UI_PORT=5174\n")

    monkeypatch.setattr(initialization_control.installer_mod, "run_setup", fake_run_setup)
    monkeypatch.delenv("KDCUBE_CLI_NONINTERACTIVE", raising=False)
    events = []
    target = LocalDeploymentTarget(
        DeploymentTargetRef.local(workdir, tenant="acme", project="lab"),
        repo_root=repo,
    )

    target.initialize(
        LocalInitializationRequest(descriptor_source=descriptors),
        event_sink=events.append,
    )

    assert observed["noninteractive"] == "1"
    assert observed["prepare_only"] == "1"
    assert observed["tenant"] == "acme"
    assert observed["project"] == "lab"
    assert observed["kwargs"]["dry_run"] is True
    assert observed["kwargs"]["workdir"] == workdir
    assert [event.message for event in events] == ["prepared"]
    assert "KDCUBE_CLI_NONINTERACTIVE" not in initialization_control.os.environ


def test_cli_start_and_stop_adapters_use_public_local_target(monkeypatch, tmp_path):
    calls = []
    maintenance_phases = []

    class FakeTarget:
        def __init__(self, reference, **kwargs):
            calls.append(("init", reference, kwargs))

        def start(self, request, *, event_sink):
            calls.append(("start", request, event_sink))
            event_sink(
                ControlEvent(
                    kind=ControlEventKind.COMMAND,
                    message="$ docker compose up -d --build",
                )
            )
            return OperationResult(
                target=DeploymentTargetRef.local(tmp_path / "runtime"),
                operation="start",
                changed=True,
                running=True,
                url="http://localhost/platform/chat",
            )

        def stop(self, request, *, event_sink):
            calls.append(("stop", request, event_sink))
            event_sink(
                ControlEvent(
                    kind=ControlEventKind.COMMAND,
                    message="$ docker compose down --remove-orphans -v",
                )
            )
            return OperationResult(
                target=DeploymentTargetRef.local(tmp_path / "runtime"),
                operation="stop",
                changed=True,
                running=False,
            )

    monkeypatch.setattr(cli_mod, "LocalDeploymentTarget", FakeTarget)
    monkeypatch.setattr(
        cli_mod,
        "_maintain_docker_build_storage",
        lambda console, *, phase: maintenance_phases.append(phase),
    )
    console = cli_mod.Console(
        file=cli_mod.io.StringIO(), force_terminal=False, width=500
    )

    cli_mod.start_compose_stack(
        console,
        repo_root=tmp_path / "repo",
        workdir=tmp_path / "runtime",
        build=True,
    )
    cli_mod.stop_compose_stack(
        console,
        repo_root=tmp_path / "repo",
        workdir=tmp_path / "runtime",
        remove_volumes=True,
    )

    assert isinstance(calls[1][1], LocalStartRequest)
    assert calls[1][1].build is True
    assert isinstance(calls[3][1], LocalStopRequest)
    assert calls[3][1].remove_volumes is True
    assert maintenance_phases == ["before", "after"]
    output = console.file.getvalue()
    assert "$ docker compose up -d --build" in output
    assert "Docker compose started." in output
    assert "Open the UI:" in output
    assert "http://localhost/platform/chat" in output
    assert "$ docker compose down --remove-orphans -v" in output
    assert "Docker compose stopped." in output
    assert f"Workdir: {tmp_path / 'runtime'}" in output
    assert "Host data under the workdir was preserved." not in output
