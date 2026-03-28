# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import pathlib
import sys
import types

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.external.base import ExternalExecRequest
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import build_external_exec_env
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import format_size_summary
from kdcube_ai_app.apps.chat.sdk.runtime.external.fargate import FargateRuntime
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import (
    normalize_exec_runtime_config,
    resolve_exec_runtime_profile,
)
from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime
from kdcube_ai_app.infra.config import (
    build_external_runtime_base_env,
    prepare_external_runtime_globals,
)
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


class _CaptureLogger(AgentLogger):
    def __init__(self) -> None:
        super().__init__("test.fargate")
        self.messages: list[tuple[str, str]] = []

    def log(self, msg, level="INFO"):
        self.messages.append((str(level), str(msg)))


def test_build_external_exec_env_matches_required_runtime_payload():
    runtime_globals = {
        "PORTABLE_SPEC_JSON": json.dumps({"model_config": {}, "env_passthrough": {}}),
        "COMM_SPEC": {"channel": "chat.events"},
        "TOOL_ALIAS_MAP": {"io_tools": "dyn_io_tools"},
    }
    base_env = {
        "REDIS_URL": "redis://localhost:6379/0",
        "AWS_REGION": "eu-west-1",
    }

    docker_env = build_external_exec_env(
        base_env=base_env,
        runtime_globals=runtime_globals,
        tool_module_names=["dyn_io_tools"],
        exec_id="exec-1",
        sandbox="docker",
        log_file_prefix="supervisor",
        bundle_root="/workspace/bundles/entrypoint",
        bundle_id="entrypoint",
    )
    fargate_env = build_external_exec_env(
        base_env=base_env,
        runtime_globals=runtime_globals,
        tool_module_names=["dyn_io_tools"],
        exec_id="exec-1",
        sandbox="fargate",
        log_file_prefix="supervisor",
        bundle_root="/workspace/bundles/entrypoint",
        bundle_id="entrypoint",
    )

    assert docker_env["RUNTIME_GLOBALS_JSON"] == fargate_env["RUNTIME_GLOBALS_JSON"]
    assert docker_env["RUNTIME_TOOL_MODULES"] == fargate_env["RUNTIME_TOOL_MODULES"]
    assert json.loads(docker_env["RUNTIME_GLOBALS_JSON"])["PORTABLE_SPEC_JSON"] == runtime_globals["PORTABLE_SPEC_JSON"]
    assert json.loads(fargate_env["RUNTIME_GLOBALS_JSON"])["PORTABLE_SPEC_JSON"] == runtime_globals["PORTABLE_SPEC_JSON"]
    for key in ("WORKDIR", "OUTPUT_DIR", "LOG_DIR", "LOG_FILE_PREFIX", "BUNDLE_ROOT", "EXEC_BUNDLE_ROOT", "BUNDLE_ID"):
        assert docker_env[key] == fargate_env[key]


def test_build_external_runtime_base_env_uses_centralized_platform_catalog():
    base_env = build_external_runtime_base_env(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/tester",
            "REDIS_URL": "redis://localhost:6379/0",
            "AUTH_PROVIDER": "cognito",
            "SECRETS_PROVIDER": "aws_sm",
            "SECRETS_SM_REGION": "eu-west-1",
            "MCP_SERVICES": '{"web_search":{"transport":"stdio"}}',
            "WEB_SEARCH_BACKEND": "hybrid",
            "WEB_FETCH_RESOURCES_NYT": '{"cookies":{"nyt-s":"abc"}}',
            "POSTGRES_HOST": "example.internal",
            "OPENAI_API_KEY": "sk-openai",
            "BRAVE_API_KEY": "brave-key",
            "GIT_HTTP_TOKEN": "gh-token",
            "KDCUBE_PLATFORM_SECRETS_JSON": '{"openai":"secret"}',
            "GATEWAY_CONFIG_JSON": '{"tenant":"demo","project":"demo-march"}',
            "UNRELATED_ENV": "drop-me",
        }
    )

    assert base_env["REDIS_URL"] == "redis://localhost:6379/0"
    assert base_env["AUTH_PROVIDER"] == "cognito"
    assert base_env["SECRETS_PROVIDER"] == "aws_sm"
    assert base_env["SECRETS_SM_REGION"] == "eu-west-1"
    assert base_env["MCP_SERVICES"] == '{"web_search":{"transport":"stdio"}}'
    assert base_env["WEB_SEARCH_BACKEND"] == "hybrid"
    assert base_env["WEB_FETCH_RESOURCES_NYT"] == '{"cookies":{"nyt-s":"abc"}}'
    assert base_env["POSTGRES_HOST"] == "example.internal"
    assert base_env["OPENAI_API_KEY"] == "sk-openai"
    assert base_env["BRAVE_API_KEY"] == "brave-key"
    assert base_env["GIT_HTTP_TOKEN"] == "gh-token"
    assert "PATH" not in base_env
    assert "HOME" not in base_env
    assert "UNRELATED_ENV" not in base_env
    assert "KDCUBE_PLATFORM_SECRETS_JSON" not in base_env
    assert "GATEWAY_CONFIG_JSON" not in base_env


