from pathlib import Path

import yaml

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.infra.secrets import build_secrets_manager_config


class _NoopSecretsManager:
    def get_secret(self, key: str):
        return None


def test_get_plain_reads_assembly_by_default(monkeypatch, tmp_path):
    for key in (
        "SECRETS_PROVIDER",
        "KDCUBE_STORAGE_PATH",
        "CB_BUNDLE_STORAGE_URL",
        "REACT_WORKSPACE_IMPLEMENTATION",
        "REACT_WORKSPACE_GIT_REPO",
        "CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION",
        "CLAUDE_CODE_SESSION_GIT_REPO",
    ):
        monkeypatch.delenv(key, raising=False)
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "secrets": {
                    "provider": "secrets-service",
                },
                "storage": {
                    "kdcube": "s3://example/kdcube",
                    "bundles": "s3://example/bundles",
                    "workspace": {"type": "git", "repo": "https://example.com/workspace.git"},
                    "claude_code_session": {"type": "git", "repo": "https://example.com/sessions.git"},
                },
                "frontend": {"routes_prefix": "/platform"},
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("storage.workspace.type") == "git"
    assert sdk_config.read_plain("a:frontend.routes_prefix") == "/platform"
    assert sdk_config.get_plain("secrets.provider") == "secrets-service"
    assert settings.SECRETS_PROVIDER == "secrets-service"
    assert build_secrets_manager_config(settings).provider == "secrets-service"
    assert settings.STORAGE_PATH == "s3://example/kdcube"
    assert settings.BUNDLE_STORAGE_URL == "s3://example/bundles"
    assert settings.REACT_WORKSPACE_IMPLEMENTATION == "git"
    assert settings.plain("storage.workspace.repo") == "https://example.com/workspace.git"
    assert settings.REACT_WORKSPACE_GIT_REPO == "https://example.com/workspace.git"
    assert settings.CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION == "git"
    assert settings.CLAUDE_CODE_SESSION_GIT_REPO == "https://example.com/sessions.git"


def test_get_plain_reads_bundles_namespace(monkeypatch, tmp_path):
    bundles_path = tmp_path / "bundles.yaml"
    bundles_path.write_text(
        yaml.safe_dump(
            {
                "default_bundle_id": "demo.bundle@1.0.0",
                "bundles": {
                    "demo.bundle@1.0.0": {
                        "name": "Demo Bundle",
                        "widgets": [{"alias": "chat", "icon": "sparkles"}],
                    }
                },
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(bundles_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("b:default_bundle_id") == "demo.bundle@1.0.0"
    assert settings.plain("b:bundles.demo.bundle@1.0.0.name") == "Demo Bundle"
    assert settings.plain("b:bundles.demo.bundle@1.0.0.widgets.0.alias") == "chat"


def test_get_plain_returns_default_when_path_missing(monkeypatch, tmp_path):
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(yaml.safe_dump({"storage": {"workspace": {"type": "custom"}}}, sort_keys=False))

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("storage.workspace.repo", default="none") == "none"


def test_settings_auth_provider_reads_auth_idp_from_assembly(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "type": "delegated",
                    "idp": "simple",
                },
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))

    settings = sdk_config.Settings()

    assert settings.AUTH_PROVIDER == "simple"


def test_settings_auth_provider_falls_back_to_legacy_auth_type(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "type": "delegated",
                },
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))

    settings = sdk_config.Settings()

    assert settings.AUTH_PROVIDER == "cognito"


