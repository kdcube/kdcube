# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional, Protocol

from rich.errors import MarkupError
from rich.text import Text

from kdcube_cli import installer as installer_mod
from kdcube_cli.control.errors import (
    InvalidDescriptorError,
    KDCubeControlError,
    MissingTargetError,
    OperationFailedError,
)
from kdcube_cli.control.models import (
    ControlEvent,
    ControlEventKind,
    DeploymentTargetRef,
    LocalInitializationRequest,
)
from kdcube_cli.control.surfaces import load_descriptor


EventSink = Callable[[ControlEvent], None]


class RuntimeInitializer(Protocol):
    def prepare(
        self,
        *,
        target: DeploymentTargetRef,
        repo_root: Path,
        request: LocalInitializationRequest,
        event_sink: Optional[EventSink],
    ) -> None:
        ...


class _EventConsole:
    """Duck-typed installer output adapter; it has no prompt implementation."""

    def __init__(self, event_sink: Optional[EventSink]) -> None:
        self._event_sink = event_sink

    def print(self, *values: object, **_kwargs: object) -> None:
        if self._event_sink is None:
            return
        rendered = " ".join(str(value) for value in values)
        try:
            message = Text.from_markup(rendered).plain
        except MarkupError:
            message = rendered
        self._event_sink(
            ControlEvent(
                kind=ControlEventKind.PROGRESS,
                message=message,
            )
        )

    def input(self, *_args: object, **_kwargs: object) -> str:
        raise RuntimeError("Interactive input is unavailable in the control API.")


_INSTALLER_ENV_LOCK = threading.RLock()


@contextmanager
def _installer_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Isolate the legacy installer's process environment compatibility layer."""

    with _INSTALLER_ENV_LOCK:
        previous = dict(os.environ)
        try:
            os.environ.update(values)
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous)


class InstallerRuntimeInitializer:
    """Prepare a runtime through the existing non-interactive installer core."""

    def prepare(
        self,
        *,
        target: DeploymentTargetRef,
        repo_root: Path,
        request: LocalInitializationRequest,
        event_sink: Optional[EventSink],
    ) -> None:
        if target.workdir is None:
            raise MissingTargetError(target.target_id)
        descriptor_source = (
            Path(request.descriptor_source).expanduser().resolve()
            if request.descriptor_source is not None
            else (repo_root / "app" / "ai-app" / "deployment").resolve()
        )
        assembly_path = descriptor_source / "assembly.yaml"
        load_descriptor(assembly_path)

        auth_type = str(request.auth_type or "").strip().lower()
        auth_provider = str(request.auth_provider or "").strip().lower()
        auth_client_id = str(request.auth_client_id or "").strip()
        bootstrap_admin_email = str(request.bootstrap_admin_email or "").strip()
        if not auth_type and any(
            (auth_provider, auth_client_id, bootstrap_admin_email)
        ):
            raise InvalidDescriptorError(
                str(assembly_path),
                "authentication inputs require an explicit auth type",
            )
        if auth_type:
            if auth_type not in {"bundle", "simple"}:
                raise InvalidDescriptorError(
                    str(assembly_path),
                    "the non-interactive control API supports bundle or simple auth",
                )
            if auth_type == "simple" and any(
                (auth_provider, auth_client_id, bootstrap_admin_email)
            ):
                raise InvalidDescriptorError(
                    str(assembly_path),
                    "Google authentication inputs apply only to bundle auth",
                )
            if auth_type == "bundle":
                auth_provider = auth_provider or "google"
                if auth_provider != "google":
                    raise InvalidDescriptorError(
                        str(assembly_path),
                        "bundle auth currently supports the google provider",
                    )
                if not auth_client_id or installer_mod.is_placeholder(auth_client_id):
                    raise InvalidDescriptorError(
                        str(assembly_path),
                        "bundle auth requires a Google OAuth client id",
                    )
            staged = installer_mod.stage_descriptor_directory(
                target.workdir / "config",
                source_dir=descriptor_source,
                ai_app_root=repo_root / "app" / "ai-app",
                require_complete=True,
            )
            assembly = staged.get("assembly")
            staged_assembly_path = staged.get("assembly_path")
            if not isinstance(assembly, dict) or not isinstance(
                staged_assembly_path, Path
            ):
                raise InvalidDescriptorError(
                    str(assembly_path), "the staged assembly descriptor is unavailable"
                )
            auth = assembly.get("auth")
            if not isinstance(auth, dict):
                auth = {}
                assembly["auth"] = auth
            auth["type"] = auth_type
            if auth_type == "bundle":
                auth.pop("idp", None)
                bundle = auth.get("bundle")
                if not isinstance(bundle, dict):
                    bundle = {}
                    auth["bundle"] = bundle
                bundle["provider"] = auth_provider
                bundle["client_id"] = auth_client_id
                if bootstrap_admin_email:
                    bundle["bootstrap_admin_email"] = bootstrap_admin_email
                else:
                    bundle.pop("bootstrap_admin_email", None)
            installer_mod.save_release_descriptor(staged_assembly_path, assembly)
            descriptor_source = (target.workdir / "config").resolve()
            assembly_path = descriptor_source / "assembly.yaml"

        parameterize_defaults = (
            request.parameterize_defaults
            or request.descriptor_source is None
            or bool(auth_type)
        )
        environment = {
            "KDCUBE_CLI_NONINTERACTIVE": "1",
            "KDCUBE_INIT_PREPARE_ONLY": "1",
            "KDCUBE_DESCRIPTORS_LOCATION": str(descriptor_source),
            "KDCUBE_ASSEMBLY_DESCRIPTOR_PATH": str(assembly_path),
            "KDCUBE_ASSEMBLY_USER_SUPPLIED": "0" if parameterize_defaults else "1",
            "KDCUBE_DEFAULT_DESCRIPTOR_BOOTSTRAP": "1"
            if parameterize_defaults
            else "0",
            "KDCUBE_DRY_RUN_PRINT_ENV": "0",
        }
        if target.tenant and target.project:
            environment["KDCUBE_PRESET_TENANT"] = target.tenant
            environment["KDCUBE_PRESET_PROJECT"] = target.project
        try:
            with _installer_environment(environment):
                installer_mod.run_setup(
                    _EventConsole(event_sink),
                    repo_root=repo_root,
                    workdir=target.workdir,
                    install_mode=request.install_mode,
                    release_ref=request.release_ref,
                    docker_namespace=request.docker_namespace,
                    dry_run=True,
                )
        except KDCubeControlError:
            raise
        except FileNotFoundError as exc:
            raise InvalidDescriptorError(
                str(descriptor_source),
                "a required descriptor or platform file is missing",
            ) from exc
        except ValueError as exc:
            raise InvalidDescriptorError(
                str(descriptor_source), "descriptor validation failed"
            ) from exc
        except SystemExit as exc:
            raise OperationFailedError(
                "initialize",
                target.target_id,
                "Runtime initialization failed in the non-interactive preparation step.",
            ) from exc
        except Exception as exc:
            raise OperationFailedError(
                "initialize",
                target.target_id,
                f"Runtime initialization failed: {type(exc).__name__}",
            ) from exc
