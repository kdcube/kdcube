from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.migration.model import (
    MigrationEvidenceMismatch,
)
from kdcube_ai_app.ops.authority_cutover import cli


def test_target_preflight_rehearses_and_closes_source_and_target(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    settings = object()
    source_closed = False
    target_closed = False
    preview_path = tmp_path / "preview.json"
    preview = SimpleNamespace(
        prerequisites={"activation": {"kind": "reset"}},
        preview_sha256="a" * 64,
        blockers=(),
    )

    async def read_preview(actual_path):
        assert actual_path == str(preview_path)
        return preview

    async def open_source(actual_settings):
        assert actual_settings is settings

        async def close() -> None:
            nonlocal source_closed
            source_closed = True

        return SimpleNamespace(source="migration-source", close=close)

    async def open_target(actual_settings):
        assert actual_settings is settings

        async def close() -> None:
            nonlocal target_closed
            target_closed = True

        return SimpleNamespace(
            close=close,
            schema_report=SimpleNamespace(
                verified_tables=tuple(f"table-{index}" for index in range(12))
            ),
        )

    async def rehearse(
        actual_settings,
        *,
        source,
        target_runtime,
        preview: object,
        confirmed_preview_sha256,
    ):
        assert actual_settings is settings
        assert source == "migration-source"
        assert target_runtime.schema_report.verified_tables == tuple(
            f"table-{index}" for index in range(12)
        )
        assert preview is not None
        assert confirmed_preview_sha256 == "a" * 64
        return SimpleNamespace(records=("record-1", "record-2"))

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "open_reset_source", open_source)
    monkeypatch.setattr(cli, "open_reset_target", open_target)
    monkeypatch.setattr(cli, "rehearse_reset_target", rehearse)
    monkeypatch.setattr(cli, "read_migration_preview", read_preview)
    monkeypatch.setattr(cli, "require_reviewed_reset", lambda value: None)

    assert (
        asyncio.run(
            cli.async_main(
                [
                    "preflight-target",
                    "--preview-file",
                    str(preview_path),
                    "--confirm-preview-sha256",
                    "a" * 64,
                ]
            )
        )
        == 0
    )
    assert source_closed is True
    assert target_closed is True
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "rehearsed_records": 2,
        "schema_tables_verified": 12,
        "target": "durable-authority",
    }


def test_target_preflight_refuses_unconfirmed_preview_before_opening_target(
    monkeypatch,
    tmp_path,
) -> None:
    preview_path = tmp_path / "preview.json"
    preview = SimpleNamespace(
        prerequisites={"activation": {"kind": "reset"}},
        preview_sha256="a" * 64,
        blockers=(),
    )

    async def read_preview(actual_path):
        assert actual_path == str(preview_path)
        return preview

    async def unexpected_target(_settings):
        raise AssertionError("target must not open for unconfirmed evidence")

    monkeypatch.setattr(cli, "read_migration_preview", read_preview)
    monkeypatch.setattr(cli, "require_reviewed_reset", lambda value: None)
    monkeypatch.setattr(cli, "open_reset_target", unexpected_target)

    with pytest.raises(MigrationEvidenceMismatch, match="preview_not_confirmed"):
        asyncio.run(
            cli.async_main(
                [
                    "preflight-target",
                    "--preview-file",
                    str(preview_path),
                    "--confirm-preview-sha256",
                    "b" * 64,
                ]
            )
        )


def test_apply_uses_transactional_runtime_and_closes_dependencies(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    settings = object()
    source_closed = False
    target_closed = False
    preview_path = tmp_path / "preview.json"
    preview = SimpleNamespace(
        prerequisites={"activation": {"kind": "reset"}},
    )
    receipt = SimpleNamespace(
        generation_id="authority-generation",
        activated_revision=1,
        source_generation="a" * 64,
        target_generation="a" * 64,
        target_counts={"oauth_clients": 1},
        preview_sha256="b" * 64,
    )

    async def read_preview(actual_path):
        assert actual_path == str(preview_path)
        return preview

    async def open_source(actual_settings):
        assert actual_settings is settings

        async def close() -> None:
            nonlocal source_closed
            source_closed = True

        return SimpleNamespace(source="migration-source", close=close)

    async def open_target(actual_settings):
        assert actual_settings is settings

        async def close() -> None:
            nonlocal target_closed
            target_closed = True

        return SimpleNamespace(close=close)

    async def apply(
        actual_settings,
        *,
        source,
        target_runtime,
        preview,
        confirmed_preview_sha256,
        source_is_quiesced,
    ):
        assert actual_settings is settings
        assert source == "migration-source"
        assert target_runtime is not None
        assert preview is not None
        assert confirmed_preview_sha256 == "b" * 64
        assert source_is_quiesced is True
        return receipt

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "open_reset_source", open_source)
    monkeypatch.setattr(cli, "open_reset_target", open_target)
    monkeypatch.setattr(cli, "apply_reset_target", apply)
    monkeypatch.setattr(cli, "read_migration_preview", read_preview)
    monkeypatch.setattr(cli, "require_reviewed_reset", lambda value: None)

    assert (
        asyncio.run(
            cli.async_main(
                [
                    "apply",
                    "--preview-file",
                    str(preview_path),
                    "--confirm-preview-sha256",
                    "b" * 64,
                    "--source-quiesced",
                ]
            )
        )
        == 0
    )
    assert source_closed is True
    assert target_closed is True
    assert json.loads(capsys.readouterr().out) == {
        "activated_revision": 1,
        "family_counts": {"oauth_clients": 1},
        "generation_id": "authority-generation",
        "preview_sha256": "b" * 64,
        "source_generation": "a" * 64,
        "target_generation": "a" * 64,
    }
