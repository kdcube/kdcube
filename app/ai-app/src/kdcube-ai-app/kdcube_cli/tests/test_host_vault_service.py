import socket
from pathlib import Path

import pytest
from kdcube_cli.host_vault import HostVaultConfigurationError, config_from_assembly
from kdcube_cli.host_vault_service import (
    VAULT_SERVER_RELATIVE,
    ensure_host_vault_running,
    local_service_from_assembly,
    probe_target,
    tcp_probe,
)


def _assembly(tmp_path: Path, *, local_service: object = "absent", address: str = "host.docker.internal:7781") -> dict:
    vault = {
        "address": address,
        "server_name": "host.docker.internal",
        "identity_dir": str(tmp_path / "identity"),
    }
    if local_service != "absent":
        vault["local_service"] = local_service
    return {
        "context": {"tenant": "demo-tenant", "project": "demo-project"},
        "platform": {"services": {"proc": {"exec": {"py_code_exec_network_mode": "auto"}}}},
        "secrets": {
            "provider": "secrets-file",
            "service": {"backend": "host-vault", "host_vault": vault},
        },
    }


def _vault_home(tmp_path: Path) -> Path:
    home = tmp_path / "vault-home"
    (home / "tls").mkdir(parents=True)
    (home / "tls" / "server.crt").write_text("fixture", encoding="utf-8")
    return home


def _source_tree(tmp_path: Path) -> Path:
    ai_app_root = tmp_path / "ai-app"
    server = ai_app_root / VAULT_SERVER_RELATIVE
    server.parent.mkdir(parents=True)
    server.write_text("# fixture entrypoint\n", encoding="utf-8")
    return ai_app_root


def _python(tmp_path: Path) -> Path:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    return python


def test_disabled_backend_needs_no_vault(tmp_path):
    assembly = {
        "context": {"tenant": "demo-tenant", "project": "demo-project"},
        "secrets": {"provider": "secrets-file"},
    }

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=tmp_path,
        probe=lambda host, port: pytest.fail("a disabled backend must not be probed"),
    )

    assert result.state == "disabled"
    assert result.describe() == ""


def test_docker_host_alias_is_probed_on_loopback(tmp_path):
    assert probe_target("host.docker.internal:7781", None) == ("127.0.0.1", 7781)
    assert probe_target("vault.internal.example:7781", None) == ("vault.internal.example", 7781)


def test_reachable_vault_is_left_alone(tmp_path):
    assembly = _assembly(tmp_path)

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=tmp_path,
        probe=lambda host, port: True,
        spawn=lambda *args: pytest.fail("a reachable vault must not be started again"),
    )

    assert (result.state, result.probe_host, result.port) == ("reachable", "127.0.0.1", 7781)


def test_unreachable_vault_without_local_service_names_the_consequence(tmp_path):
    # The regression: after a host reboot nothing listened on 7781, the broker
    # answered 503 backend_unavailable, and Compose left ingress and proc in
    # Created with no error. The start command must say this itself.
    assembly = _assembly(tmp_path)

    with pytest.raises(HostVaultConfigurationError) as raised:
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=tmp_path,
            probe=lambda host, port: False,
        )

    message = str(raised.value)
    assert "host.docker.internal:7781" in message
    assert "127.0.0.1:7781" in message
    assert "Created" in message
    assert "local_service" in message


def test_null_local_service_means_not_declared(tmp_path):
    for platform in ("darwin", "linux", "win32"):
        assert (
            local_service_from_assembly(_assembly(tmp_path, local_service=None), platform=platform)
            is None
        )


def test_local_service_requires_absolute_home_and_python(tmp_path):
    with pytest.raises(HostVaultConfigurationError, match="requires 'home'"):
        local_service_from_assembly(
            _assembly(tmp_path, local_service={"home": str(tmp_path)}), platform="darwin"
        )
    with pytest.raises(HostVaultConfigurationError, match="absolute host paths"):
        local_service_from_assembly(
            _assembly(tmp_path, local_service={"home": "vault-home", "python": "bin/python"}),
            platform="darwin",
        )


