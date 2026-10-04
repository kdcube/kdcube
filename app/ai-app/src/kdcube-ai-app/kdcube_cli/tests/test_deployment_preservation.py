# SPDX-License-Identifier: MIT
"""Synthetic Docker boundaries only: these tests never contact a daemon."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from kdcube_cli.control.execution import CommandResult


def image_id(name: str) -> str:
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


class Docker:
    def __init__(self):
        self.config = {
            "name": "example",
            "services": {
                "postgres-db": {"image": "postgres:16", "volumes": ["data:/db"]},
                "postgres-setup": {"image": "kdcube-setup:latest"},
                "chat-proc": {
                    "image": "kdcube-proc:latest", "build": {"context": "."},
                    "environment": {"TOKEN": "synthetic-secret", "PY_CODE_EXEC_IMAGE": "executor:latest"},
                },
                "proxylogin": {"image": "kdcube-proxy:latest", "profiles": ["proxylogin"]},
            },
            "volumes": {"data": {}},
        }
        self.images = {v["image"]: image_id(k) for k, v in self.config["services"].items()}
        self.images["executor:latest"] = image_id("executor")
        for value in list(self.images.values()):
            self.images[value] = value
        self.containers = {}
        self.commands = []
        self.fail_tag = None
        self.override = None
        self.working_dir = ""

    @property
    def mutations(self):
        return [c for c in self.commands if c[:3] in [("docker", "image", "tag"), ("docker", "image", "rm")]
                or "down" in c or "up" in c]

    def run(self, command, **kwargs):
        c = tuple(map(str, command))
        self.commands.append(c)
        if c[:2] == ("docker", "info"):
            return CommandResult(0, "synthetic-daemon-one\n")
        if c[:3] == ("docker", "image", "inspect"):
            value = self.images.get(c[-1])
            return CommandResult(0 if value else 1, (value or "") + "\n")
        if c[:3] == ("docker", "image", "tag"):
            if c[-1] == self.fail_tag:
                return CommandResult(1, stderr="injected tag failure")
            self.images[c[-1]] = self.images[c[-2]]
            return CommandResult(0)
        if c[:3] == ("docker", "image", "rm"):
            self.images.pop(c[-1], None)
            return CommandResult(0)
        if c[:3] == ("docker", "ps", "-a"):
            return CommandResult(0, "\n".join(self.containers))
        if c[:2] == ("docker", "inspect"):
            return CommandResult(0, json.dumps([self.containers[c[-1]]]))
        if c[:2] == ("docker", "compose"):
            if "config" in c:
                return CommandResult(0, json.dumps(self.config))
            if "ps" in c:
                return CommandResult(0, "\n".join(k for k, v in self.containers.items()
                                                  if v["Config"]["Labels"].get("com.docker.compose.project") == "example"))
            if "down" in c:
                self.containers = {k: v for k, v in self.containers.items()
                                   if v["Config"]["Labels"].get("com.docker.compose.project") != "example"}
                return CommandResult(0)
            if "up" in c:
                files = [c[i+1] for i, value in enumerate(c) if value == "-f"]
                self.override = (json.loads(Path(files[-1]).read_text()) if len(files) > 1 else
                                 {"services": {name: details for name, details in self.config["services"].items()
                                               if not details.get("profiles") or "proxylogin" in c}})
                for service, details in self.override["services"].items():
                    self.add_container(service, details["image"])
                return CommandResult(0)
        raise AssertionError(f"Unexpected synthetic Docker command: {c}")

    def add_container(self, service, reference, *, project="example"):
        self.containers[service] = {
            "Image": self.images[reference],
            "Config": {"Image": reference, "Env": [], "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                "com.docker.compose.project.working_dir": self.working_dir,
            }},
        }


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env").write_text("TOKEN=synthetic-secret\n")
    (config / "assembly.yaml").write_text("context: {tenant: example, project: app}\n")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    (docker_dir / "docker-compose.yaml").write_text("services: {}\n")
    docker = Docker()
    docker.working_dir = str(docker_dir)
    return tmp_path, docker_dir, docker


def engine(setup, **kwargs):
    module = importlib.import_module("kdcube_cli.deployment_preservation")
    workdir, docker_dir, docker = setup
    return module.DeploymentPreservation(workdir=workdir, docker_dir=docker_dir,
                                        env_file=workdir / "config/.env", runner=docker, **kwargs)


def native_start_stub(e, docker):
    # A synthetic stand-in for the owning native lifecycle. Native controller
    # integration and its flags get a separate regression gate.
    return lambda override: docker.run([*e._compose(override=override), "up", "-d", "--no-build", "--pull", "never"])


def test_native_cleanup_retains_reserved_hold_tags(monkeypatch):
    from kdcube_cli import cli
    removed = []
    monkeypatch.setattr(cli, "_ensure_docker_responsive", lambda: None)
    monkeypatch.setattr(cli, "_docker_run", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_docker_output_soft", lambda *a, **kw: "")
    monkeypatch.setattr(cli, "_docker_output", lambda *a, **kw:
                        f"{image_id('old')} kdcube-preservation/owner baseline\n"
                        f"{image_id('unused')} kdcube-unused latest\n")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kw: removed.extend(command[2:]))
    cli.clean_docker_images(Console(file=io.StringIO()))
    assert removed == ["kdcube-unused:latest"]


def test_complete_map_protects_database_setup_profiles_and_executor_without_credentials(setup):
    e = engine(setup, profiles=("proxylogin",))
    manifest = e.prepare(key="window-one", owner="operator", consumers=["qualification"])
    assert set(manifest["services"]) == {"postgres-db", "postgres-setup", "chat-proc", "proxylogin"}
    assert manifest["auxiliary"]["py-code-exec"]["image_id"] == image_id("executor")
    assert manifest["state"] == "protected"
    serialized = json.dumps(manifest)
    assert "synthetic-secret" not in serialized and "TOKEN" not in serialized
    assert all(setup[2].images[row["hold_ref"]] == row["image_id"]
               for row in [*manifest["services"].values(), *manifest["auxiliary"].values()])


def test_disabled_profile_is_explicitly_excluded_and_not_restored(setup):
    e = engine(setup)
    m = e.prepare(key="no-proxy", owner="operator")
    assert m["excluded_profiles"] == {"proxylogin": ["proxylogin"]}
    e.restore(m["manifest_id"], start_stack=native_start_stub(e, setup[2]))
    assert "proxylogin" not in setup[2].override["services"]


def test_missing_baseline_image_refuses_before_first_mutation(setup):
    setup[2].images.pop("postgres:16")
    with pytest.raises(RuntimeError, match="image_missing"):
        engine(setup).prepare(key="missing", owner="operator")
    assert not setup[2].mutations


def test_live_container_id_wins_over_a_moved_latest_tag(setup):
    docker = setup[2]
    docker.add_container("chat-proc", "kdcube-proc:latest")
    docker.images["kdcube-proc:latest"] = image_id("new-candidate")
    docker.images[image_id("new-candidate")] = image_id("new-candidate")
    m = engine(setup).prepare(key="running", owner="operator")
    assert m["services"]["chat-proc"]["image_id"] == image_id("chat-proc")


def test_interrupted_protection_retains_partial_holds_and_retries_same_manifest(setup):
    e = engine(setup)
    m = e.prepare(key="retry", owner="operator", protect=False)
    rows = [*m["services"].values(), *m["auxiliary"].values()]
    setup[2].fail_tag = rows[1]["hold_ref"]
    with pytest.raises(RuntimeError, match="command_failed"):
        e.protect(m["manifest_id"])
    assert setup[2].images[rows[0]["hold_ref"]] == rows[0]["image_id"]
    assert e.status(m["manifest_id"])["state"] == "preparing"
    setup[2].fail_tag = None
    protected = e.prepare(key="retry", owner="operator")
    assert protected["manifest_id"] == m["manifest_id"] and protected["state"] == "protected"
    before = len(setup[2].mutations)
    e.protect(m["manifest_id"])
    assert len(setup[2].mutations) == before


@pytest.mark.parametrize("failure", ["id", "hold", "configuration", "foreign", "incomplete"])
def test_restore_preflight_refuses_before_disruption(setup, failure):
    e = engine(setup)
    m = e.prepare(key="refusal", owner="operator")
    docker = setup[2]
    if failure == "id":
        docker.images.pop(image_id("postgres-db"))
    elif failure == "hold":
        docker.images[m["services"]["postgres-db"]["hold_ref"]] = image_id("other")
    elif failure == "configuration":
        docker.config["services"]["postgres-db"]["volumes"] = ["other:/db"]
    elif failure == "foreign":
        foreign = setup[0] / "foreign"
        (foreign / "config").mkdir(parents=True)
        (foreign / "config/assembly.yaml").write_text("context: {tenant: other, project: app}\n")
        e = engine((foreign, setup[1], docker))
        e.manifest_path(m["manifest_id"]).parent.mkdir(parents=True)
        e.manifest_path(m["manifest_id"]).write_text(json.dumps(m))
    else:
        path = e.manifest_path(m["manifest_id"])
        payload = json.loads(path.read_text())
        payload["services"].pop("postgres-setup")
        path.write_text(json.dumps(payload))
    before = list(docker.mutations)
    with pytest.raises(RuntimeError):
        e.restore(m["manifest_id"])
    assert docker.mutations == before


def test_restore_uses_exact_complete_map_no_build_no_pull_and_attests_ids(setup):
    e = engine(setup)
    m = e.prepare(key="restore", owner="operator")
    docker = setup[2]
    docker.images["kdcube-proc:latest"] = image_id("new")
    docker.images["executor:latest"] = image_id("new-executor")
    result = e.restore(m["manifest_id"], start_stack=native_start_stub(e, docker))
    assert result["restored_ids"] == {k: v["image_id"] for k, v in m["services"].items()}
    assert docker.images["executor:latest"] == image_id("executor")
    command = next(c for c in docker.commands if "up" in c)
    assert "--no-build" in command and command[command.index("--pull")+1] == "never"
    assert not any(c[:2] == ("docker", "build") or "--build" in c or "-v" in c for c in docker.commands)


def test_foreign_executor_consumer_refuses_retag_before_stop(setup):
    e = engine(setup)
    m = e.prepare(key="consumer", owner="operator")
    setup[2].add_container("foreign-proc", "executor:latest", project="other")
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="foreign_consumer"):
        e.restore(m["manifest_id"])
    assert setup[2].mutations == before


def test_descriptor_executor_wins_over_compose_environment_and_all_bundle_profiles_are_bound(setup):
    (setup[0] / "config/assembly.yaml").write_text(
        "platform: {services: {proc: {exec: {py_code_exec_image: 'descriptor:latest'}}}}\n")
    (setup[0] / "config/bundles.yaml").write_text(
        "bundles:\n  items:\n    - id: app\n      config:\n        execution:\n          runtime:\n"
        "            profiles:\n              small: {mode: docker, image: 'small:latest'}\n"
        "              large: {mode: docker, docker_image: 'large:latest'}\n")
    docker = setup[2]
    for name in ("descriptor", "small", "large"):
        docker.images[f"{name}:latest"] = image_id(name)
        docker.images[image_id(name)] = image_id(name)
    e = engine(setup)
    m = e.prepare(key="profiles", owner="operator")
    assert m["auxiliary"]["py-code-exec"]["image_id"] == image_id("descriptor")
    assert m["auxiliary"]["bundle/app/profile/small"]["image_id"] == image_id("small")
    assert m["auxiliary"]["bundle/app/profile/large"]["image_id"] == image_id("large")
    docker.images["small:latest"] = image_id("candidate-small")
    restored = e.restore(m["manifest_id"], start_stack=native_start_stub(e, docker))
    assert docker.images["small:latest"] == image_id("small")
    assert restored["auxiliary"]["bundle/app/profile/small"]["image_id"] == image_id("small")


@pytest.mark.parametrize("runtime", ["{mode: fargate, image: 'remote:latest'}", "{profiles: {broken: nope}}"])
def test_unsupported_executor_evidence_refuses_before_mutation(setup, runtime):
    (setup[0] / "config/bundles.yaml").write_text(
        "bundles: {items: [{id: app, config: {execution: {runtime: " + runtime + "}}}]}\n")
    with pytest.raises(RuntimeError, match="executor_"):
        engine(setup).prepare(key="unsupported", owner="operator")
    assert not setup[2].mutations


def test_native_secret_startup_cannot_be_bypassed_with_base_env_only(setup):
    setup[2].config["services"]["chat-proc"]["environment"]["SECRETS_TOKEN"] = "synthetic-runtime-token"
    e = engine(setup)
    m = e.prepare(key="native-start", owner="operator")
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="native_start_required"):
        e.restore(m["manifest_id"])
    assert setup[2].mutations == before


def test_same_operation_key_cannot_silently_drop_a_consumer(setup):
    e = engine(setup)
    e.prepare(key="same-key", owner="operator", consumers=["window"])
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="preparation_conflict"):
        e.prepare(key="same-key", owner="operator", consumers=[])
    assert setup[2].mutations == before


def test_status_is_nonmutating_and_release_requires_closed_consumers_and_independent_evidence(setup):
    e = engine(setup)
    m = e.prepare(key="release", owner="author", consumers=["window"])
    before = list(setup[2].mutations)
    assert e.status(m["manifest_id"])["state"] == "protected"
    assert setup[2].mutations == before
    qualification = {"manifest_id": m["manifest_id"], "binding_sha256": m["binding_sha256"],
                     "owner": "author", "qualified_by": "independent", "all_clear": True,
                     "evidence_ref": "qualification-result-one"}
    with pytest.raises(RuntimeError, match="consumer_open"):
        e.release(m["manifest_id"], qualification=qualification)
    e.consumer_done(m["manifest_id"], consumer="window", evidence_ref="window-all-clear")
    with pytest.raises(RuntimeError, match="qualification_invalid"):
        e.release(m["manifest_id"], qualification={**qualification, "qualified_by": "author"})
    setup[2].images["unrelated:backup"] = image_id("backup")
    setup[2].config["services"]["postgres-db"]["volumes"] = ["new-qualified-data:/db"]
    result = e.release(m["manifest_id"], qualification=qualification)
    assert result["state"] == "released"
    assert setup[2].images["unrelated:backup"] == image_id("backup")
    assert not any("prune" in c or "volume" in c or "-f" in c for c in setup[2].commands if c[:3] == ("docker", "image", "rm"))
    before = list(setup[2].mutations)
    e.release(m["manifest_id"], qualification=qualification)
    assert setup[2].mutations == before


def test_automatic_protection_skips_only_a_fresh_deployment_without_baseline_evidence(setup):
    e = engine(setup)
    assert e.prepare_current() is None
    assert not setup[2].mutations
    setup[2].add_container("chat-proc", "kdcube-proc:latest")
    setup[2].images.pop("postgres:16")
    with pytest.raises(RuntimeError, match="image_missing"):
        e.prepare_current()
    assert not setup[2].mutations


def test_existing_protected_history_cannot_be_misclassified_as_fresh_after_containers_disappear(setup):
    e = engine(setup)
    m = e.prepare(key="prior-baseline", owner="operator")
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="baseline_evidence_missing"):
        e.prepare_current()
    assert e.status(m["manifest_id"])["state"] == "protected"
    assert setup[2].mutations == before


def test_automatic_protection_reuses_complete_current_baseline_and_retains_after_candidate_config_changes(setup):
    e = engine(setup)
    docker = setup[2]
    docker.add_container("chat-proc", "kdcube-proc:latest")
    first = e.prepare_current()
    assert first["consumers"] == {"rollback": {"state": "open"}}
    before = list(docker.mutations)
    assert e.prepare_current()["manifest_id"] == first["manifest_id"]
    assert docker.mutations == before
    docker.config["services"]["postgres-db"]["volumes"] = ["candidate:/db"]
    assert e.retain(first["manifest_id"])["state"] == "protected"
    assert docker.mutations == before
    with pytest.raises(RuntimeError, match="configuration_mismatch"):
        e.restore(first["manifest_id"], start_stack=native_start_stub(e, docker))


def test_stopped_deployment_with_incomplete_built_image_receipts_fails_closed(setup):
    workdir, _, docker = setup
    (workdir / ".kdcube").mkdir()
    (workdir / ".kdcube/deployment-images.v1.json").write_text(json.dumps({
        "latest_services": {"postgres-setup": {
            "image": "kdcube-setup:latest", "image_id": image_id("postgres-setup"),
        }},
    }))
    with pytest.raises(RuntimeError, match="baseline_incomplete"):
        engine(setup).prepare_current()
    assert not docker.mutations


@pytest.mark.parametrize("failure", ["build", "receipt", "none"])
def test_cli_build_protects_before_maintenance_and_retains_through_finally(setup, monkeypatch, failure):
    from types import SimpleNamespace
    from kdcube_cli import cli
    e = engine(setup)
    setup[2].add_container("chat-proc", "kdcube-proc:latest")
    monkeypatch.setattr(cli, "_build_paths_for_repo", lambda *a: SimpleNamespace(
        config_dir=setup[0] / "config", docker_dir=setup[1]))
    monkeypatch.setattr(cli, "_ensure_docker_responsive", lambda: None)
    monkeypatch.setattr(cli, "DeploymentPreservation", lambda **kw: e)
    monkeypatch.setattr(cli.installer_mod, "missing_build_keys", lambda env: [])
    events = []

    def maintenance(console, *, phase):
        assert any("tag" in c for c in setup[2].mutations)
        events.append(phase)

    def build(console, command, **kwargs):
        assert events[0] == "before"
        events.append("build")
        if failure == "build":
            raise SystemExit("synthetic failed build")

    def receipt(**kwargs):
        if failure == "receipt":
            raise cli.DeploymentProvenanceError("synthetic receipt failure")

    monkeypatch.setattr(cli, "_maintain_docker_build_storage", maintenance)
    monkeypatch.setattr(cli, "_run_compose", build)
    monkeypatch.setattr(cli, "record_compose_image_receipts", receipt)
    if failure == "none":
        cli.build_compose_images(Console(quiet=True), repo_root=setup[0], workdir=setup[0])
    else:
        expected = SystemExit if failure == "build" else cli.ImageReceiptError
        with pytest.raises(expected):
            cli.build_compose_images(Console(quiet=True), repo_root=setup[0], workdir=setup[0])
    assert events[-1] == "after"
    assert not any(c[:3] == ("docker", "image", "rm") for c in setup[2].mutations)
    assert e.prepare_current()["state"] == "protected"


def test_native_controller_protects_before_up_and_down_with_real_preservation_fake(setup, monkeypatch):
    from types import SimpleNamespace
    from kdcube_cli.control import local_lifecycle
    from kdcube_cli.control.models import DeploymentTargetRef, LocalStartRequest, LocalStopRequest
    workdir, docker_dir, docker = setup
    docker.add_container("chat-proc", "kdcube-proc:latest")
    monkeypatch.setattr(local_lifecycle, "validate_host_vault_assembly_for_start", lambda *a, **kw: None)
    monkeypatch.setattr(local_lifecycle, "ensure_host_vault_running",
                        lambda *a, **kw: SimpleNamespace(describe=lambda: ""))
    monkeypatch.setattr(local_lifecycle.installer_mod, "generate_runtime_tokens",
                        lambda: {"SECRETS_ADMIN_TOKEN": "synthetic-only"})
    controller = local_lifecycle.LocalLifecycleController(
        DeploymentTargetRef.local(workdir),
        SimpleNamespace(workdir=workdir, docker_dir=docker_dir, config_dir=workdir / "config",
                        ai_app_root=workdir), runner=docker, lock_file=workdir / "lock.json",
        stream_process_output=False,
    )
    controller.start(LocalStartRequest(build=True), event_sink=None)
    first_up = next(i for i, c in enumerate(docker.commands) if "up" in c)
    assert any(c[:3] == ("docker", "image", "tag") for c in docker.commands[:first_up])
    controller.stop(LocalStopRequest(), event_sink=None)
    assert not any("-v" in c for c in docker.commands)
    assert engine(setup).prepare(key="another", owner="operator")["state"] == "protected"
    assert not list((workdir / "config").glob("*.runtime*"))


def test_workdir_operation_refuses_concurrent_mutation_and_reuses_nested_owner(setup):
    from concurrent.futures import ThreadPoolExecutor
    from kdcube_cli.deployment_preservation import deployment_operation
    e = engine(setup)
    setup[2].add_container("chat-proc", "kdcube-proc:latest")
    with deployment_operation(setup[0]):
        manifest = e.prepare_current()
        assert e.retain(manifest["manifest_id"])["state"] == "protected"
        before = list(setup[2].commands)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(RuntimeError, match="operation_in_progress"):
                executor.submit(e.prepare_current).result(timeout=5)
        assert setup[2].commands == before
    assert e.prepare_current()["state"] == "protected"


def test_preservation_cli_prepare_and_recorded_status_use_native_engine_without_secret_output(setup, monkeypatch, capsys):
    from types import SimpleNamespace
    from kdcube_cli import cli
    e = engine(setup)
    monkeypatch.setattr(cli, "DeploymentPreservation", lambda **kw: e)
    monkeypatch.setattr(cli, "_build_paths_for_repo", lambda *a: SimpleNamespace(
        config_dir=setup[0] / "config", docker_dir=setup[1]))
    common = ["--workdir", str(setup[0]), "--path", str(setup[0]), "--json", "--quiet"]
    monkeypatch.setattr(cli.sys, "argv", ["kdcube", "preservation", "prepare", *common,
                                        "--key", "cli-one", "--owner", "operator", "--consumer", "qualification"])
    cli.main()
    text = capsys.readouterr().out
    prepared = json.loads(text)
    assert "synthetic-secret" not in text and prepared["state"] == "protected"
    commands = list(setup[2].commands)
    manifest_before = e.manifest_path(prepared["manifest_id"]).read_bytes()
    monkeypatch.setattr(cli.sys, "argv", ["kdcube", "preservation", "status", *common,
                                        "--manifest", prepared["manifest_id"]])
    cli.main()
    assert json.loads(capsys.readouterr().out)["manifest_id"] == prepared["manifest_id"]
    assert setup[2].commands == commands
    assert e.manifest_path(prepared["manifest_id"]).read_bytes() == manifest_before


@pytest.mark.parametrize("field,value", [("services", []), ("consumers", {"rollback": None}),
                                         ("identity", "wrong-shape")])
def test_rehashed_but_malformed_manifest_has_named_refusal_before_mutation(setup, field, value):
    from kdcube_cli import deployment_preservation as preservation
    e = engine(setup)
    m = e.prepare(key="malformed", owner="operator")
    m[field] = value
    m["binding_sha256"] = preservation._digest({k: m[k] for k in preservation.IMMUTABLE_FIELDS})
    e.manifest_path(m["manifest_id"]).write_text(json.dumps(m))
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="manifest_invalid"):
        e.restore(m["manifest_id"], start_stack=native_start_stub(e, setup[2]))
    assert setup[2].mutations == before


def test_changed_executor_profile_map_refuses_before_disruption(setup):
    e = engine(setup)
    m = e.prepare(key="profile-change", owner="operator")
    (setup[0] / "config/bundles.yaml").write_text(
        "bundles: {items: [{id: app, config: {execution: {runtime: {image: 'out-of-map:latest'}}}}]}\n")
    before = list(setup[2].mutations)
    with pytest.raises(RuntimeError, match="configuration_mismatch"):
        e.restore(m["manifest_id"], start_stack=native_start_stub(e, setup[2]))
    assert setup[2].mutations == before


@pytest.mark.parametrize("failure", ["none", "build", "receipt"])
def test_refresh_protects_installed_source_before_copy_config_stop_and_build(setup, monkeypatch, failure):
    from types import SimpleNamespace
    from kdcube_cli import cli
    workdir, docker_dir, docker = setup
    e = engine(setup)
    docker.add_container("chat-proc", "kdcube-proc:latest")
    installed_repo = workdir / "installed-source"
    candidate_repo = workdir / "candidate-source"
    events = []
    seen_manifest = []
    monkeypatch.setattr(cli, "DeploymentPreservation", lambda **kw: e)
    monkeypatch.setattr(cli, "_build_paths_for_repo", lambda *a: SimpleNamespace(
        config_dir=workdir / "config", docker_dir=docker_dir))
    monkeypatch.setattr(cli, "_canonical_descriptor_dir_from_initialized_workdir", lambda w: workdir / "config")
    monkeypatch.setattr(cli, "_resolve_namespaced_runtime_target", lambda **kw: (workdir, "example", "app"))
    monkeypatch.setattr(cli, "_resolve_subcommand_repo", lambda *a, path_provided=False, **kw:
                        candidate_repo if path_provided else installed_repo)

    def source_copy(console, *, source_repo, workdir):
        assert source_repo == candidate_repo
        assert any(c[:3] == ("docker", "image", "tag") for c in docker.mutations)
        events.append("copy")
        return installed_repo

    def config_change(*a, **kw):
        assert events == ["copy"]
        events.append("config")

    def stop(*a, preservation_manifest, **kw):
        assert events == ["copy", "config"]
        seen_manifest.append(preservation_manifest)
        assert e.retain(preservation_manifest)["state"] == "protected"
        events.append("stop")

    def build(*a, preservation_manifest, **kw):
        assert preservation_manifest == seen_manifest[0]
        events.append("build")
        if failure == "build":
            raise SystemExit("synthetic build failure")
        if failure == "receipt":
            raise cli.ImageReceiptError("synthetic receipt failure")

    monkeypatch.setattr(cli, "_copy_dirty_local_source", source_copy)
    monkeypatch.setattr(cli, "_refresh_runtime_proxy_config", config_change)
    monkeypatch.setattr(cli.installer_mod, "ensure_generated_runtime_secrets", lambda *a: False)
    monkeypatch.setattr(cli, "stop_compose_stack", stop)
    monkeypatch.setattr(cli, "build_compose_images", build)
    monkeypatch.setattr(cli, "start_compose_stack", lambda *a, **kw: pytest.fail("--no-restart must not start"))
    monkeypatch.setattr(cli.sys, "argv", ["kdcube", "refresh", "--workdir", str(workdir),
                                        "--path", str(candidate_repo), "--build", "--no-restart", "--quiet"])
    if failure == "none":
        cli.main()
    else:
        with pytest.raises(SystemExit, match="synthetic"):
            cli.main()
    assert events == ["copy", "config", "stop", "build"]
    assert e.status(seen_manifest[0])["state"] == "protected"


def test_partial_release_is_durable_and_retry_removes_only_remaining_owned_tags(setup, monkeypatch):
    e = engine(setup)
    m = e.prepare(key="release-retry", owner="author")
    qualification = {"manifest_id": m["manifest_id"], "binding_sha256": m["binding_sha256"],
                     "owner": "author", "qualified_by": "independent", "all_clear": True,
                     "evidence_ref": "all-clear"}
    docker = setup[2]
    original_run = docker.run
    second = list(m["services"].values())[1]["hold_ref"]

    def interrupted(command, **kw):
        if list(command)[:3] == ["docker", "image", "rm"] and command[-1] == second:
            return CommandResult(1, stderr="synthetic interruption")
        return original_run(command, **kw)

    monkeypatch.setattr(docker, "run", interrupted)
    with pytest.raises(RuntimeError, match="command_failed"):
        e.release(m["manifest_id"], qualification=qualification)
    assert e.status(m["manifest_id"])["state"] == "releasing"
    first = list(m["services"].values())[0]["hold_ref"]
    assert first not in docker.images and second in docker.images
    monkeypatch.setattr(docker, "run", original_run)
    assert e.release(m["manifest_id"], qualification=qualification)["state"] == "released"
    assert sum(c[-1] == first for c in docker.commands if c[:3] == ("docker", "image", "rm")) == 1