def test_prepare_external_runtime_globals_compacts_payload_for_remote_runtime():
    prepared = prepare_external_runtime_globals(
        {
            "PORTABLE_SPEC_JSON": json.dumps(
                {
                    "model_config": {
                        "selected_model": "o3-mini",
                        "selected_embedder": "openai-text-embedding-3-small",
                        "custom_embedding_endpoint": None,
                    },
                    "comm": {
                        "channel": "chat.events",
                        "service": {"request_id": "req-1"},
                        "conversation": {"conversation_id": "conv-1", "turn_id": "turn-1"},
                    },
                    "integrations": {"ctx_client": None, "kv_cache": None},
                    "cv_snapshot": {"entries": []},
                    "env_passthrough": {},
                    "contextvars": {"run_ctx": {"OUTDIR_CV": ""}, "comm_ctx": {"COMM_PRESENT": False}},
                }
            ),
            "TOOL_MODULE_FILES": {
                "io_tools": None,
                "local_tools": "/host/bundles/entrypoint/tools/local_tools.py",
            },
            "BUNDLE_SPEC": {
                "id": "bundle@1",
                "name": "bundle@1",
                "path": "/host/bundles/entrypoint",
                "module": "entrypoint",
                "singleton": False,
            },
            "BUNDLE_ROOT_HOST": "/host/bundles/entrypoint",
            "RAW_TOOL_SPECS": [
                {"module": "pkg.io_tools", "alias": "io_tools", "use_sk": True, "raw": {"drop": True}},
                {"ref": "tools/local_tools.py", "alias": "local_tools", "use_sk": True},
            ],
            "MCP_TOOL_SPECS": [],
            "SKILLS_DESCRIPTOR": {"custom_skills_root": "/host/bundles/entrypoint/skills", "agents_config": {}},
            "EXEC_RUNTIME_CONFIG": {"mode": "fargate"},
            "EXEC_CONTEXT": {"tenant": "demo"},
            "EXEC_SNAPSHOT": {
                "storage_uri": "s3://bucket/prefix",
                "base_prefix": "cb/tenants/demo/projects/demo/executions/privileged/user/conv/turn/run/exec",
                "output_out_uri": "s3://bucket/prefix/out.zip",
            },
        },
        host_bundle_root="/host/bundles/entrypoint",
        bundle_root="/workspace/bundles/entrypoint",
        bundle_dir="entrypoint",
        bundle_id="bundle@1",
    )

    assert prepared["BUNDLE_DIR"] == "entrypoint"
    assert prepared["BUNDLE_ID"] == "bundle@1"
    assert prepared["TOOL_MODULE_FILES"] == {
        "local_tools": "/workspace/bundles/entrypoint/tools/local_tools.py",
    }
    assert prepared["BUNDLE_SPEC"]["path"] == "/workspace/bundles/entrypoint"
    assert prepared["SKILLS_DESCRIPTOR"] == {
        "custom_skills_root": "/workspace/bundles/entrypoint/skills",
    }
    assert "EXEC_RUNTIME_CONFIG" not in prepared
    assert "EXEC_CONTEXT" not in prepared
    assert "BUNDLE_ROOT_HOST" not in prepared
    assert "MCP_TOOL_SPECS" not in prepared
    assert prepared["EXEC_SNAPSHOT"] == {
        "storage_uri": "s3://bucket/prefix",
        "base_prefix": "cb/tenants/demo/projects/demo/executions/privileged/user/conv/turn/run/exec",
    }
    compact_portable = json.loads(prepared["PORTABLE_SPEC_JSON"])
    assert "integrations" not in compact_portable
    assert "cv_snapshot" not in compact_portable
    assert "env_passthrough" not in compact_portable