def test_declared_local_service_is_started_when_nothing_listens(tmp_path):
    home = _vault_home(tmp_path)
    python = _python(tmp_path)
    ai_app_root = _source_tree(tmp_path)
    assembly = _assembly(tmp_path, local_service={"home": str(home), "python": str(python)})
    spawned: list[dict] = []
    answers = iter([False, False, True])

    def spawn(command, env, cwd, log_path):
        spawned.append({"command": command, "env": dict(env), "cwd": cwd, "log": log_path})
        return 4242

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=ai_app_root,
        probe=lambda host, port: next(answers),
        spawn=spawn,
        timeout_seconds=5,
        platform="darwin",
    )

    assert (result.state, result.pid, result.probe_host, result.port) == ("started", 4242, "127.0.0.1", 7781)
    assert "pid 4242" in result.describe()
    assert len(spawned) == 1
    call = spawned[0]
    assert call["command"] == [str(python), str(ai_app_root / VAULT_SERVER_RELATIVE)]
    assert call["env"]["KDCUBE_HOST_VAULT_HOME"] == str(home)
    assert call["env"]["KDCUBE_HOST_VAULT_BIND"] == "127.0.0.1"
    assert call["env"]["KDCUBE_HOST_VAULT_PORT"] == "7781"
    assert call["env"]["PYTHONPATH"] == str(ai_app_root / "src" / "kdcube-ai-app")
    assert call["log"] == home / "service.log"
    assert (home / "service.pid").read_text(encoding="utf-8").strip() == "4242"


def test_wildcard_bind_is_probed_on_loopback(tmp_path):
    home = _vault_home(tmp_path)
    local = local_service_from_assembly(
        _assembly(tmp_path, local_service={"home": str(home), "python": str(_python(tmp_path)), "bind": "0.0.0.0"}),
        platform="darwin",
    )

    assert probe_target("host.docker.internal:7781", local) == ("127.0.0.1", 7781)


def test_started_vault_that_never_listens_reports_its_log(tmp_path):
    home = _vault_home(tmp_path)
    (home / "service.log").write_text("ssl.SSLError: bad server key\n", encoding="utf-8")
    assembly = _assembly(tmp_path, local_service={"home": str(home), "python": str(_python(tmp_path))})

    with pytest.raises(HostVaultConfigurationError) as raised:
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=_source_tree(tmp_path),
            probe=lambda host, port: False,
            spawn=lambda *args: 99,
            timeout_seconds=0.3,
            platform="darwin",
        )

    assert "did not listen on 127.0.0.1:7781" in str(raised.value)
    assert "bad server key" in str(raised.value)
    assert not (home / "service.pid").exists()


def test_local_service_refuses_an_uninitialized_home_and_a_missing_checkout(tmp_path):
    python = _python(tmp_path)
    bare_home = tmp_path / "bare-home"
    bare_home.mkdir()
    assembly = _assembly(tmp_path, local_service={"home": str(bare_home), "python": str(python)})
    with pytest.raises(HostVaultConfigurationError, match="not an initialized"):
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=_source_tree(tmp_path),
            probe=lambda host, port: False,
            spawn=lambda *args: pytest.fail("must not start against an uninitialized home"),
            platform="darwin",
        )

    assembly = _assembly(tmp_path, local_service={"home": str(_vault_home(tmp_path)), "python": str(python)})
    with pytest.raises(HostVaultConfigurationError, match="entrypoint is missing"):
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=tmp_path / "no-checkout",
            probe=lambda host, port: False,
            spawn=lambda *args: pytest.fail("must not start without the entrypoint"),
            platform="darwin",
        )


