# SPDX-License-Identifier: MIT
from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

from kdcube_cli import installer as installer_mod
from kdcube_cli.control.errors import (
    AmbiguousApplicationSurfaceError,
    ApplicationNotFoundError,
    ApplicationSurfaceNotFoundError,
    KDCubeControlError,
    MissingTargetError,
    OperationFailedError,
    UnsupportedCapabilityError,
)
from kdcube_cli.control.execution import CommandRunner, SubprocessCommandRunner
from kdcube_cli.control.initialization import (
    EventSink,
    InstallerRuntimeInitializer,
    RuntimeInitializer,
)
from kdcube_cli.control.local_lifecycle import LocalLifecycleController
from kdcube_cli.deployment_preservation import DeploymentPreservationError
from kdcube_cli.control.local_runtime import (
    DEFAULT_CLI_LOCK,
    LocalRuntimeContext,
    build_local_context,
    local_paths,
    local_public_base_url,
    local_target_identity,
    read_json,
    resolve_repo_root,
    runtime_env_exists,
)
from kdcube_cli.control.models import (
    ApplicationRef,
    ApplicationStatus,
    ApplicationSurface,
    DeploymentTargetRef,
    Diagnostic,
    DiagnosticSeverity,
    LocalInitializationRequest,
    LocalPlatformSourceRequest,
    LocalStartRequest,
    LocalStopRequest,
    OperationResult,
    PreparedPlatformSource,
    ReleaseCoordinates,
    SurfaceSelector,
    TargetCapabilities,
    TargetCapability,
    TargetKind,
    TargetStatus,
)
from kdcube_cli.control.source import prepare_local_platform_source
from kdcube_cli.control.surfaces import application_inventory, load_descriptor


BrowserOpener = Callable[[str], bool]

LOCAL_CAPABILITIES = TargetCapabilities(
    frozenset(
        {
            TargetCapability.INITIALIZE,
            TargetCapability.START,
            TargetCapability.STOP,
            TargetCapability.STATUS,
            TargetCapability.RESOLVE_ENDPOINTS,
            TargetCapability.OPEN,
        }
    )
)


