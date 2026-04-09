from pathlib import Path

import yaml

from kdcube_ai_app.apps.chat.sdk import config as sdk_config


class _NoopSecretsManager:
    def get_secret(self, key: str):
        return None


def test_get_plain_reads_assembly_by_default(monkeypatch, tmp_path):
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(
        yaml.safe_dump(
            {
                "storage": {
                    "workspace": {"type": "git", "repo": "https://example.com/workspace.git"},
                },
                "frontend": {"routes_prefix": "/example-product"},
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setattr(sdk_config, "_ASSEMBLY_YAML_PATH", Path(assembly_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("storage.workspace.type") == "git"
    assert sdk_config.read_plain("a:frontend.routes_prefix") == "/example-product"
    assert settings.plain("storage.workspace.repo") == "https://example.com/workspace.git"


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
    monkeypatch.setattr(sdk_config, "_BUNDLES_YAML_PATH", Path(bundles_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("b:default_bundle_id") == "demo.bundle@1.0.0"
    assert settings.plain("b:bundles.demo.bundle@1.0.0.name") == "Demo Bundle"
    assert settings.plain("b:bundles.demo.bundle@1.0.0.widgets.0.alias") == "chat"


def test_get_plain_returns_default_when_path_missing(monkeypatch, tmp_path):
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text(yaml.safe_dump({"storage": {"workspace": {"type": "custom"}}}, sort_keys=False))

    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setattr(sdk_config, "_ASSEMBLY_YAML_PATH", Path(assembly_path))
    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert sdk_config.get_plain("storage.workspace.repo", default="none") == "none"
