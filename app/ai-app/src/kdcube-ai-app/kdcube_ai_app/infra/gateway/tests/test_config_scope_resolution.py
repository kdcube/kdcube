from types import SimpleNamespace

from kdcube_ai_app.infra.gateway import config as gateway_config


def test_config_from_dict_resolves_placeholder_scope_from_settings(monkeypatch):
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("DEFAULT_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PROJECT_ID", raising=False)
    monkeypatch.setattr(
        gateway_config,
        "get_settings",
        lambda: SimpleNamespace(
            REDIS_URL="redis://example",
            TENANT="demo-tenant",
            PROJECT="demo-project",
        ),
    )

    cfg = gateway_config._config_from_dict(  # noqa: SLF001
        {
            "tenant": "TENANT_ID",
            "project": "PROJECT_ID",
            "profile": "development",
        }
    )

    assert cfg.tenant_id == "demo-tenant"
    assert cfg.project_id == "demo-project"


def test_config_from_dict_keeps_explicit_scope_values(monkeypatch):
    monkeypatch.setattr(
        gateway_config,
        "get_settings",
        lambda: SimpleNamespace(
            REDIS_URL="redis://example",
            TENANT="settings-tenant",
            PROJECT="settings-project",
        ),
    )

    cfg = gateway_config._config_from_dict(  # noqa: SLF001
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "profile": "development",
        }
    )

    assert cfg.tenant_id == "tenant-a"
    assert cfg.project_id == "project-a"


def test_config_from_dict_parses_component_scoped_data_bus_publish_limits(monkeypatch):
    monkeypatch.setattr(
        gateway_config,
        "get_settings",
        lambda: SimpleNamespace(
            REDIS_URL="redis://example",
            TENANT="settings-tenant",
            PROJECT="settings-project",
        ),
    )

    cfg = gateway_config.parse_gateway_config_for_component(
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "profile": "development",
            "data_bus": {
                "ingress": {
                    "publish_limits": {
                        "registered": {
                            "enabled": False,
                            "packages_per_minute": 11,
                            "messages_per_minute": 22,
                            "bytes_per_minute": 33,
                            "max_messages_per_package": 4,
                            "max_package_bytes": 55,
                            "window_seconds": 6,
                        }
                    }
                },
                "proc": {
                    "publish_limits": {
                        "registered": {
                            "packages_per_minute": 999,
                        }
                    }
                },
            },
        },
        "ingress",
    )

    limit = cfg.data_bus.get("registered")
    assert limit.enabled is False
    assert limit.packages_per_minute == 11
    assert limit.messages_per_minute == 22
    assert limit.bytes_per_minute == 33
    assert limit.max_messages_per_package == 4
    assert limit.max_package_bytes == 55
    assert limit.window_seconds == 6
