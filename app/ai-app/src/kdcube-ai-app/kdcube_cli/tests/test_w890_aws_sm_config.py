# SPDX-License-Identifier: MIT
"""W890: `kdcube config import` and `kdcube config export` straight to and from AWS Secrets Manager.

The aws CLI is replaced by an in-memory store that answers exactly as `aws secretsmanager` does. The parity
tests put the runtime's own aws-sm provider (AwsSecretsManagerSecretsManager, with the provider tests' fake
client) on the same store, so what the CLI writes is what the runtime reads, and the other way round.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kdcube_cli import aws_sm_secrets as aws_sm
from kdcube_cli import cli as cli_mod
from kdcube_ai_app.infra.secrets.manager import AwsSecretsManagerSecretsManager, SecretsManagerConfig

PREFIX = "kdcube/demo/project"

APP = "bundles.travel@1-0.secrets.openai.api_key"
HUB = "bundles.connection-hub@1-0.secrets.google.client_secret"
PERSON = "users.u-1.secrets.github.token"
PERSON_APP = "users.u-1.bundles.travel@1-0.secrets.calendar.refresh_token"
COGNITO = "cognito:3f1c2b7a-9d4e-4c1a-8f2e-5b6a7c8d9e0f"
COGNITO_PERSON = f"users.{COGNITO}.secrets.github.token"
COGNITO_PERSON_APP = f"users.{COGNITO}.bundles.travel@1-0.secrets.calendar.refresh_token"
COGNITO_SEGMENT = "cognito!3A3f1c2b7a-9d4e-4c1a-8f2e-5b6a7c8d9e0f"
DOTTED = "bundles.travel@1-0.secrets.providers.maps.v2.key"
PLATFORM = "platform.services.anthropic.api_key"
AGGREGATE = f"{PREFIX}/bundles/secrets"

VALUES = {
    APP: "app-value-1",
    HUB: "hub-value-1",
    PERSON: "person-value-1",
    PERSON_APP: "person-app-value-1",
    DOTTED: "dotted-value-1",
}


# ── The aws CLI, in memory ──


class FakeAwsCli:
    """`aws secretsmanager` get-secret-value / put-secret-value / create-secret over one dict."""

    def __init__(self, data: dict[str, str] | None = None):
        self.data = data if data is not None else {}
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_put: set[str] = set()
        self.written: list[str] = []

    @staticmethod
    def _missing(argv):
        return subprocess.CompletedProcess(
            argv, 254, "", "An error occurred (ResourceNotFoundException) when calling the operation"
        )

    def __call__(self, argv, *, input=None, capture_output, text, check, env):
        assert capture_output and text and not check
        self.calls.append((list(argv), input))
        assert argv[:2] == ["aws", "secretsmanager"]
        op = argv[2]
        if op == "get-secret-value":
            secret_id = argv[argv.index("--secret-id") + 1]
            assert argv[argv.index("--output") + 1] == "json"
            if secret_id not in self.data:
                return self._missing(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.data[secret_id]) + "\n", "")
        assert argv[-2:] == ["--secret-string", "file:///dev/stdin"]
        if op == "put-secret-value":
            secret_id = argv[argv.index("--secret-id") + 1]
            if secret_id in self.fail_put:
                return subprocess.CompletedProcess(argv, 255, "", f"AccessDenied echoing {input}")
            if secret_id not in self.data:
                return self._missing(argv)
            self.data[secret_id] = input
            self.written.append(secret_id)
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        if op == "create-secret":
            name = argv[argv.index("--name") + 1]
            assert name not in self.data
            self.data[name] = input
            self.written.append(name)
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        raise AssertionError(f"unexpected aws call {argv}")

    def puts(self) -> list[str]:
        return list(self.written)

    def argv_text(self) -> str:
        return "\n".join(" ".join(argv) for argv, _ in self.calls)


_REAL_CLI = aws_sm.AwsSecretsCli


def _cli(store: FakeAwsCli) -> aws_sm.AwsSecretsCli:
    return _REAL_CLI(region="eu-west-1", profile=None, runner=store)


# ── The runtime's provider on the same store (the provider tests' fake client shape) ──


class _ClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _SdkClient:
    def __init__(self, data: dict[str, str]):
        self.data = data

    async def get_secret_value(self, *, SecretId: str):
        if SecretId not in self.data:
            raise _ClientError("ResourceNotFoundException")
        return {"SecretString": self.data[SecretId]}

    async def put_secret_value(self, *, SecretId: str, SecretString: str):
        if SecretId not in self.data:
            raise _ClientError("ResourceNotFoundException")
        self.data[SecretId] = SecretString
        return {"ARN": SecretId}

    async def create_secret(self, *, Name: str, SecretString: str, Tags=None, ClientRequestToken=None):
        if Name in self.data:
            raise _ClientError("ResourceExistsException")
        self.data[Name] = SecretString
        return {"ARN": Name, "VersionId": ClientRequestToken}


class _SdkClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_exc):
        return False


class _SdkSession:
    def __init__(self, client):
        self._client = client

    def client(self, *_args, **_kwargs):
        return _SdkClientContext(self._client)


def _runtime(data: dict[str, str]) -> AwsSecretsManagerSecretsManager:
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(provider="aws-sm", component="proc", aws_sm_prefix=PREFIX)
    )
    manager._session = _SdkSession(_SdkClient(data))
    return manager


# ── Parity with the runtime provider ──


def test_the_runtime_writes_and_the_cli_exports_the_same_keys_and_values():
    data: dict[str, str] = {}
    asyncio.run(_runtime(data).set_many({**VALUES, PLATFORM: "platform-value"}))
    store = FakeAwsCli(data)
    assert aws_sm.export_values(_cli(store), PREFIX, include_platform=False) == VALUES
    assert aws_sm.export_values(_cli(store), PREFIX, include_platform=True) == {**VALUES, PLATFORM: "platform-value"}


def test_the_cli_writes_what_the_runtime_reads_and_the_same_documents_the_runtime_writes():
    by_cli: dict[str, str] = {}
    result = aws_sm.apply_import(_cli(FakeAwsCli(by_cli)), PREFIX, VALUES, write_rule="overwrite")
    assert result.written_keys == len(VALUES)
    runtime = _runtime(by_cli)
    for key, value in VALUES.items():
        assert asyncio.run(runtime.get_secret(key)) == value
    assert asyncio.run(runtime.list_all_secret_keys()) == sorted(VALUES)

    by_runtime: dict[str, str] = {}
    asyncio.run(_runtime(by_runtime).set_many(VALUES))
    assert set(by_cli) == set(by_runtime) == {
        f"{PREFIX}/inventory",
        f"{PREFIX}/bundles/travel@1-0/secrets",
        f"{PREFIX}/bundles/connection-hub@1-0/secrets",
        f"{PREFIX}/users/u-1/secrets",
        f"{PREFIX}/users/u-1/bundles/travel@1-0/secrets",
    }
    assert by_cli[f"{PREFIX}/inventory"] == by_runtime[f"{PREFIX}/inventory"]
    for secret_id in by_cli:
        assert json.loads(by_cli[secret_id]) == json.loads(by_runtime[secret_id])


def test_an_import_keeps_the_runtime_values_it_does_not_name():
    data: dict[str, str] = {}
    asyncio.run(_runtime(data).set_many({APP: "kept", PLATFORM: "platform-kept"}))
    aws_sm.apply_import(_cli(FakeAwsCli(data)), PREFIX, {DOTTED: "new"}, write_rule="overwrite")
    runtime = _runtime(data)
    assert asyncio.run(runtime.get_secret(APP)) == "kept"
    assert asyncio.run(runtime.get_secret(PLATFORM)) == "platform-kept"
    assert asyncio.run(runtime.get_secret(DOTTED)) == "new"
    assert asyncio.run(runtime.list_all_secret_keys()) == sorted([APP, PLATFORM, DOTTED])


@pytest.mark.parametrize("key", [APP, HUB, PERSON, PERSON_APP, COGNITO_PERSON, COGNITO_PERSON_APP, DOTTED, PLATFORM])
def test_document_and_tail_are_the_runtime_ones(key):
    runtime = _runtime({})
    assert aws_sm.document_id(PREFIX, key) == runtime._secret_id(key)
    assert aws_sm.key_tail(key) == runtime._tail_for_key(key)


def test_a_cognito_users_documents_are_named_as_the_runtime_names_them():
    """W927: the runtime writes a cognito:<uuid> user's documents under an encoded name; the CLI names the same
    documents. (The management user target does not accept ":" yet, so this user is not in the CLI round trip.)"""
    data: dict[str, str] = {}
    asyncio.run(_runtime(data).set_many({COGNITO_PERSON: "p", COGNITO_PERSON_APP: "pa"}))
    assert {aws_sm.document_id(PREFIX, COGNITO_PERSON), aws_sm.document_id(PREFIX, COGNITO_PERSON_APP)} == {
        f"{PREFIX}/users/{COGNITO_SEGMENT}/secrets",
        f"{PREFIX}/users/{COGNITO_SEGMENT}/bundles/travel@1-0/secrets",
    } == set(data) - {f"{PREFIX}/inventory"}


# ── The plan and the write rules ──


def test_the_plan_says_new_equal_or_different_and_writes_nothing():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {APP: "a", HUB: "h"}, write_rule="overwrite")
    before = dict(store.data)
    store.calls.clear()
    store.written.clear()
    plan = aws_sm.plan_import(_cli(store), PREFIX, {APP: "a", HUB: "changed", PERSON: "p"})
    assert {(item.key, item.status) for item in plan} == {(APP, "equal"), (HUB, "different"), (PERSON, "new")}
    assert store.puts() == [] and store.data == before
    labels = {item.key: (aws_sm.plan_label(item, "overwrite"), aws_sm.plan_label(item, "write-new")) for item in plan}
    assert labels == {
        APP: ("equal", "equal"),
        HUB: ("would-overwrite", "skipped"),
        PERSON: ("new", "new"),
    }


@pytest.mark.parametrize("rule, hub_after", [("overwrite", "changed"), ("write-new", "h")])
def test_the_write_rules(rule, hub_after):
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {APP: "a", HUB: "h"}, write_rule="overwrite")
    store.calls.clear()
    store.written.clear()
    aws_sm.apply_import(_cli(store), PREFIX, {APP: "a", HUB: "changed", PERSON: "p"}, write_rule=rule)
    values = aws_sm.export_values(_cli(store), PREFIX, include_platform=False)
    assert values == {APP: "a", HUB: hub_after, PERSON: "p"}
    # The inventory first, then only the documents that change; the equal app document is not rewritten.
    expected = [f"{PREFIX}/inventory"]
    if rule == "overwrite":
        expected.append(f"{PREFIX}/bundles/connection-hub@1-0/secrets")
    expected.append(f"{PREFIX}/users/u-1/secrets")
    assert store.puts() == expected


def test_a_rerun_writes_nothing():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="overwrite")
    store.calls.clear()
    store.written.clear()
    for rule in aws_sm.WRITE_RULES:
        result = aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule=rule)
        assert result.written_keys == 0 and store.puts() == []


def test_a_failed_write_stops_names_the_written_documents_and_a_rerun_finishes():
    store = FakeAwsCli()
    store.fail_put.add(f"{PREFIX}/bundles/travel@1-0/secrets")
    with pytest.raises(aws_sm.AwsSecretsPartialWrite) as caught:
        aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="write-new")
    message = str(caught.value)
    assert caught.value.written_documents == (f"{PREFIX}/bundles/connection-hub@1-0/secrets",)
    assert "bundles/connection-hub@1-0/secrets" in message and "bundles/travel@1-0/secrets" in message
    assert "app-value-1" not in message and "AccessDenied" not in message
    # The documents after the failure were not attempted.
    assert f"{PREFIX}/users/u-1/secrets" not in store.data

    store.fail_put.clear()
    store.calls.clear()
    store.written.clear()
    result = aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="write-new")
    assert f"{PREFIX}/bundles/connection-hub@1-0/secrets" not in store.puts()  # its own earlier write is equal
    assert aws_sm.export_values(_cli(store), PREFIX, include_platform=False) == VALUES
    assert result.written_keys == len(VALUES) - 1


@pytest.mark.parametrize(
    "stored, incoming",
    [
        ("bundles.travel@1-0.secrets.token.child", "bundles.travel@1-0.secrets.token"),
        ("bundles.travel@1-0.secrets.token", "bundles.travel@1-0.secrets.token.child"),
    ],
)
def test_a_key_that_would_drop_another_is_refused_before_any_write(stored, incoming):
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {stored: "kept"}, write_rule="overwrite")
    before = dict(store.data)
    for rule in aws_sm.WRITE_RULES:
        with pytest.raises(aws_sm.AwsSecretsError, match="refusing"):
            aws_sm.apply_import(_cli(store), PREFIX, {incoming: "new"}, write_rule=rule)
    assert store.data == before


def test_the_aggregate_is_kept_current_and_keeps_other_apps():
    store = FakeAwsCli()
    store.data[AGGREGATE] = json.dumps({"other@1-0": {"k": "v"}, "travel@1-0": {"stale": "x"}})
    result = aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="write-new")
    assert result.aggregate == "updated"
    assert store.puts()[-1] == AGGREGATE  # after the per-app documents
    aggregate = json.loads(store.data[AGGREGATE])
    assert aggregate["other@1-0"] == {"k": "v"}
    assert aggregate["travel@1-0"] == json.loads(store.data[f"{PREFIX}/bundles/travel@1-0/secrets"])
    assert aggregate["connection-hub@1-0"] == {"google": {"client_secret": "hub-value-1"}}
    assert "u-1" not in aggregate  # per-person documents are not in it
    store.written.clear()
    assert aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="overwrite").aggregate == "equal"
    assert store.puts() == []


def test_an_absent_aggregate_is_never_created():
    store = FakeAwsCli()
    result = aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="overwrite")
    assert result.aggregate == "absent" and AGGREGATE not in store.data
    assert aws_sm.apply_import(_cli(store), PREFIX, {PERSON: "p2"}, write_rule="overwrite").aggregate == "none"


def test_a_failed_aggregate_write_is_finished_by_a_rerun():
    store = FakeAwsCli()
    store.data[AGGREGATE] = "{}"
    store.fail_put.add(AGGREGATE)
    with pytest.raises(aws_sm.AwsSecretsPartialWrite, match="Stopped before kdcube/demo/project/bundles/secrets"):
        aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="overwrite")
    store.fail_put.clear()
    store.written.clear()
    result = aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="write-new")
    assert result.written_keys == 0 and result.aggregate == "updated" and store.puts() == [AGGREGATE]
    assert json.loads(store.data[AGGREGATE]) == _app_documents(store.data)


PB_PROOF = "bundles.problem-board@1-0.secrets.connection_hub.peer_proof_secret"
CH_PROOF = "bundles.connection-hub@1-0.secrets.project_membership.peer_proof_secret"
PB_LOOKUP = "bundles.problem-board@1-0.secrets.connection_hub.identity_lookup_secret"
CH_LOOKUP = (
    "bundles.connection-hub@1-0.secrets.connections.delegated_credentials.admission.services.problem-board.signing_secret"
)


def test_a_pair_that_would_differ_refuses_before_any_write_and_names_only_the_pair():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {PB_PROOF: "proof-old", CH_PROOF: "proof-old"}, write_rule="overwrite")
    before = dict(store.data)
    store.written.clear()
    with pytest.raises(aws_sm.AwsSecretsPairMismatch) as caught:
        aws_sm.apply_import(_cli(store), PREFIX, {CH_PROOF: "proof-new", APP: "a"}, write_rule="overwrite")
    assert str(caught.value).startswith("Matched pair(s) would differ after the import: peer proof.")
    assert "proof-new" not in str(caught.value) and "proof-old" not in str(caught.value)
    assert store.data == before and store.puts() == []


@pytest.mark.parametrize(
    "stored, incoming, rule, expected",
    [
        # both sides rotated together
        ({PB_PROOF: "p", CH_PROOF: "p"}, {PB_PROOF: "q", CH_PROOF: "q"}, "overwrite", "match"),
        # write-new keeps the stored value, so the pair stays equal
        ({PB_PROOF: "p", CH_PROOF: "p"}, {CH_PROOF: "q"}, "write-new", "match"),
        # a fresh pair from the folder
        ({}, {PB_LOOKUP: "l", CH_LOOKUP: "l"}, "overwrite", "match"),
        ({}, {PB_LOOKUP: "l"}, "overwrite", "one-side"),
        ({PB_LOOKUP: "l"}, {CH_LOOKUP: "other"}, "write-new", "differs"),
    ],
)
def test_pairs_are_compared_as_they_will_be(stored, incoming, rule, expected):
    store = FakeAwsCli()
    if stored:
        aws_sm.apply_import(_cli(store), PREFIX, stored, write_rule="overwrite")
    checks = aws_sm.check_pairs(_cli(store), PREFIX, incoming, write_rule=rule)
    assert [check.status for check in checks] == [expected]
    if expected != "differs":
        assert aws_sm.apply_import(_cli(store), PREFIX, incoming, write_rule=rule).pairs == checks


def test_pairs_the_folder_does_not_touch_are_not_checked():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {PB_PROOF: "p"}, write_rule="overwrite")
    assert aws_sm.check_pairs(_cli(store), PREFIX, VALUES, write_rule="overwrite") == ()


def test_the_dry_run_shows_a_differing_pair_and_fails(monkeypatch, tmp_path, capsys):
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {PB_PROOF: "proof-old"}, write_rule="overwrite")
    store.written.clear()
    folder = tmp_path / "in"
    folder.mkdir(mode=0o700)
    aws_sm.write_values_to_descriptors(folder, {CH_PROOF: "proof-new"}, replace=False)
    with pytest.raises(SystemExit, match="peer proof"):
        _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(folder), "--dry-run", "--json")
    report = json.loads(capsys.readouterr().out)
    assert report["pairs"] == [{"name": "peer proof", "status": "differs"}]
    assert "proof-new" not in json.dumps(report) and store.puts() == []


def test_values_never_travel_in_arguments():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, {**VALUES, PLATFORM: "platform-value"}, write_rule="overwrite")
    aws_sm.export_values(_cli(store), PREFIX, include_platform=True)
    argv = store.argv_text()
    for value in [*VALUES.values(), "platform-value"]:
        assert value not in argv
    assert all(stdin is None or argv[2] != "get-secret-value" for argv, stdin in store.calls)


def test_a_failed_read_does_not_echo_aws_output():
    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 255, "", "AccessDenied secret-material")

    with pytest.raises(aws_sm.AwsSecretsError) as caught:
        aws_sm.AwsSecretsCli(region=None, profile=None, runner=runner).get(f"{PREFIX}/inventory")
    assert "secret-material" not in str(caught.value)


def test_export_stops_on_a_key_without_a_value_or_document():
    store = FakeAwsCli()
    aws_sm.apply_import(_cli(store), PREFIX, VALUES, write_rule="overwrite")
    del store.data[f"{PREFIX}/users/u-1/secrets"]
    with pytest.raises(aws_sm.AwsSecretsError, match="users/u-1/secrets"):
        aws_sm.export_values(_cli(store), PREFIX, include_platform=False)
    store.data[f"{PREFIX}/users/u-1/secrets"] = json.dumps({"other": "x"})
    with pytest.raises(aws_sm.AwsSecretsError, match="users.u-1.secrets.github.token"):
        aws_sm.export_values(_cli(store), PREFIX, include_platform=False)


def test_export_without_an_inventory_stops():
    with pytest.raises(aws_sm.AwsSecretsError, match="inventory does not exist"):
        aws_sm.export_values(_cli(FakeAwsCli()), PREFIX, include_platform=False)


def test_an_invalid_inventory_is_refused_like_the_runtime_refuses_it():
    store = FakeAwsCli()
    store.data[f"{PREFIX}/inventory"] = json.dumps(
        {"schema": aws_sm.INVENTORY_SCHEMA, "keys": [APP, APP]}
    )
    with pytest.raises(aws_sm.AwsSecretsError, match="not a valid secret inventory"):
        aws_sm.export_values(_cli(store), PREFIX, include_platform=False)


# ── Through `kdcube config import / export` ──


def _main(monkeypatch, store: FakeAwsCli, tmp_path: Path, *argv: str) -> None:
    monkeypatch.setattr(aws_sm, "AwsSecretsCli", lambda **kwargs: _REAL_CLI(runner=store, **kwargs))
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *_a, **_k: tmp_path / "no-runtime")
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda path, **_k: Path(path))
    monkeypatch.setattr(cli_mod, "_canonical_descriptor_dir_from_initialized_workdir", lambda _w: None)
    monkeypatch.setattr(sys, "argv", ["kdcube", "config", *argv, "--aws-sm-prefix", PREFIX, "--aws-region", "eu-west-1"])
    cli_mod.main()


def _pair(folder: Path) -> tuple[dict, dict]:
    return (
        yaml.safe_load((folder / "secrets.yaml").read_text()),
        yaml.safe_load((folder / "bundles.secrets.yaml").read_text()),
    )


def _bundle_secrets(bundles_doc: dict, bundle_id: str) -> dict:
    return next(item["secrets"] for item in bundles_doc["bundles"]["items"] if item["id"] == bundle_id)


def _app_documents(data: dict[str, str]) -> dict[str, dict]:
    head, tail = f"{PREFIX}/bundles/", "/secrets"
    return {
        secret_id[len(head):-len(tail)]: json.loads(raw)
        for secret_id, raw in data.items()
        if secret_id.startswith(head) and secret_id.endswith(tail) and secret_id != AGGREGATE
    }


@pytest.mark.parametrize("rule", ["overwrite", "write-new"])
def test_round_trip_export_edit_import_export(monkeypatch, tmp_path, capsys, rule):
    store = FakeAwsCli()
    asyncio.run(_runtime(store.data).set_many({**VALUES, PLATFORM: "platform-value"}))
    store.data[AGGREGATE] = "{}"  # the deployment creates it; the CLI keeps it current

    first = tmp_path / "first"
    _main(monkeypatch, store, tmp_path, "export", "--out-dir", str(first))
    out = capsys.readouterr().out
    assert "bundles-meta does not exist" in out
    assert stat.S_IMODE(first.stat().st_mode) == 0o700
    for name in ("secrets.yaml", "bundles.secrets.yaml"):
        assert stat.S_IMODE((first / name).stat().st_mode) == 0o600
    assert not (first / "bundles.yaml").exists()
    secrets_doc, bundles_doc = _pair(first)
    assert secrets_doc["platform"] == {}  # platform only with the flag
    assert secrets_doc["users"]["u-1"]["secrets"]["github"]["token"] == "person-value-1"
    assert _bundle_secrets(bundles_doc, "travel@1-0")["providers"]["maps"]["v2"]["key"] == "dotted-value-1"
    for value in VALUES.values():
        assert value not in out

    # Edit one value and add one key.
    _bundle_secrets(bundles_doc, "travel@1-0")["openai"]["api_key"] = "app-value-2"
    secrets_doc["users"]["u-2"] = {"secrets": {"slack": {"token": "person-2-value"}}}
    for name, doc in (("secrets.yaml", secrets_doc), ("bundles.secrets.yaml", bundles_doc)):
        (first / name).write_text(yaml.safe_dump(doc, sort_keys=False))
        os.chmod(first / name, 0o600)

    _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(first), "--dry-run",
          "--write-rule", rule, "--json")
    report = json.loads(capsys.readouterr().out)
    statuses = {row["key"]: row["status"] for row in report["keys"]}
    assert statuses[APP] == ("would-overwrite" if rule == "overwrite" else "skipped")
    assert statuses["users.u-2.secrets.slack.token"] == "new"
    assert statuses[HUB] == "equal"
    assert PLATFORM not in statuses
    assert report["aggregate"] == {"document": AGGREGATE, "status": "would-update"}
    assert "app-value-2" not in json.dumps(report) and "person-2-value" not in json.dumps(report)
    assert store.puts() == []

    _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(first), "--yes",
          "--no-runtime-writers", "--write-rule", rule)
    assert "app-value-2" not in capsys.readouterr().out

    second = tmp_path / "second"
    _main(monkeypatch, store, tmp_path, "export", "--out-dir", str(second))
    capsys.readouterr()
    expected_secrets, expected_bundles = secrets_doc, bundles_doc
    if rule == "write-new":
        _bundle_secrets(expected_bundles, "travel@1-0")["openai"]["api_key"] = "app-value-1"
    assert _pair(second) == (expected_secrets, expected_bundles)
    # The aggregate holds every app's whole document, as the per-app documents now are.
    assert json.loads(store.data[AGGREGATE]) == _app_documents(store.data)
    assert set(_app_documents(store.data)) == {"travel@1-0", "connection-hub@1-0"}
    assert asyncio.run(_runtime(store.data).get_secret(PLATFORM)) == "platform-value"


def test_platform_keys_move_only_with_the_flag(monkeypatch, tmp_path, capsys):
    store = FakeAwsCli()
    asyncio.run(_runtime(store.data).set_many({APP: "a", PLATFORM: "platform-value"}))
    folder = tmp_path / "out"
    _main(monkeypatch, store, tmp_path, "export", "--out-dir", str(folder), "--include-platform-descriptors")
    assert _pair(folder)[0]["platform"] == {"services": {"anthropic": {"api_key": "platform-value"}}}
    secrets_doc = _pair(folder)[0]
    secrets_doc["platform"]["services"]["anthropic"]["api_key"] = "platform-value-2"
    (folder / "secrets.yaml").write_text(yaml.safe_dump(secrets_doc))
    os.chmod(folder / "secrets.yaml", 0o600)
    capsys.readouterr()

    _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(folder), "--yes", "--no-runtime-writers")
    assert asyncio.run(_runtime(store.data).get_secret(PLATFORM)) == "platform-value"
    _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(folder), "--yes",
          "--no-runtime-writers", "--include-platform-descriptors")
    assert asyncio.run(_runtime(store.data).get_secret(PLATFORM)) == "platform-value-2"


def test_export_writes_bundles_yaml_when_bundles_meta_exists(monkeypatch, tmp_path, capsys):
    from kdcube_cli import export_live_bundles as export_mod

    store = FakeAwsCli()
    asyncio.run(_runtime(store.data).set_many({APP: "a"}))
    store.data[f"{PREFIX}/bundles-meta"] = json.dumps({"bundle_ids": ["travel@1-0"]})
    descriptors = {
        f"{PREFIX}/bundles-meta": {"bundle_ids": ["travel@1-0"]},
        f"{PREFIX}/bundles/travel@1-0/descriptor": {"module": "travel.entrypoint"},
    }
    monkeypatch.setattr(export_mod, "aws_secret_json", lambda *, secret_id, **_k: descriptors[secret_id])
    folder = tmp_path / "out"
    _main(monkeypatch, store, tmp_path, "export", "--out-dir", str(folder))
    capsys.readouterr()
    assert yaml.safe_load((folder / "bundles.yaml").read_text())["bundles"]["items"][0]["id"] == "travel@1-0"
    assert _bundle_secrets(_pair(folder)[1], "travel@1-0") == {"openai": {"api_key": "a"}}


def test_an_existing_pair_is_refused_before_any_value_is_read(monkeypatch, tmp_path):
    store = FakeAwsCli()
    asyncio.run(_runtime(store.data).set_many({APP: "a"}))
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "bundles.secrets.yaml").write_text("bundles: {}\n")
    store.calls.clear()
    store.written.clear()
    with pytest.raises(SystemExit, match="secret_export_output_exists"):
        _main(monkeypatch, store, tmp_path, "export", "--out-dir", str(folder))
    assert store.calls == []


@pytest.mark.parametrize(
    "extra",
    [["--yes"], ["--no-runtime-writers"], []],
)
def test_a_write_needs_yes_and_no_runtime_writers(monkeypatch, tmp_path, extra):
    store = FakeAwsCli()
    folder = tmp_path / "in"
    folder.mkdir(mode=0o700)
    aws_sm.write_values_to_descriptors(folder, {APP: "a"}, replace=False)
    with pytest.raises(SystemExit, match="--yes and --no-runtime-writers"):
        _main(monkeypatch, store, tmp_path, "import", "--descriptors-location", str(folder), *extra)
    assert store.calls == []


@pytest.mark.parametrize("flag", ["--include-user-data", "--reload", "--skip-secret-values", "--credential-stdin"])
def test_runtime_only_flags_are_refused(monkeypatch, tmp_path, flag):
    folder = tmp_path / "in"
    folder.mkdir(mode=0o700)
    aws_sm.write_values_to_descriptors(folder, {APP: "a"}, replace=False)
    with pytest.raises(SystemExit, match=flag):
        _main(monkeypatch, FakeAwsCli(), tmp_path, "import", "--descriptors-location", str(folder), "--dry-run", flag)


def test_write_rule_outside_the_aws_import_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda path, **_k: Path(path))
    monkeypatch.setattr(cli_mod, "_canonical_descriptor_dir_from_initialized_workdir", lambda _w: None)
    monkeypatch.setattr(sys, "argv", ["kdcube", "config", "import", "--descriptors-location", str(tmp_path),
                                      "--write-rule", "write-new"])
    with pytest.raises(SystemExit, match="--write-rule"):
        cli_mod.main()