def test_build_external_runtime_inline_env_is_small_bootstrap_subset():
    from kdcube_ai_app.infra.config import build_external_runtime_inline_env

    inline_env = build_external_runtime_inline_env(
        {
            "AWS_REGION": "eu-west-1",
            "AWS_DEFAULT_REGION": "eu-west-1",
            "SECRETS_PROVIDER": "aws_sm",
            "SECRETS_SM_REGION": "eu-west-1",
            "SECRETS_SM_PREFIX": "kdcube/demo/demo-march",
            "OPENAI_API_KEY": "sk-openai",
            "WEB_FETCH_RESOURCES_MEDIUM": '{"cookies":{"sid":"x"}}',
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )

    assert inline_env["AWS_REGION"] == "eu-west-1"
    assert inline_env["SECRETS_PROVIDER"] == "aws_sm"
    assert inline_env["SECRETS_SM_PREFIX"] == "kdcube/demo/demo-march"
    assert "OPENAI_API_KEY" not in inline_env
    assert "WEB_FETCH_RESOURCES_MEDIUM" not in inline_env
    assert "REDIS_URL" not in inline_env


def test_format_size_summary_reports_largest_entries_without_values():
    summary = format_size_summary(
        {
            "SMALL": "x",
            "BIG": "y" * 200,
            "MID": {"nested": "z" * 50},
        },
        top_n=2,
    )

    assert "BIG=" in summary
    assert "MID=" in summary
    assert "SMALL=" not in summary
    assert "yyyy" not in summary


def test_normalize_exec_runtime_config_with_profiles():
    resolved = normalize_exec_runtime_config(
        {
            "default_profile": "fargate",
            "profiles": {
                "docker": {"mode": "docker"},
                "fargate": {
                    "mode": "fargate",
                    "enabled": True,
                    "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                },
            },
            "container_name": "exec",
        }
    )

    assert resolved == {
        "default_profile": "fargate",
        "profiles": {
            "docker": {"mode": "docker"},
            "fargate": {
                "mode": "fargate",
                "enabled": True,
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
            },
        },
        "container_name": "exec",
    }


def test_resolve_exec_runtime_profile_selects_bundle_supported_profile():
    out = resolve_exec_runtime_profile(
        runtime={
            "default_profile": "fargate",
            "profiles": {
                "docker": {"mode": "docker"},
                "fargate": {"mode": "fargate", "enabled": True},
            },
        },
        profile="docker",
        overrides={"network_mode": "bridge"},
    )

    assert out == {
        "mode": "docker",
        "network_mode": "bridge",
    }


@pytest.mark.asyncio
async def test_execute_py_code_routes_to_fargate_from_runtime_config(monkeypatch, tmp_path):
    captured = {}

    async def _fake_run_py_in_fargate(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "returncode": 0, "error": None, "seconds": 0.1}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.run_py_in_fargate",
        _fake_run_py_in_fargate,
    )

    runtime = _InProcessRuntime(AgentLogger("test.exec"))
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"

    res = await runtime.execute_py_code(
        workdir=workdir,
        output_dir=outdir,
        bundle_root=None,
        tool_modules=[],
        globals={
            "EXEC_RUNTIME_CONFIG": {
                "mode": "fargate",
                "enabled": True,
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                "task_definition": "demo-exec",
                "container_name": "exec",
                "subnets": ["subnet-a"],
            }
        },
        isolation="docker",
        timeout_s=30,
    )

    assert res["ok"] is True
    assert captured["runtime_globals"]["EXEC_RUNTIME_CONFIG"]["mode"] == "fargate"
    assert captured["extra_env"]["EXECUTION_SANDBOX"] == "fargate"


@pytest.mark.asyncio
async def test_execute_py_code_routes_to_docker_with_selected_profile_settings(monkeypatch, tmp_path):
    captured = {}

    async def _fake_run_py_in_docker(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "returncode": 0, "error": None, "seconds": 0.1}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.docker.run_py_in_docker",
        _fake_run_py_in_docker,
    )

    runtime = _InProcessRuntime(AgentLogger("test.exec"))
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"

    res = await runtime.execute_py_code(
        workdir=workdir,
        output_dir=outdir,
        bundle_root=None,
        tool_modules=[],
        globals={
            "EXEC_RUNTIME_CONFIG": {
                "default_profile": "docker_large",
                "profiles": {
                    "docker_small": {
                        "mode": "docker",
                        "image": "py-code-exec:small",
                    },
                    "docker_large": {
                        "mode": "docker",
                        "image": "py-code-exec:large",
                        "network_mode": "bridge",
                        "cpus": "1.5",
                        "memory": "2g",
                        "extra_args": ["--pids-limit", "256"],
                    },
                },
            }
        },
        isolation="docker",
        timeout_s=30,
    )

    assert res["ok"] is True
    assert captured["image"] == "py-code-exec:large"
    assert captured["network_mode"] == "bridge"
    assert captured["extra_docker_args"] == [
        "--cpus",
        "1.5",
        "--memory",
        "2g",
        "--pids-limit",
        "256",
    ]
    assert captured["extra_env"]["EXECUTION_SANDBOX"] == "docker"