def test_disabled_backend_ignores_a_declared_local_service_on_every_os(tmp_path):
    # Gate 1: without the host-vault backend nothing is validated, probed, or
    # started, whatever the block says and whatever the operating system is.
    assembly = _assembly(
        tmp_path, local_service={"home": "relative", "python": "relative"}
    )
    assembly["secrets"]["service"]["backend"] = "ephemeral"
    for platform in ("darwin", "linux", "win32"):
        result = ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=tmp_path,
            probe=lambda host, port: pytest.fail("a disabled backend must not be probed"),
            spawn=lambda *args: pytest.fail("a disabled backend must not start anything"),
            platform=platform,
        )
        assert result.state == "disabled"


def test_windows_refuses_local_service_and_starts_nothing(tmp_path):
    assembly = _assembly(
        tmp_path,
        local_service={"home": str(_vault_home(tmp_path)), "python": str(_python(tmp_path))},
    )

    with pytest.raises(HostVaultConfigurationError, match="not supported on Windows"):
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=_source_tree(tmp_path),
            probe=lambda host, port: False,
            spawn=lambda *args: pytest.fail("Windows must not start a vault process"),
            platform="win32",
        )


def test_windows_still_checks_a_vault_it_does_not_own(tmp_path):
    assembly = _assembly(tmp_path)

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=tmp_path,
        probe=lambda host, port: True,
        platform="win32",
    )

    assert result.state == "reachable"


def test_linux_requires_an_explicit_bind(tmp_path):
    # Compose maps host.docker.internal to host-gateway. Under Docker Engine a
    # loopback listener is unreachable from the broker, so a silent loopback
    # default would report "started" and still leave the stack in Created.
    assembly = _assembly(
        tmp_path,
        local_service={"home": str(_vault_home(tmp_path)), "python": str(_python(tmp_path))},
    )

    with pytest.raises(HostVaultConfigurationError) as raised:
        ensure_host_vault_running(
            config_from_assembly(assembly),
            assembly,
            ai_app_root=_source_tree(tmp_path),
            probe=lambda host, port: False,
            spawn=lambda *args: pytest.fail("must not start with an undecided bind"),
            platform="linux",
        )

    assert "bind is required on Linux" in str(raised.value)
    assert "172.17.0.1" in str(raised.value)


def test_linux_starts_on_the_declared_bridge_address(tmp_path):
    home = _vault_home(tmp_path)
    assembly = _assembly(
        tmp_path,
        local_service={"home": str(home), "python": str(_python(tmp_path)), "bind": "172.17.0.1"},
    )
    probed: list[tuple[str, int]] = []
    spawned: list[dict] = []

    def probe(host, port):
        probed.append((host, port))
        return len(probed) > 1

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=_source_tree(tmp_path),
        probe=probe,
        spawn=lambda command, env, cwd, log: spawned.append(dict(env)) or 77,
        timeout_seconds=5,
        platform="linux",
    )

    assert result.state == "started"
    assert set(probed) == {("172.17.0.1", 7781)}
    assert spawned[0]["KDCUBE_HOST_VAULT_BIND"] == "172.17.0.1"


def test_unreachable_vault_on_another_machine_warns_and_does_not_block(tmp_path):
    # The CLI sees the network from the host, the broker from a container. For a
    # vault elsewhere a failed probe is not proof, so start is not refused.
    assembly = _assembly(tmp_path, address="vault.internal.example:7781")

    result = ensure_host_vault_running(
        config_from_assembly(assembly),
        assembly,
        ai_app_root=tmp_path,
        probe=lambda host, port: False,
        spawn=lambda *args: pytest.fail("a vault on another machine is never started here"),
    )

    assert result.state == "unverified"
    assert "vault.internal.example:7781" in result.describe()
    assert "Created" in result.describe()


def test_tcp_probe_sees_a_real_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert tcp_probe("127.0.0.1", port) is True
    assert tcp_probe("127.0.0.1", port) is False
