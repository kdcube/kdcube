"""Execution of backend-neutral KDCube secret management commands."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kdcube_cli import installer as installer_mod
from kdcube_cli.control.local_runtime import (
    local_public_base_url,
    local_target_identity,
)
from kdcube_cli.management.client import ManagementClient
from kdcube_cli.management.credentials import normalize_bearer
from kdcube_cli.management.errors import ManagementCliError
from kdcube_cli.management.models import (
    SECRET_VALUE_READ,
    ManagementDenial,
    ManagementRequest,
    ManagementResult,
    ManagementSecretTarget,
    ManagementTarget,
)
from kdcube_cli.management.presentation import management_view
from kdcube_cli.management.secret_descriptors import (
    SecretDescriptorExport,
    SecretDescriptorImport,
    load_secret_descriptors,
    validate_existing_secret_descriptor_directory,
    validate_secret_descriptor_export,
    write_secret_descriptors,
    write_secret_descriptors_into_directory,
)
from kdcube_cli.management.secret_export import (
    BrowserSecretExportService,
    HttpxSecretExportTransport,
    SecretExportClient,
    SecretExportResult,
)
from kdcube_cli.management.secret_output import (
    validate_private_secret_output,
    write_private_secret,
)
from kdcube_cli.management.transport import HttpxManagementTransport


def local_management_target(
    workdir: Path,
    *,
    tenant: str = "",
    project: str = "",
) -> ManagementTarget:
    """Resolve one initialized local runtime into its public management target."""

    if workdir is None:
        raise ManagementCliError(
            "management_target_required",
            "Select a local KDCube workdir or provide --endpoint with coordinates.",
        )
    workdir = workdir.expanduser().resolve()
    env_path = workdir / "config" / ".env"
    if not env_path.is_file():
        raise ManagementCliError(
            "management_local_target_invalid",
            "The selected local KDCube runtime is not initialized.",
        )
    resolved_tenant, resolved_project = local_target_identity(workdir)
    exact_tenant = str(resolved_tenant or "").strip()
    exact_project = str(resolved_project or "").strip()
    if not exact_tenant or not exact_project:
        raise ManagementCliError(
            "management_local_target_invalid",
            "The selected local KDCube runtime has no deployment coordinates.",
        )
    if (tenant and tenant != exact_tenant) or (project and project != exact_project):
        raise ManagementCliError(
            "management_coordinate_mismatch",
            "The selected workdir belongs to different KDCube coordinates.",
        )
    env_file = installer_mod.load_env_file(env_path)
    return ManagementTarget.create(
        public_base_url=local_public_base_url(env_file),
        tenant=exact_tenant,
        project=exact_project,
        session_target_key=f"local:{workdir}",
    )


def _target_from_args(
    args: argparse.Namespace,
    *,
    local_workdir: Path | None,
    tenant: str,
    project: str,
) -> ManagementTarget:
    endpoint = str(getattr(args, "endpoint", "") or "").strip()
    if endpoint:
        if not tenant or not project:
            raise ManagementCliError(
                "management_coordinates_required",
                "A remote KDCube endpoint requires both --tenant and --project.",
            )
        return ManagementTarget.create(
            public_base_url=endpoint,
            tenant=tenant,
            project=project,
        )
    if local_workdir is None:
        raise ManagementCliError(
            "management_target_required",
            "Select a local KDCube workdir or provide --endpoint with coordinates.",
        )
    return local_management_target(
        local_workdir,
        tenant=tenant,
        project=project,
    )


def delegated_credential_from_input(args: argparse.Namespace) -> str:
    if bool(args.credential_stdin):
        candidate = sys.stdin.readline()
        candidate = candidate.removesuffix("\n").removesuffix("\r")
        return normalize_bearer(candidate)
    return normalize_bearer(getpass.getpass("Delegated KDCube bearer: "))


def _secret_value(args: argparse.Namespace) -> str:
    return (
        sys.stdin.read()
        if bool(args.value_stdin)
        else getpass.getpass("Secret value: ")
    )


def _secret_targets(args: argparse.Namespace) -> tuple[ManagementSecretTarget, ...]:
    targets = [
        ManagementSecretTarget.create(scope="platform", key=key)
        for key in (args.platform_key or [])
    ]
    for value in args.bundle_key or []:
        bundle_id, separator, key = str(value or "").partition("=")
        if not separator:
            raise ManagementCliError(
                "secret_export_bundle_target_invalid",
                "Each --bundle-key must use BUNDLE_ID=KEY.",
            )
        targets.append(
            ManagementSecretTarget.create(
                scope="bundle",
                bundle_id=bundle_id,
                key=key,
            )
        )
    for value in args.user_key or []:
        user_id, separator, key = str(value or "").partition("=")
        if not separator:
            raise ManagementCliError(
                "secret_export_user_target_invalid",
                "Each --user-key must use USER_ID=KEY.",
            )
        targets.append(
            ManagementSecretTarget.create(
                scope="user",
                user_id=user_id,
                key=key,
            )
        )
    for value in args.user_bundle_key or []:
        owner, separator, key = str(value or "").partition("=")
        user_id, owner_separator, bundle_id = owner.partition("/")
        if not separator or not owner_separator:
            raise ManagementCliError(
                "secret_export_user_bundle_target_invalid",
                "Each --user-bundle-key must use USER_ID/BUNDLE_ID=KEY.",
            )
        targets.append(
            ManagementSecretTarget.create(
                scope="user",
                user_id=user_id,
                bundle_id=bundle_id,
                key=key,
            )
        )
    if bool(args.all_secrets):
        if targets:
            raise ManagementCliError(
                "secret_export_selection_invalid",
                "--all cannot be combined with exact-key export options.",
            )
        return ()
    if not targets:
        raise ManagementCliError(
            "secret_export_targets_required",
            "Secret export requires --all or at least one exact-key option.",
        )
    ordered = tuple(sorted(targets, key=lambda item: item.identity))
    if len({item.identity for item in ordered}) != len(ordered):
        raise ManagementCliError(
            "secret_export_target_duplicate",
            "Each secret export target must be named once.",
        )
    return ordered


def _print_json(value: Any) -> None:
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def _print_manual_authorization_url(url: str) -> bool:
    sys.stderr.write(f"Open this authorization URL in a browser:\n{url}\n")
    sys.stderr.flush()
    return True


async def _result_with_consent(
    args: argparse.Namespace,
    client: ManagementClient,
    request: ManagementRequest,
    bearer: str,
) -> ManagementResult | ManagementDenial:
    return await _execute_with_consent(
        client,
        request,
        bearer,
        no_open=bool(args.no_open),
        no_wait=bool(args.no_wait),
    )


async def _execute_with_consent(
    client: ManagementClient,
    request: ManagementRequest,
    bearer: str,
    *,
    no_open: bool,
    no_wait: bool,
) -> ManagementResult | ManagementDenial:
    result = await client.execute(request, bearer=bearer)
    if isinstance(result, ManagementDenial) and result.recovery is not None:
        recovery = result.recovery
        opened = False
        if recovery.expires_at > int(time.time()) and not no_open:
            try:
                opened = bool(webbrowser.open(recovery.authorization_url))
            except Exception:  # noqa: BLE001
                opened = False
        if opened and sys.stdin.isatty() and not no_wait:
            input("Approve the exact operation in the browser, then press Enter: ")
            result = await client.execute(request, bearer=bearer)
    return result


def _request_for_args(
    args: argparse.Namespace,
    target: ManagementTarget,
) -> ManagementRequest:
    options = {
        "scope": args.scope,
        "key": args.key,
        "bundle_id": args.bundle_id,
        "user_id": args.user_id,
        "invocation_id": args.invocation_id,
    }
    if args.secrets_command == "metadata":
        return ManagementRequest.secret_metadata(target, **options)
    if args.secrets_command == "get":
        return ManagementRequest.secret_read(target, **options)
    if args.secrets_command == "set":
        return ManagementRequest.secret_write(
            target,
            value=_secret_value(args),
            **options,
        )
    if args.secrets_command == "delete":
        return ManagementRequest.secret_delete(target, **options)
    raise AssertionError("unhandled delegated secret command")


async def _run_delegated(
    args: argparse.Namespace,
    *,
    target: ManagementTarget,
) -> int:
    output_target: Path | None = None
    if args.secrets_command == "get":
        output_target = validate_private_secret_output(
            Path(args.output),
            replace=bool(args.replace),
        )
    bearer = delegated_credential_from_input(args)
    request = _request_for_args(args, target)
    client = ManagementClient(transport=HttpxManagementTransport())
    result = await _result_with_consent(args, client, request, bearer)
    view = management_view(request, result)
    if isinstance(result, ManagementResult) and request.operation == SECRET_VALUE_READ:
        secret_value = result.result.get("value")
        if not isinstance(secret_value, str) or output_target is None:
            raise ManagementCliError(
                "management_secret_result_invalid",
                "KDCube did not return the requested secret value.",
            )
        output = write_private_secret(
            output_target,
            secret_value,
            replace=bool(args.replace),
        )
        view["result"] = {
            key: value
            for key, value in dict(view["result"]).items()
            if key != "disclosed"
        }
        view["result"].update(
            {
                "disclosed": True,
                "output": str(output),
                "permissions": (
                    {"file_mode": "0600"}
                    if sys.platform != "win32"
                    else {"windows_acl": "inherited_from_output_parent"}
                ),
            }
        )
    _print_json(view)
    return 0 if isinstance(result, ManagementResult) else 3


async def _run_export(
    args: argparse.Namespace,
    *,
    target: ManagementTarget,
) -> int:
    targets = _secret_targets(args)
    result, exported = await export_secret_descriptor_pair(
        target=target,
        output_directory=Path(args.output_directory),
        targets=targets,
        selection="all" if args.all_secrets else "",
        timeout_seconds=args.wait_seconds,
        no_open=bool(args.no_open),
        into_existing_directory=bool(
            getattr(args, "into_descriptor_directory", False)
        ),
        replace=bool(getattr(args, "replace_descriptor_files", False)),
    )
    _print_json(_secret_export_view(target, result, exported))
    return 0


async def export_bootstrap_values(
    *,
    target: ManagementTarget,
    user_data: bool,
    timeout_seconds: float = 300.0,
    no_open: bool = False,
) -> SecretExportResult:
    """W885: one browser-approved configuration-bootstrap export; the server filters before any value read.
    The caller writes the result with the same writer a secrets-file export uses."""
    service = BrowserSecretExportService(
        client=SecretExportClient(transport=HttpxSecretExportTransport())
    )
    browser_options: dict[str, Any] = {}
    if no_open:
        browser_options["browser_opener"] = _print_manual_authorization_url
    return await service.export(
        target=target,
        targets=(),
        selection="bootstrap",
        user_data=user_data,
        timeout_seconds=timeout_seconds,
        **browser_options,
    )


async def export_secret_descriptor_pair(
    *,
    target: ManagementTarget,
    output_directory: Path,
    targets: tuple[ManagementSecretTarget, ...] = (),
    selection: str = "all",
    timeout_seconds: float = 300.0,
    no_open: bool = False,
    into_existing_directory: bool = False,
    replace: bool = False,
) -> tuple[SecretExportResult, SecretDescriptorExport]:
    """Export current provider values into the literal descriptor pair."""

    if into_existing_directory:
        output_directory = validate_existing_secret_descriptor_directory(
            output_directory,
            targets,
            replace=replace,
        )
    else:
        if replace:
            raise ManagementCliError(
                "secret_export_replace_invalid",
                "Replacing secret files requires --into-descriptor-directory.",
            )
        output_directory = validate_secret_descriptor_export(
            output_directory,
            targets,
        )
    service = BrowserSecretExportService(
        client=SecretExportClient(transport=HttpxSecretExportTransport())
    )
    browser_options: dict[str, Any] = {}
    if no_open:
        browser_options["browser_opener"] = _print_manual_authorization_url
    result = await service.export(
        target=target,
        targets=targets,
        selection=selection,
        timeout_seconds=timeout_seconds,
        **browser_options,
    )
    exported = (
        write_secret_descriptors_into_directory(
            output_directory,
            result.values,
            replace=replace,
        )
        if into_existing_directory
        else write_secret_descriptors(output_directory, result.values)
    )
    return result, exported


def _secret_export_view(
    target: ManagementTarget,
    result: SecretExportResult,
    exported: SecretDescriptorExport,
) -> dict[str, Any]:
    return {
        "schema": "kdcube_cli.secret_descriptor_export.v1",
        "ok": True,
        "target": {"tenant": target.tenant, "project": target.project},
        "request_digest": result.request_digest,
        "approval": {
            "assurance": result.assurance,
            "method": result.approval_method,
            "verified_at": result.approval_verified_at,
        },
        "output": {
            "directory": str(exported.directory),
            "platform_descriptor": str(exported.platform_path),
            "bundles_descriptor": str(exported.bundles_path),
            "platform_secret_count": exported.platform_count,
            "bundle_secret_count": exported.bundle_count,
            "user_secret_count": exported.user_count,
            "total_secret_count": exported.total_count,
            "permissions": (
                {"directory_mode": "0700", "file_mode": "0600"}
                if sys.platform != "win32"
                else {"windows_acl": "inherited_from_output_parent"}
            ),
        },
    }


def _import_summary(imported: Any) -> dict[str, Any]:
    return {
        "directory": str(imported.directory),
        "platform_secret_count": imported.platform_count,
        "bundle_secret_count": imported.bundle_count,
        "user_secret_count": imported.user_count,
        "total_secret_count": imported.total_count,
    }


async def _run_import(
    args: argparse.Namespace,
    *,
    target: ManagementTarget,
) -> int:
    imported = load_secret_descriptors(Path(args.input_directory))
    if args.dry_run:
        _print_json(
            {
                "schema": "kdcube_cli.secret_descriptor_import.v1",
                "ok": True,
                "dry_run": True,
                "target": {"tenant": target.tenant, "project": target.project},
                "input": _import_summary(imported),
                "applied": 0,
            }
        )
        return 0
    if not args.yes:
        raise ManagementCliError(
            "secret_import_confirmation_required",
            "Secret import requires --yes after reviewing --dry-run.",
        )

    result = await apply_secret_descriptor_import(
        target=target,
        imported=imported,
        bearer=delegated_credential_from_input(args),
        no_open=bool(args.no_open),
        no_wait=bool(args.no_wait),
    )
    if result.denial is not None and result.failed_request is not None:
        _print_json(secret_import_result_view(target, imported, result))
        return 3

    _print_json(secret_import_result_view(target, imported, result))
    return 0


@dataclass(frozen=True)
class SecretDescriptorApplyResult:
    applied: int
    failed_target: ManagementSecretTarget | None = None
    failed_request: ManagementRequest | None = None
    denial: ManagementDenial | None = None

    @property
    def ok(self) -> bool:
        return self.denial is None


async def apply_secret_descriptor_import(
    *,
    target: ManagementTarget,
    imported: SecretDescriptorImport,
    bearer: str,
    no_open: bool,
    no_wait: bool,
) -> SecretDescriptorApplyResult:
    """Upsert every literal descriptor value through the selected provider."""

    client = ManagementClient(transport=HttpxManagementTransport())
    applied = 0
    for exported in imported.values:
        secret_target = exported.target
        request = ManagementRequest.secret_write(
            target,
            scope=secret_target.scope,
            key=secret_target.key,
            bundle_id=secret_target.bundle_id,
            user_id=secret_target.user_id,
            value=exported.value,
        )
        result = await _execute_with_consent(
            client,
            request,
            bearer,
            no_open=no_open,
            no_wait=no_wait,
        )
        if isinstance(result, ManagementDenial):
            return SecretDescriptorApplyResult(
                applied=applied,
                failed_target=secret_target,
                failed_request=request,
                denial=result,
            )
        applied += 1
    return SecretDescriptorApplyResult(applied=applied)


def secret_import_result_view(
    target: ManagementTarget,
    imported: SecretDescriptorImport,
    result: SecretDescriptorApplyResult,
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "schema": "kdcube_cli.secret_descriptor_import.v1",
        "ok": result.ok,
        "dry_run": False,
        "target": {"tenant": target.tenant, "project": target.project},
        "input": _import_summary(imported),
        "applied": result.applied,
        "semantics": "upsert_present_values",
    }
    if (
        result.denial is not None
        and result.failed_request is not None
        and result.failed_target is not None
    ):
        view["failed_target"] = result.failed_target.to_dict()
        view["denial"] = management_view(result.failed_request, result.denial)
    return view


def run_management_secret_command(
    args: argparse.Namespace,
    *,
    local_workdir: Path | None,
    tenant: str,
    project: str,
) -> int:
    target = _target_from_args(
        args,
        local_workdir=local_workdir,
        tenant=tenant,
        project=project,
    )
    if args.secrets_command == "export":
        return asyncio.run(_run_export(args, target=target))
    if args.secrets_command == "import":
        return asyncio.run(_run_import(args, target=target))
    return asyncio.run(_run_delegated(args, target=target))


__all__ = [
    "SecretDescriptorApplyResult",
    "apply_secret_descriptor_import",
    "delegated_credential_from_input",
    "export_secret_descriptor_pair",
    "local_management_target",
    "run_management_secret_command",
    "secret_import_result_view",
]