@pytest.mark.asyncio
async def test_fargate_runtime_logs_run_task_exception(monkeypatch, tmp_path):
    class _Snapshot:
        storage_uri = "s3://bucket"
        base_prefix = "s3://bucket/base"
        input_work_uri = "s3://bucket/in-work.zip"
        input_out_uri = "s3://bucket/in-out.zip"
        output_work_uri = "s3://bucket/out-work.zip"
        output_out_uri = "s3://bucket/out-out.zip"

    class _FakeEcsClient:
        def __init__(self) -> None:
            self.last_kwargs = None

        def run_task(self, **_kwargs):
            self.last_kwargs = _kwargs
            raise RuntimeError("boom")

    fake_ecs = _FakeEcsClient()
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.snapshot_exec_input",
        lambda **_kwargs: _Snapshot(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.build_exec_snapshot_workspace",
        lambda **kwargs: {"workdir": kwargs["workdir"], "outdir": kwargs["outdir"]},
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.put_exec_payload_secret",
        lambda **_kwargs: "secret-id",
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.delete_exec_payload_secret",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda *_args, **_kwargs: fake_ecs),
    )

    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()
    logger = _CaptureLogger()
    request = ExternalExecRequest(
        workdir=pathlib.Path(workdir),
        outdir=pathlib.Path(outdir),
        runtime_globals={
            "EXEC_RUNTIME_CONFIG": {
                "mode": "fargate",
                "enabled": True,
                "region": "eu-west-1",
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                "task_definition": "demo-exec",
                "container_name": "exec",
                "subnets": ["subnet-a"],
                "security_groups": ["sg-a"],
            }
        },
        tool_module_names=[],
        timeout_s=30,
    )

    res = await FargateRuntime().run(request, logger=logger)

    assert res.ok is False
    assert "fargate_run_task_exception" in (res.error or "")
    assert any("effective config mode=fargate" in msg for _, msg in logger.messages)
    assert any("ecs.run_task raised" in msg for _, msg in logger.messages)
    env_pairs = (
        fake_ecs.last_kwargs["overrides"]["containerOverrides"][0]["environment"]
        if fake_ecs.last_kwargs
        else []
    )
    env_names = {item["name"] for item in env_pairs}
    assert "KDCUBE_EXEC_PAYLOAD_SECRET_ID" in env_names
    assert "RUNTIME_GLOBALS_JSON" not in env_names


