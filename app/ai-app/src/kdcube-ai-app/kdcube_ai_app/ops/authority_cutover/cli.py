# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Dry-run-first command for a reset into durable authority storage.

It also runs the W414 release step that gives existing public native clients
the device grant (``grant-device-to-public-native-clients``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from connection_hub.delegated_credentials.migration.artifact import (
    read_migration_preview,
    write_migration_preview,
)
from connection_hub.delegated_credentials.migration.model import (
    MigrationEvidenceMismatch,
)
from connection_hub.delegated_credentials.migration.service import (
    create_migration_preview,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.ops.authority_cutover.evidence import (
    require_reviewed_reset,
    reviewed_reset_prerequisites,
)
from kdcube_ai_app.ops.authority_cutover.runtime import (
    apply_reset_target,
    open_oauth_authority,
    open_reset_source,
    open_reset_target,
    rehearse_reset_target,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kdcube-authority-cutover")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview")
    preview.add_argument("--generation-id", required=True)
    preview.add_argument("--preview-file", required=True)

    preflight = commands.add_parser("preflight-target")
    preflight.add_argument("--preview-file", required=True)
    preflight.add_argument("--confirm-preview-sha256", required=True)

    apply = commands.add_parser("apply")
    apply.add_argument("--preview-file", required=True)
    apply.add_argument("--confirm-preview-sha256", required=True)
    apply.add_argument("--source-quiesced", action="store_true")

    device_grant = commands.add_parser("grant-device-to-public-native-clients")
    device_grant.add_argument(
        "--every-process-checks-card-continuity", action="store_true"
    )
    return parser


async def _preview(args: argparse.Namespace) -> int:
    settings = get_settings()
    runtime = await open_reset_source(settings)
    try:
        preview = await create_migration_preview(
            source=runtime.source,
            generation_id=args.generation_id,
            prerequisites=reviewed_reset_prerequisites(),
        )
        await write_migration_preview(args.preview_file, preview)
        print(json.dumps(preview.to_dict(), sort_keys=True, indent=2))
        return 0
    finally:
        await runtime.close()


async def _apply(args: argparse.Namespace) -> int:
    preview = await read_migration_preview(args.preview_file)
    require_reviewed_reset(dict(preview.prerequisites))
    settings = get_settings()
    source = await open_reset_source(settings)
    target = None
    try:
        target = await open_reset_target(settings)
        receipt = await apply_reset_target(
            settings,
            preview=preview,
            source=source.source,
            target_runtime=target,
            confirmed_preview_sha256=args.confirm_preview_sha256,
            source_is_quiesced=bool(args.source_quiesced),
        )
        print(
            json.dumps(
                {
                    "generation_id": receipt.generation_id,
                    "activated_revision": receipt.activated_revision,
                    "source_generation": receipt.source_generation,
                    "target_generation": receipt.target_generation,
                    "family_counts": dict(receipt.target_counts),
                    "preview_sha256": receipt.preview_sha256,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    finally:
        if target is not None:
            await target.close()
        await source.close()


async def _preflight_target(args: argparse.Namespace) -> int:
    """Rehearse reviewed data against the target before quiescence."""

    preview = await read_migration_preview(args.preview_file)
    require_reviewed_reset(dict(preview.prerequisites))
    confirmed = str(args.confirm_preview_sha256 or "").strip().lower()
    if confirmed != preview.preview_sha256:
        raise MigrationEvidenceMismatch("authority_migration_preview_not_confirmed")
    if preview.blockers:
        raise MigrationEvidenceMismatch("authority_migration_preview_has_blockers")

    settings = get_settings()
    source = None
    target = None
    try:
        source = await open_reset_source(settings)
        target = await open_reset_target(settings)
        destination = await rehearse_reset_target(
            settings,
            source=source.source,
            target_runtime=target,
            preview=preview,
            confirmed_preview_sha256=confirmed,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema_tables_verified": len(
                        target.schema_report.verified_tables
                    ),
                    "target": "durable-authority",
                    "rehearsed_records": len(destination.records),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if target is not None:
            await target.close()
        if source is not None:
            await source.close()


async def _grant_device_to_public_native_clients(args: argparse.Namespace) -> int:
    """W414 release step: give existing public native clients the device grant.

    A handler older than the Card continuity check would let a migrated client
    re-authorize any Card by device login without proof. The operator runs
    this only after every process serves the checked handler and says so with
    the flag; without it nothing is contacted or changed.
    """

    if not args.every_process_checks_card_continuity:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "device_grant_migration_requires_checked_rollout",
                    "detail": (
                        "Run this only after every process serves the W414 "
                        "Card continuity check, with "
                        "--every-process-checks-card-continuity."
                    ),
                },
                sort_keys=True,
            )
        )
        return 2
    runtime = await open_oauth_authority(get_settings())
    try:
        changed = await runtime.authority.grant_device_to_public_native_clients()
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema": runtime.authority.schema,
                    "clients_updated": changed,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await runtime.close()


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "grant-device-to-public-native-clients":
        return await _grant_device_to_public_native_clients(args)
    if args.command == "preview":
        return await _preview(args)
    if args.command == "preflight-target":
        return await _preflight_target(args)
    return await _apply(args)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["async_main", "main"]
