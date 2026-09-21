"""`kdcube bundle reload <id> --commit <ref>` pins on the host, sends the pin, and reads the receipt back (W209)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_cli import bundle_activation
from kdcube_cli import cli as cli_mod

BUNDLE_ID = "demo.bundle@1.0.0"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def host_repo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "host" / "applications"
    bundle = repo / "apps" / "demo"
    bundle.mkdir(parents=True)
    _git(repo, "init", "-q")
    (bundle / "entrypoint.py").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "A")
    commit_a = _git(repo, "rev-parse", "HEAD")
    (bundle / "entrypoint.py").write_text("B\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "B")
    commit_b = _git(repo, "rev-parse", "HEAD")
    return repo, bundle, commit_a, commit_b


def test_host_path_maps_the_container_path_under_the_bundles_root(tmp_path: Path):
    host_root = tmp_path / "host"
    assert bundle_activation.host_path_for("/bundles/kdcube/applications/apps/demo", host_root=host_root, container_root="/bundles") == host_root / "kdcube/applications/apps/demo"
    assert bundle_activation.host_path_for("/bundles", host_root=host_root, container_root="/bundles") == host_root
    assert bundle_activation.host_path_for("/elsewhere/demo", host_root=host_root, container_root="/bundles") is None
    assert bundle_activation.host_path_for("/bundles/x", host_root=None, container_root="/bundles") is None
    assert bundle_activation.host_path_for("", host_root=host_root, container_root="/bundles") is None


def test_the_payload_carries_the_ref_as_typed_and_the_sha_pinned_on_the_host(host_repo):
    repo, bundle, commit_a, commit_b = host_repo
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    payload, lines = bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit=branch, expect=None, host_path=bundle)

    assert payload == {"bundle_id": BUNDLE_ID, "commit": branch, "expected_commit": commit_b}
    assert lines == [f"Expected: {commit_b} ({branch} pinned in {bundle})"]

    short, _ = bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit=commit_a[:8], expect=None, host_path=bundle)
    assert short["expected_commit"] == commit_a


def test_an_explicit_expect_replaces_the_host_pin_and_must_be_a_full_sha(host_repo):
    repo, bundle, commit_a, commit_b = host_repo
    payload, lines = bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit="main", expect=commit_a, host_path=bundle)
    assert payload["expected_commit"] == commit_a and lines == [f"Expected: {commit_a} (from --expect)"]
    with pytest.raises(SystemExit, match="full 40-character"):
        bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit="main", expect="abc123", host_path=bundle)
    with pytest.raises(SystemExit, match="Pass --commit as well"):
        bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit=None, expect=commit_a, host_path=bundle)


def test_without_a_host_mapping_the_ref_goes_unpinned_and_the_line_says_so():
    payload, lines = bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit="main", expect=None, host_path=None)
    assert payload == {"bundle_id": BUNDLE_ID, "commit": "main"}
    assert lines == ["Commit: main, not pinned on this host (no host mapping for the bundle path). The proc's resolution stands."]


def test_an_unknown_ref_or_missing_path_exits_with_the_reason(host_repo, tmp_path: Path):
    repo, bundle, commit_a, commit_b = host_repo
    with pytest.raises(SystemExit, match="does not name a commit"):
        bundle_activation.pin_ref(bundle, "no-such-ref")
    with pytest.raises(SystemExit, match="does not exist on this host"):
        bundle_activation.pin_ref(tmp_path / "missing", "main")


def test_a_plain_reload_sends_only_the_bundle_id():
    payload, lines = bundle_activation.activation_payload(bundle_id=BUNDLE_ID, commit=None, expect=None, host_path=None)
    assert payload == {"bundle_id": BUNDLE_ID} and lines == []


def test_receipt_lines_name_the_snapshot_and_refuse_a_commit_that_is_not_the_pin():
    sha = "c" * 40
    result = {"activation": {"mode": "snapshot", "commit": sha, "ref": "main", "origin": "request", "durable": False, "path": "/managed/x/snapshots/" + sha}}
    lines = bundle_activation.activation_lines(result, {"bundle_id": BUNDLE_ID, "commit": "main", "expected_commit": sha})
    assert lines[0] == f"Loaded: snapshot of {sha[:12]} (main, request)"
    assert lines[1] == "Snapshot: /managed/x/snapshots/" + sha
    assert lines[2].startswith("Durable: no. The descriptor was not written")

    with pytest.raises(SystemExit, match="the fence did not hold"):
        bundle_activation.activation_lines(result, {"bundle_id": BUNDLE_ID, "commit": "main", "expected_commit": "d" * 40})


def test_receipt_lines_describe_the_mounted_tree_and_an_old_proc():
    head = "e" * 40
    local = {"activation": {"mode": "local-path", "path": "/bundles/demo", "head": head, "dirty": True, "changed_paths": ["apps/demo/x.py"]}}
    lines = bundle_activation.activation_lines(local, {"bundle_id": BUNDLE_ID})
    assert lines == [
        f"Loaded: mounted tree at head {head[:12]}, dirty (1 changed path under the bundle)",
        "Named by evidence at the moment of the read, not fenced: pass --commit to fence.",
    ]
    assert bundle_activation.activation_lines({}, {"bundle_id": BUNDLE_ID}) == [
        "Loaded: not named by this proc (receipt predates activation evidence)."
    ]
    with pytest.raises(SystemExit, match="predates commit activation"):
        bundle_activation.activation_lines({}, {"bundle_id": BUNDLE_ID, "commit": "main"})


def test_reload_command_pins_from_the_descriptor_path_and_prints_the_receipt(monkeypatch, tmp_path: Path, host_repo, capsys):
    repo, bundle, commit_a, commit_b = host_repo
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("TENANT=demo\n", encoding="utf-8")
    (config_dir / ".env.proc").write_text("PROJECT=project\n", encoding="utf-8")
    descriptor = config_dir / "bundles.yaml"
    descriptor.write_text(
        json.dumps({"bundles": {BUNDLE_ID: {"path": "/bundles/kdcube/applications/apps/demo", "module": "entrypoint"}}}),
        encoding="utf-8",
    )
    paths = SimpleNamespace(config_dir=config_dir, docker_dir=tmp_path / "docker")
    monkeypatch.setattr(cli_mod, "_build_paths_for_repo", lambda *_args: paths)
    monkeypatch.setattr(cli_mod, "_resolve_bundle_reload_source", lambda *_args: descriptor)
    monkeypatch.setattr(cli_mod, "_compose_running_services", lambda *_args: {"chat-proc"})
    monkeypatch.setattr(cli_mod, "_bundle_runtime_roots", lambda _workdir: (tmp_path / "host", "/bundles"))
    # The descriptor path /bundles/kdcube/applications/apps/demo maps to the host repo's bundle dir.
    (tmp_path / "host" / "kdcube").mkdir(parents=True, exist_ok=True)
    (tmp_path / "host" / "kdcube" / "applications").symlink_to(repo)
    sent: list[dict] = []

    def post(_console, **kwargs):
        sent.append(kwargs["payload"])
        return {
            "status": "ok",
            "bundle_id": BUNDLE_ID,
            "eviction": {"sys_modules_deleted": 2},
            "broadcast_receivers": 1,
            "activation": {"mode": "snapshot", "commit": commit_a, "ref": commit_a[:8], "origin": "request", "durable": False, "path": "/managed/snap"},
        }

    monkeypatch.setattr(cli_mod, "_post_local_bundle_control", post)

    result = cli_mod.reload_bundle_from_descriptor(
        cli_mod.Console(), repo_root=tmp_path, workdir=tmp_path, bundle_id=BUNDLE_ID, json_output=True, commit=commit_a[:8]
    )

    assert sent == [{"bundle_id": BUNDLE_ID, "commit": commit_a[:8], "expected_commit": commit_a}]
    messages = result["messages"]
    assert f"Loaded: snapshot of {commit_a[:12]} ({commit_a[:8]}, request)" in messages
    assert messages[-1] == "The next request will re-import that bundle from the snapshot above."
    assert json.loads(capsys.readouterr().out)["activation"]["commit"] == commit_a


def test_the_parsers_accept_commit_and_expect_on_both_reload_forms(monkeypatch):
    seen: dict[str, object] = {}

    def fake_reload(_console, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(cli_mod, "reload_bundle_from_descriptor", fake_reload)
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *a, **k: Path("/tmp/wd"))
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda w: w)
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_repo", lambda *a, **k: Path("/tmp/repo"))
    monkeypatch.setattr(cli_mod, "print_cli_banner", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda *a, **k: {}, raising=False)

    monkeypatch.setattr("sys.argv", ["kdcube", "reload", BUNDLE_ID, "--commit", "main", "--expect", "a" * 40, "--workdir", "/tmp/wd"])
    cli_mod.main()
    assert seen["commit"] == "main" and seen["expect"] == "a" * 40

    seen.clear()
    monkeypatch.setattr("sys.argv", ["kdcube", "bundle", "reload", BUNDLE_ID, "--commit", "v1", "--workdir", "/tmp/wd"])
    cli_mod.main()
    assert seen["commit"] == "v1" and seen["expect"] is None

    monkeypatch.setattr("sys.argv", ["kdcube", "bundle", "status", BUNDLE_ID, "--commit", "v1", "--workdir", "/tmp/wd"])
    with pytest.raises(SystemExit, match="only supported with `kdcube bundle reload"):
        cli_mod.main()