@pytest.mark.asyncio
async def test_fargate_runtime_snapshots_bundle_storage_when_present(monkeypatch, tmp_path):
    class _Snapshot:
        storage_uri = "s3://bucket"
        base_prefix = "cb/tenants/demo/projects/demo/executions/registered/user/conv/turn/run/exec"
        input_work_uri = "s3://bucket/in-work.zip"
        input_out_uri = "s3://bucket/in-out.zip"
        output_work_uri = "s3://bucket/out-work.zip"
        output_out_uri = "s3://bucket/out-out.zip"

    class _BundleSnapshot:
        bundle_uri = "s3://bucket/bundle.zip"

    class _BundleStorageSnapshot:
        snapshot_uri = "s3://bucket/bundle-storage.zip"

    class _FakeEcsClient:
        def run_task(self, **_kwargs):
            return {"tasks": []}

    captured_payload = {}
    fake_ecs = _FakeEcsClient()

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.snapshot_exec_input",
        lambda **_kwargs: _Snapshot(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.build_exec_snapshot_workspace",
        lambda **kwargs: {"workdir": kwargs["workdir"], "outdir": kwargs["outdir"]},
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.ensure_bundle_snapshot",
        lambda **_kwargs: _BundleSnapshot(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.ensure_bundle_storage_snapshot",
        lambda **_kwargs: _BundleStorageSnapshot(),
    )
    def _capture_payload(**kwargs):
        captured_payload["payload"] = kwargs["payload"]
        return "secret-id"
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.put_exec_payload_secret",
        _capture_payload,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.delete_exec_payload_secret",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda *_args, **_kwargs: fake_ecs),
    )

    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    bundle_root = tmp_path / "bundle"
    bundle_storage_dir = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    workdir.mkdir()
    outdir.mkdir()
    bundle_root.mkdir()
    bundle_storage_dir.mkdir(parents=True)
    logger = _CaptureLogger()
    request = ExternalExecRequest(
        workdir=pathlib.Path(workdir),
        outdir=pathlib.Path(outdir),
        bundle_root=pathlib.Path(bundle_root),
        runtime_globals={
            "BUNDLE_SPEC": {
                "id": "react.doc",
                "module": "entrypoint",
                "version": "main",
            },
            "EXEC_CONTEXT": {
                "tenant": "tenant",
                "project": "project",
            },
            "BUNDLE_STORAGE_DIR": str(bundle_storage_dir),
            "EXEC_RUNTIME_CONFIG": {
                "mode": "fargate",
                "enabled": True,
                "region": "eu-west-1",
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                "task_definition": "demo-exec",
                "container_name": "exec",
                "subnets": ["subnet-a"],
                "security_groups": ["sg-a"],
            },
        },
        tool_module_names=[],
        timeout_s=30,
    )

    res = await FargateRuntime().run(request, logger=logger)

    assert res.ok is False
    payload = captured_payload["payload"]
    assert payload["runtime_globals"]["BUNDLE_STORAGE_SNAPSHOT_URI"] == "s3://bucket/bundle-storage.zip"
    assert payload["env"]["BUNDLE_STORAGE_DIR"] == str(bundle_storage_dir)


@pytest.mark.asyncio
async def test_fargate_runtime_fails_before_run_task_when_overrides_too_large(monkeypatch, tmp_path):
    class _Snapshot:
        storage_uri = "file:///tmp"
        base_prefix = "cb/tenants/demo/projects/demo/executions/registered/user/conv/turn/run/exec"
        input_work_uri = "file:///tmp/in-work.zip"
        input_out_uri = "file:///tmp/in-out.zip"
        output_work_uri = "file:///tmp/out-work.zip"
        output_out_uri = "file:///tmp/out-out.zip"

    class _FakeEcsClient:
        def __init__(self) -> None:
            self.run_task_called = False

        def run_task(self, **_kwargs):
            self.run_task_called = True
            raise RuntimeError("boom")

    fake_ecs = _FakeEcsClient()

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.snapshot_exec_input",
        lambda **_kwargs: _Snapshot(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.build_exec_snapshot_workspace",
        lambda **kwargs: {"workdir": kwargs["workdir"], "outdir": kwargs["outdir"]},
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.put_exec_payload_secret",
        lambda **_kwargs: "secret-id",
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.delete_exec_payload_secret",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda *_args, **_kwargs: fake_ecs),
    )

    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()
    logger = _CaptureLogger()
    request = ExternalExecRequest(
        workdir=pathlib.Path(workdir),
        outdir=pathlib.Path(outdir),
        runtime_globals={
            "PORTABLE_SPEC_JSON": "x" * 12000,
            "EXEC_RUNTIME_CONFIG": {
                "mode": "fargate",
                "enabled": True,
                "region": "eu-west-1",
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                "task_definition": "demo-exec",
                "container_name": "exec",
                "subnets": ["subnet-a"],
                "security_groups": ["sg-a"],
            },
        },
        tool_module_names=[],
        timeout_s=30,
    )

    res = await FargateRuntime().run(request, logger=logger)

    assert res.ok is False
    assert "fargate_overrides_too_large" not in (res.error or "")
    assert "fargate_run_task_exception" in (res.error or "")
    assert fake_ecs.run_task_called is True
    assert any("payload_env_bytes=" in msg for _, msg in logger.messages)


