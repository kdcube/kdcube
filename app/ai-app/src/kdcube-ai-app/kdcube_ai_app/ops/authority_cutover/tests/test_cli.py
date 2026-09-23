from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.migration.model import (
    MigrationEvidenceMismatch,
)
from kdcube_ai_app.ops.authority_cutover import cli


def test_target_preflight_opens_and_closes_configured_target(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    settings = object()
    closed = False
    preview_path = tmp_path / "preview.json"
    preview = SimpleNamespace(
        prerequisites={"activation": {"kind": "reset"}},
        preview_sha256="a" * 64,
        blockers=(),
    )

    async def read_preview(actual_path):
        assert actual_path == str(preview_path)
        return preview

    async def open_target(actual_settings):
        assert actual_settings is settings

        async def close() -> None:
            nonlocal closed
            closed = True

        return SimpleNamespace(close=close)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "open_reset_target", open_target)
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
    assert closed is True
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
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