def test_settings_reads_metrics_service_config_from_assembly(monkeypatch, tmp_path):
    for key in (
        "METRICS_PORT",
        "METRICS_MODE",
        "METRICS_REQUEST_TIMEOUT_SEC",
        "METRICS_ENABLE_PG_POOL",
        "METRICS_INGRESS_BASE_URL",
        "METRICS_PROC_BASE_URL",
        "METRICS_AUTH_HEADER_NAME",
        "METRICS_AUTH_HEADER_VALUE",
        "METRICS_HEADERS_JSON",
        "METRICS_SCHEDULER_ENABLED",
        "METRICS_EXPORT_INTERVAL_SEC",
        "METRICS_EXPORT_ON_START",
        "METRICS_RUN_ONCE",
        "METRICS_MAPPING_JSON",
        "METRICS_EXPORT_CLOUDWATCH",
        "METRICS_CLOUDWATCH_NAMESPACE",
        "METRICS_CLOUDWATCH_REGION",
        "METRICS_CLOUDWATCH_DIMENSIONS_JSON",
        "METRICS_EXPORT_PROMETHEUS_PUSH",
        "METRICS_PROM_PUSHGATEWAY_URL",
        "METRICS_PROM_JOB_NAME",
        "METRICS_PROM_GROUPING_LABELS_JSON",
        "METRICS_PROM_SCRAPE_TTL_SEC",
        "LOG_LEVEL",
        "LOG_MAX_MB",
        "LOG_BACKUP_COUNT",
        "LOG_DIR",
        "LOG_FILE_PREFIX",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")

    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "ports": {"metrics": "9191"},
                "platform": {
                    "services": {
                        "metrics": {
                            "log": {
                                "log_level": "DEBUG",
                                "log_max_mb": 42,
                                "log_backup_count": 7,
                                "log_dir": "/var/log/kdcube-metrics",
                                "log_file_prefix": "metrics-service",
                            },
                            "service": {
                                "metrics_mode": "proxy",
                                "metrics_request_timeout_sec": 9.5,
                                "metrics_enable_pg_pool": True,
                            },
                            "proxy": {
                                "metrics_ingress_base_url": "http://ingress:8010",
                                "metrics_proc_base_url": "http://proc:8020",
                                "metrics_auth_header_name": "Authorization",
                                "metrics_auth_header_value": "Bearer demo",
                                "metrics_headers_json": '{"X-Scope":"metrics"}',
                            },
                            "export": {
                                "scheduler_enabled": True,
                                "export_interval_sec": 12,
                                "export_on_start": False,
                                "run_once": True,
                                "mapping_json": '{"proc.queue.total":{"name":"ProcQueueTotal"}}',
                                "cloudwatch": {
                                    "enabled": True,
                                    "namespace": "Demo/Metrics",
                                    "region": "eu-west-1",
                                    "dimensions_json": '{"env":"dev"}',
                                },
                                "prometheus": {
                                    "push_enabled": True,
                                    "pushgateway_url": "http://pushgateway:9091",
                                    "job_name": "demo_metrics",
                                    "grouping_labels_json": '{"tenant":"demo"}',
                                    "scrape_ttl_sec": 17,
                                },
                            },
                        }
                    }
                },
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))

    settings = sdk_config.Settings()

    assert settings.METRICS_PORT == 9191
    metrics = settings.PLATFORM.METRICS
    assert metrics.LOG.LOG_LEVEL == "DEBUG"
    assert metrics.LOG.LOG_MAX_MB == 42
    assert metrics.LOG.LOG_BACKUP_COUNT == 7
    assert metrics.LOG.LOG_DIR == "/var/log/kdcube-metrics"
    assert metrics.LOG.LOG_FILE_PREFIX == "metrics-service"
    assert metrics.SERVICE.METRICS_MODE == "proxy"
    assert metrics.SERVICE.METRICS_REQUEST_TIMEOUT_SEC == 9.5
    assert metrics.SERVICE.METRICS_ENABLE_PG_POOL is True
    assert metrics.PROXY.METRICS_INGRESS_BASE_URL == "http://ingress:8010"
    assert metrics.PROXY.METRICS_PROC_BASE_URL == "http://proc:8020"
    assert metrics.PROXY.METRICS_AUTH_HEADER_NAME == "Authorization"
    assert metrics.PROXY.METRICS_AUTH_HEADER_VALUE == "Bearer demo"
    assert metrics.PROXY.METRICS_HEADERS_JSON == '{"X-Scope":"metrics"}'
    assert metrics.EXPORT.METRICS_SCHEDULER_ENABLED is True
    assert metrics.EXPORT.METRICS_EXPORT_INTERVAL_SEC == 12
    assert metrics.EXPORT.METRICS_EXPORT_ON_START is False
    assert metrics.EXPORT.METRICS_RUN_ONCE is True
    assert metrics.EXPORT.METRICS_MAPPING_JSON == '{"proc.queue.total":{"name":"ProcQueueTotal"}}'
    assert metrics.EXPORT.CLOUDWATCH.METRICS_EXPORT_CLOUDWATCH is True
    assert metrics.EXPORT.CLOUDWATCH.METRICS_CLOUDWATCH_NAMESPACE == "Demo/Metrics"
    assert metrics.EXPORT.CLOUDWATCH.METRICS_CLOUDWATCH_REGION == "eu-west-1"
    assert metrics.EXPORT.CLOUDWATCH.METRICS_CLOUDWATCH_DIMENSIONS_JSON == '{"env":"dev"}'
    assert metrics.EXPORT.PROMETHEUS.METRICS_EXPORT_PROMETHEUS_PUSH is True
    assert metrics.EXPORT.PROMETHEUS.METRICS_PROM_PUSHGATEWAY_URL == "http://pushgateway:9091"
    assert metrics.EXPORT.PROMETHEUS.METRICS_PROM_JOB_NAME == "demo_metrics"
    assert metrics.EXPORT.PROMETHEUS.METRICS_PROM_GROUPING_LABELS_JSON == '{"tenant":"demo"}'
    assert metrics.EXPORT.PROMETHEUS.METRICS_PROM_SCRAPE_TTL_SEC == 17


def test_export_managed_env_includes_fargate_settings_from_assembly(monkeypatch, tmp_path):
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "secrets": {
                    "provider": "secrets-file",
                    "aws_sm_prefix": "kdcube/demo/project",
                },
                "platform": {
                    "services": {
                        "proc": {
                            "exec": {
                                "fargate": {
                                    "enabled": True,
                                    "cluster": "demo-cluster",
                                    "task_definition": "demo-exec",
                                    "container_name": "exec",
                                    "subnets": ["subnet-a", "subnet-b"],
                                    "security_groups": ["sg-a"],
                                    "assign_public_ip": "DISABLED",
                                    "platform_version": "1.4.0",
                                }
                            }
                        }
                    }
                }
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("PLATFORM_DESCRIPTORS_DIR", str(tmp_path))
    settings = sdk_config.Settings()

    exported = sdk_config.export_managed_env(settings=settings)

    assert exported["PLATFORM_DESCRIPTORS_DIR"] == str(tmp_path)
    assert exported["SECRETS_PROVIDER"] == "secrets-file"
    assert exported["SECRETS_AWS_SM_PREFIX"] == "kdcube/demo/project"
    assert exported["SECRETS_SM_PREFIX"] == "kdcube/demo/project"
    assert exported["FARGATE_EXEC_ENABLED"] == "1"
    assert exported["FARGATE_CLUSTER"] == "demo-cluster"
    assert exported["FARGATE_TASK_DEFINITION"] == "demo-exec"
    assert exported["FARGATE_CONTAINER_NAME"] == "exec"
    assert exported["FARGATE_SUBNETS"] == "subnet-a,subnet-b"
    assert exported["FARGATE_SECURITY_GROUPS"] == "sg-a"
    assert exported["FARGATE_ASSIGN_PUBLIC_IP"] == "DISABLED"
    assert exported["FARGATE_PLATFORM_VERSION"] == "1.4.0"