@pytest.mark.asyncio
async def test_fargate_runtime_reports_pending_state_on_timeout(monkeypatch, tmp_path):
    class _Snapshot:
        storage_uri = "file:///tmp"
        base_prefix = "cb/tenants/demo/projects/demo/executions/privileged/user/conv/turn/run/exec"
        input_work_uri = "file:///tmp/in-work.zip"
        input_out_uri = "file:///tmp/in-out.zip"
        output_work_uri = "file:///tmp/out-work.zip"
        output_out_uri = "file:///tmp/out-out.zip"

    class _FakeEcsClient:
        def run_task(self, **_kwargs):
            return {"tasks": [{"taskArn": "arn:aws:ecs:eu-west-1:123:task/demo"}]}

        def describe_tasks(self, **_kwargs):
            return {
                "tasks": [
                    {
                        "taskArn": "arn:aws:ecs:eu-west-1:123:task/demo",
                        "desiredStatus": "RUNNING",
                        "lastStatus": "PENDING",
                        "attachments": [
                            {
                                "type": "ElasticNetworkInterface",
                                "status": "ATTACHING",
                                "details": [
                                    {"name": "subnetId", "value": "subnet-a"},
                                    {"name": "networkInterfaceId", "value": "eni-123"},
                                ],
                            }
                        ],
                        "containers": [
                            {
                                "name": "exec",
                                "lastStatus": "PENDING",
                                "reason": "RESOURCE:SECRET",
                            }
                        ],
                    }
                ]
            }

        def stop_task(self, **_kwargs):
            return {}

    fake_ecs = _FakeEcsClient()
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.snapshot_exec_input",
        lambda **_kwargs: _Snapshot(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.build_exec_snapshot_workspace",
        lambda **kwargs: {"workdir": kwargs["workdir"], "outdir": kwargs["outdir"]},
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.put_exec_payload_secret",
        lambda **_kwargs: "secret-id",
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.delete_exec_payload_secret",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda *_args, **_kwargs: fake_ecs),
    )

    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()
    logger = _CaptureLogger()
    request = ExternalExecRequest(
        workdir=pathlib.Path(workdir),
        outdir=pathlib.Path(outdir),
        runtime_globals={
            "EXEC_RUNTIME_CONFIG": {
                "mode": "fargate",
                "enabled": True,
                "region": "eu-west-1",
                "cluster": "arn:aws:ecs:eu-west-1:123456789012:cluster/demo",
                "task_definition": "demo-exec",
                "container_name": "exec",
                "subnets": ["subnet-a"],
                "security_groups": ["sg-a"],
            }
        },
        tool_module_names=[],
        timeout_s=1,
    )

    async def _fast_sleep(_seconds):
        return None

    time_values = iter([0.0, 0.0, 2.0])

    def _fake_time():
        return next(time_values)

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.asyncio.sleep", _fast_sleep)
    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.runtime.external.fargate.time.time", _fake_time)

    res = await FargateRuntime().run(request, logger=logger)

    assert res.ok is False
    assert res.returncode == 124
    assert "timeout:" in (res.error or "")
    assert "lastStatus=PENDING" in (res.error or "")
    assert "RESOURCE:SECRET" in (res.error or "")
    assert any("task state" in msg for _, msg in logger.messages)
    assert any("task timeout; stopping." in msg for _, msg in logger.messages)