class LocalDeploymentTarget:
    def __init__(
        self,
        reference: DeploymentTargetRef,
        *,
        repo_root: Optional[Path] = None,
        runner: Optional[CommandRunner] = None,
        initializer: Optional[RuntimeInitializer] = None,
        lock_file: Path = DEFAULT_CLI_LOCK,
        stream_process_output: bool = False,
    ) -> None:
        if reference.kind != TargetKind.LOCAL or reference.workdir is None:
            raise ValueError("LocalDeploymentTarget requires a local target reference.")
        tenant, project = local_target_identity(reference.workdir)
        self._reference = DeploymentTargetRef.local(
            reference.workdir,
            tenant=reference.tenant or tenant,
            project=reference.project or project,
        )
        self._repo_root = Path(repo_root).expanduser().resolve() if repo_root is not None else None
        self._runner = runner or SubprocessCommandRunner()
        self._initializer = initializer or InstallerRuntimeInitializer()
        self._lock_file = Path(lock_file).expanduser().resolve()
        self._stream_process_output = bool(stream_process_output)

    @property
    def reference(self) -> DeploymentTargetRef:
        return self._reference

    @property
    def capabilities(self) -> TargetCapabilities:
        return LOCAL_CAPABILITIES

    def _require(self, capability: TargetCapability) -> None:
        if not self.capabilities.supports(capability):
            raise UnsupportedCapabilityError(self.reference.target_id, capability.value)

    def _resolved_repo_root(self) -> Path:
        return resolve_repo_root(self.reference, self._repo_root)

    def _context(self) -> LocalRuntimeContext:
        return build_local_context(self.reference, self._resolved_repo_root())

    def _lifecycle(self, context: Optional[LocalRuntimeContext] = None) -> LocalLifecycleController:
        return LocalLifecycleController(
            self.reference,
            context or self._context(),
            runner=self._runner,
            lock_file=self._lock_file,
            stream_process_output=self._stream_process_output,
        )

    def initialize(
        self,
        request: LocalInitializationRequest,
        *,
        event_sink: Optional[EventSink] = None,
    ) -> OperationResult:
        self._require(TargetCapability.INITIALIZE)
        if self.reference.workdir is None:
            raise MissingTargetError(self.reference.target_id)
        if (self.reference.workdir / "config" / "install-meta.json").exists():
            raise OperationFailedError(
                "initialize",
                self.reference.target_id,
                f"An initialized runtime already exists at {self.reference.workdir}.",
                recovery={"action": "Use refresh, or choose a new target."},
            )
        try:
            self._initializer.prepare(
                target=self.reference,
                repo_root=self._resolved_repo_root(),
                request=request,
                event_sink=event_sink,
            )
        except KDCubeControlError:
            raise
        except Exception as exc:
            raise OperationFailedError(
                "initialize",
                self.reference.target_id,
                f"Runtime initialization failed: {type(exc).__name__}",
            ) from exc
        if not runtime_env_exists(self.reference.workdir):
            raise OperationFailedError(
                "initialize",
                self.reference.target_id,
                "Runtime preparation completed without creating config/.env.",
            )
        return OperationResult(
            target=self.reference,
            operation="initialize",
            changed=True,
            running=False,
        )

    def prepare_source(
        self,
        request: LocalPlatformSourceRequest = LocalPlatformSourceRequest(),
        *,
        event_sink: Optional[EventSink] = None,
    ) -> PreparedPlatformSource:
        self._require(TargetCapability.INITIALIZE)
        return prepare_local_platform_source(
            self.reference,
            request,
            runner=self._runner,
            event_sink=event_sink,
        )

    def status(self, *, probe_runtime: bool = True) -> TargetStatus:
        self._require(TargetCapability.STATUS)
        if self.reference.workdir is None or not self.reference.workdir.exists():
            raise MissingTargetError(self.reference.target_id)
        diagnostics: List[Diagnostic] = []
        initialized = runtime_env_exists(self.reference.workdir)
        context: Optional[LocalRuntimeContext] = None
        try:
            context = self._context()
        except KDCubeControlError as exc:
            diagnostics.append(_diagnostic_from_error(exc))
        except Exception:
            diagnostics.append(
                Diagnostic(
                    code="target.runtime_context_invalid",
                    severity=DiagnosticSeverity.ERROR,
                    summary="The local runtime context could not be resolved.",
                    recovery={"workdir": str(self.reference.workdir)},
                )
            )

        config_dir = self.reference.workdir / "config"
        assembly_path = config_dir / "assembly.yaml"
        bundles_path = config_dir / "bundles.yaml"
        assembly: Mapping[str, object] = {}
        if assembly_path.exists():
            try:
                assembly = load_descriptor(assembly_path)
            except KDCubeControlError as exc:
                diagnostics.append(_diagnostic_from_error(exc))
        target_ref = self.reference
        tenant, project = installer_mod.descriptor_context_from_assembly(assembly)
        if tenant or project:
            target_ref = DeploymentTargetRef.local(
                self.reference.workdir,
                tenant=tenant or self.reference.tenant,
                project=project or self.reference.project,
            )

        env_file = None
        if initialized:
            try:
                env_file = installer_mod.load_env_file(config_dir / ".env")
            except Exception:
                diagnostics.append(
                    Diagnostic(
                        code="target.env_invalid",
                        severity=DiagnosticSeverity.ERROR,
                        summary="The runtime .env file could not be read.",
                        recovery={"path": str(config_dir / ".env")},
                    )
                )
        public_base_url = local_public_base_url(env_file)
        applications: Tuple[ApplicationStatus, ...] = ()
        default_application_id = None
        if bundles_path.exists() and target_ref.tenant and target_ref.project:
            try:
                applications, default_application_id = application_inventory(
                    load_descriptor(bundles_path),
                    target_ref,
                    endpoint=public_base_url,
                )
            except KDCubeControlError as exc:
                diagnostics.append(_diagnostic_from_error(exc))

        meta = read_json(config_dir / "install-meta.json") or {}
        platform_value = assembly.get("platform")
        platform = platform_value if isinstance(platform_value, dict) else {}
        platform_ref = str(meta.get("platform_ref") or platform.get("ref") or "").strip() or None
        release = ReleaseCoordinates(
            platform_ref=platform_ref,
            install_mode=str(meta.get("install_mode") or "").strip() or None,
            docker_namespace=str(meta.get("dockerhub_namespace") or "").strip() or None,
        )

        running: Optional[bool] = None
        if probe_runtime and initialized and context is not None:
            lifecycle = self._lifecycle(context)
            try:
                lifecycle.ensure_docker_responsive()
                running = bool(lifecycle.running_services())
            except KDCubeControlError as exc:
                diagnostics.append(
                    _diagnostic_from_error(exc, DiagnosticSeverity.WARNING)
                )

        resolved_paths = (
            local_paths(context, env_file, assembly_path, bundles_path)
            if context is not None
            else None
        )
        if not initialized:
            diagnostics.append(
                Diagnostic(
                    code="target.not_initialized",
                    severity=DiagnosticSeverity.WARNING,
                    summary="The local target has not been initialized.",
                    recovery={"action": "Initialize the selected target."},
                )
            )
        return TargetStatus(
            reference=target_ref,
            capabilities=self.capabilities,
            reachable=True,
            initialized=initialized,
            running=running,
            release=release,
            applications=applications,
            diagnostics=tuple(diagnostics),
            local_paths=resolved_paths,
            default_application_id=default_application_id,
            public_base_url=public_base_url,
        )

    def describe(self) -> TargetStatus:
        return self.status()

    def application_status(self, application: ApplicationRef) -> ApplicationStatus:
        status = self.status(probe_runtime=False)
        for candidate in status.applications:
            if candidate.reference.bundle_id == application.bundle_id:
                return candidate
        raise ApplicationNotFoundError(self.reference.target_id, application.bundle_id)

    def resolve_surface(
        self,
        application: ApplicationRef,
        selector: Optional[SurfaceSelector] = None,
        *,
        openable_only: bool = False,
    ) -> ApplicationSurface:
        self._require(TargetCapability.RESOLVE_ENDPOINTS)
        status = self.application_status(application)
        candidates = list(status.surfaces)
        if selector is not None:
            if selector.kind is not None:
                candidates = [item for item in candidates if item.kind == selector.kind]
            if selector.alias:
                candidates = [item for item in candidates if item.alias == selector.alias]
        if openable_only:
            candidates = [item for item in candidates if item.openable]
        elif selector is None:
            openable = [item for item in candidates if item.openable]
            if openable:
                candidates = openable
        selector_label = selector.label() if selector is not None else "default"
        if not candidates:
            raise ApplicationSurfaceNotFoundError(application.bundle_id, selector_label)
        if len(candidates) > 1:
            raise AmbiguousApplicationSurfaceError(
                application.bundle_id,
                [item.surface_id for item in candidates],
            )
        return candidates[0]

    def application_url(
        self,
        application: ApplicationRef,
        selector: Optional[SurfaceSelector] = None,
    ) -> str:
        return self.resolve_surface(application, selector, openable_only=True).url

    def open_application(
        self,
        application: ApplicationRef,
        selector: Optional[SurfaceSelector] = None,
        *,
        opener: BrowserOpener = webbrowser.open,
    ) -> OperationResult:
        self._require(TargetCapability.OPEN)
        surface = self.resolve_surface(application, selector, openable_only=True)
        try:
            opened = bool(opener(surface.url))
        except Exception as exc:
            raise OperationFailedError(
                "open",
                self.reference.target_id,
                f"The browser could not open the application URL: {type(exc).__name__}",
            ) from exc
        if not opened:
            raise OperationFailedError(
                "open",
                self.reference.target_id,
                "The browser did not accept the application URL.",
            )
        return OperationResult(
            target=self.reference,
            operation="open",
            changed=False,
            url=surface.url,
        )

    def start(
        self,
        request: LocalStartRequest = LocalStartRequest(),
        *,
        event_sink: Optional[EventSink] = None,
        preservation_manifest: str | None = None,
    ) -> OperationResult:
        self._require(TargetCapability.START)
        try:
            return self._lifecycle().start(request, event_sink=event_sink,
                                           preservation_manifest=preservation_manifest)
        except DeploymentPreservationError as exc:
            raise OperationFailedError("start", self.reference.target_id,
                                       f"Baseline preservation refused before start: {exc}") from exc
        except KDCubeControlError:
            raise
        except Exception as exc:
            raise OperationFailedError(
                "start",
                self.reference.target_id,
                f"Runtime start failed: {type(exc).__name__}",
            ) from exc

    def stop(
        self,
        request: LocalStopRequest = LocalStopRequest(),
        *,
        event_sink: Optional[EventSink] = None,
        preservation_manifest: str | None = None,
    ) -> OperationResult:
        self._require(TargetCapability.STOP)
        try:
            return self._lifecycle().stop(request, event_sink=event_sink,
                                          preservation_manifest=preservation_manifest)
        except DeploymentPreservationError as exc:
            raise OperationFailedError("stop", self.reference.target_id,
                                       f"Baseline preservation refused before stop: {exc}") from exc
        except KDCubeControlError:
            raise
        except Exception as exc:
            raise OperationFailedError(
                "stop",
                self.reference.target_id,
                f"Runtime stop failed: {type(exc).__name__}",
            ) from exc


def _diagnostic_from_error(
    error: KDCubeControlError,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> Diagnostic:
    return Diagnostic(
        code=error.code.value,
        severity=severity,
        summary=error.summary,
        recovery=error.recovery,
    )
