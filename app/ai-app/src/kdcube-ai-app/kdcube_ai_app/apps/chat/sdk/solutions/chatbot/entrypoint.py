# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/entrypoint.py

from __future__ import annotations

import asyncio
import importlib
import copy
import hashlib
import json
import os
import pathlib
import re
import shlex
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from kdcube_ai_app.apps.chat.emitters import (
    ChatCommunicator,
    build_comm_from_comm_context,
    build_relay_from_env,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id, index_agent_id
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import normalize_exec_runtime_config
from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import (
    LocalSidecarHandle,
    ensure_local_sidecar as ensure_runtime_local_sidecar,
    get_local_sidecar as get_runtime_local_sidecar,
    stop_local_sidecar as stop_runtime_local_sidecar,
    update_local_sidecar_runtime_metadata as update_runtime_local_sidecar_runtime_metadata,
)
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.apps.chat.sdk.solutions.chat import apply_chat_widget_engine
from kdcube_ai_app.infra.plugin.bundle_loader import api, on_reactive_event, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import (
    APP_STATE_KEYS,
    AgentLogger,
    Config,
    ModelServiceBase,
    _mid,
)
from kdcube_ai_app.tools.serialization import json_safe
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.turn_reporting import (
    _format_cost_table_markdown,
    _format_cost_summary_compact,
    _format_agent_breakdown_markdown,
)
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.accounting.calculator import RateCalculator
from kdcube_ai_app.apps.chat.sdk.viz.tsx_transpiler import ClientSideTSXTranspiler
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache_from_env

_REQUEST_LOCAL_UNSET = object()


class BaseEntrypoint:
    """
    Minimal, reusable bundle entrypoint base.
    Intended to be subclassed by bundle-specific workflows.
    """

    BUNDLE_ID = "kdcube.bundle.base"

    # Platform-level defaults for events.record.*.
    # Overridden by assembly.yaml (a:events.record.*) and then by
    # per-bundle bundles.yaml (config.events.record.*).
    _PERSIST_EVENTS_DEFAULT: list[str] = ["accounting.usage", "chat.turn.summary"]
    _PERSIST_EVENTS_ENABLED_DEFAULT: bool = True
    _TELEMETRY_EVENTS_ENABLED_DEFAULT: bool = True

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ExternalEventPayload = None,
        event_filter: Optional[Any] = None,
        ctx_client: Optional[Any] = None,
    ):
        self.config = config
        self.settings = get_settings()
        self.pg_pool = pg_pool
        self.redis = redis
        self._comm_context: Optional[ExternalEventPayload] = comm_context
        self._event_filter = event_filter

        self._comm: Optional[ChatCommunicator] = None
        self._comm_context_cv: ContextVar[object] = ContextVar(
            f"bundle_comm_context_{id(self)}",
            default=_REQUEST_LOCAL_UNSET,
        )
        self._comm_cv: ContextVar[object] = ContextVar(
            f"bundle_comm_{id(self)}",
            default=_REQUEST_LOCAL_UNSET,
        )
        self._conv_idx = None
        self._kb = None
        self._store = None
        self.ctx_client = ctx_client
        self.bundle_props: Dict[str, Any] = {}
        self.bundle_props = dict(self.bundle_props_defaults or {})
        self.kv_cache = create_kv_cache_from_env()

        self.logger = AgentLogger(f"{self.BUNDLE_ID}.Workflow", config.log_level)

        if getattr(self, "ctx_client", None) is None:
            self.ctx_client = None

        self._apply_configuration_overrides()
        self._rebuild_models_service()

    @property
    def comm_context(self) -> Optional[ExternalEventPayload]:
        comm_context_cv = getattr(self, "_comm_context_cv", None)
        if comm_context_cv is not None:
            bound = comm_context_cv.get()
            if bound is not _REQUEST_LOCAL_UNSET:
                return bound
        return getattr(self, "_comm_context", None)

    @comm_context.setter
    def comm_context(self, value: Optional[ExternalEventPayload]) -> None:
        self._comm_context = value

    def rebind_request_context(
        self,
        *,
        comm_context: Optional[ExternalEventPayload] = None,
        pg_pool: Any = None,
        redis: Any = None,
    ) -> None:
        """
        Refresh request-bound state on cached singleton workflows.
        """
        if comm_context is not None:
            self._comm_context_cv.set(comm_context)
            self._comm_cv.set(None)
        if pg_pool is not None:
            self.pg_pool = pg_pool
        if redis is not None:
            self.redis = redis

    @contextmanager
    def bind_request_context(
        self,
        *,
        comm_context: Optional[ExternalEventPayload] = None,
        comm: Any = _REQUEST_LOCAL_UNSET,
    ):
        """
        Temporarily bind request-scoped state for this entrypoint instance.

        Use this when bundle code intentionally scopes a nested run to a
        different user/conversation/turn. Unlike rebind_request_context(), this
        restores the previous task-local binding when the nested run exits.
        """
        comm_context_token = None
        comm_token = None
        try:
            if comm_context is not None:
                comm_context_token = self._comm_context_cv.set(comm_context)
                comm_token = self._comm_cv.set(None if comm is _REQUEST_LOCAL_UNSET else comm)
            elif comm is not _REQUEST_LOCAL_UNSET:
                comm_token = self._comm_cv.set(comm)
            yield self
        finally:
            if comm_token is not None:
                self._comm_cv.reset(comm_token)
            if comm_context_token is not None:
                self._comm_context_cv.reset(comm_context_token)

    # ---------- Common helpers ----------

    def _apply_configuration_overrides(self) -> None:
        configuration = self._resolve_configuration() or {}

        wf_roles = configuration.get("role_models") or {}
        if wf_roles:
            self.config.set_role_models({**(self.config.role_models or {}), **wf_roles})

        wf_embedding = configuration.get("embedding") or {}
        if wf_embedding:
            self.config.set_embedding(wf_embedding)

    def _rebuild_models_service(self) -> None:
        self.models_service = ModelServiceBase(self.config)

    @property
    def bundle_props_defaults(self) -> Dict[str, Any]:
        """
        Bundle-defined configuration defaults (without external overrides).
        """
        return self._configuration_without_overrides()

    def _configuration_without_overrides(self) -> Dict[str, Any]:
        prev_props = getattr(self, "bundle_props", None)
        try:
            self.bundle_props = {}
            config = self._resolve_configuration()
            return dict(config or {}) if isinstance(config, dict) else {}
        finally:
            if prev_props is None:
                try:
                    del self.bundle_props
                except AttributeError:
                    pass
            else:
                self.bundle_props = prev_props

    def _resolve_configuration(self) -> Any:
        """
        Resolve configuration defined as a @property or legacy method.
        Some bundles still implement configuration() as a method without @property.
        """
        config_attr = getattr(self, "configuration", None)
        if callable(config_attr):
            try:
                return config_attr()
            except TypeError:
                # In case a @property-like object is callable but expects no args
                return config_attr
        return config_attr

    def runtime_identity(self) -> Dict[str, str]:
        """
        Return request-bound runtime identity in the compact form used by SDK
        subsystems that need tenant/project/user-scoped storage.

        Prefer this over bundle-local helpers. The values come from the current
        request context when available, merged over the runtime ContextVar
        binding used by tools, jobs, and Data Bus handlers.
        """
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity

            identity = dict(get_current_user_identity() or {})
        except Exception:
            identity = {}

        actor = getattr(self.comm_context, "actor", None)
        user_ctx = getattr(self.comm_context, "user", None)
        tenant = getattr(actor, "tenant_id", None) if actor is not None else None
        project = getattr(actor, "project_id", None) if actor is not None else None
        user_id = getattr(user_ctx, "user_id", None) if user_ctx is not None else None
        fingerprint = getattr(user_ctx, "fingerprint", None) if user_ctx is not None else None
        user_type = getattr(user_ctx, "user_type", None) if user_ctx is not None else None

        return {
            "tenant": str(tenant or identity.get("tenant_id") or identity.get("tenant") or "").strip(),
            "project": str(project or identity.get("project_id") or identity.get("project") or "").strip(),
            "user": str(user_id or identity.get("user_id") or "").strip(),
            "fingerprint": str(fingerprint or identity.get("fingerprint") or "").strip(),
            "user_type": str(user_type or identity.get("user_type") or "anonymous").strip() or "anonymous",
        }

    def _react_event_sources(self) -> Any:
        """Return connected event-source policies/readers for ReAct helpers.

        Bundle assemblies that mount SDK components such as memory or canvas
        should override this and return an EventSourceSubsystem composed from
        those component modules. The base fallback intentionally stays empty so
        the shared preview operation still works for plain chat bundles.
        """

        return None

    def _react_debug_dir(self) -> Optional[Path]:
        storage_path = str(getattr(self.settings, "STORAGE_PATH", "") or "").strip()
        if not storage_path:
            return None
        return Path(storage_path) / "react-debug"

    def _react_preview_user_id(self, payload: Mapping[str, Any]) -> str:
        resolver = getattr(self, "_resolve_user_id", None)
        if callable(resolver):
            try:
                resolved = resolver(payload)
                if resolved:
                    return str(resolved)
            except Exception:
                pass
        ident = self.runtime_identity()
        return str(
            payload.get("user_id")
            or ident.get("user")
            or ident.get("fingerprint")
            or "anonymous"
        ).strip() or "anonymous"

    @api(method="POST", alias="react_context_preview", route="operations", user_types=())
    async def react_context_preview(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Render proposed `external_events[]` through ReAct event policies only.

        This powers the reusable chat widget dry-run context preview. It does
        not write conversation state and does not call the model.
        """

        payload: Dict[str, Any] = {}
        if isinstance(data, Mapping):
            nested = data.get("data")
            if isinstance(nested, Mapping):
                payload.update({str(k): v for k, v in nested.items()})
            else:
                payload.update({str(k): v for k, v in data.items()})
        for key, value in kwargs.items():
            if key not in {"request", "alias", "route", "endpoint_alias"} and value is not None:
                payload[key] = value
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.events import (
                render_external_events_preview_payload,
            )

            result = await render_external_events_preview_payload(
                payload,
                event_sources=self._react_event_sources(),
                runtime_identity=self.runtime_identity(),
                user_id=self._react_preview_user_id(payload),
                bundle_id=getattr(getattr(self, "config", None), "ai_bundle_spec", None).id
                if getattr(getattr(self, "config", None), "ai_bundle_spec", None) is not None
                else "",
                debug_dir=self._react_debug_dir(),
            )
            if not result.get("ok"):
                try:
                    self.logger.log(
                        f"[react_context_preview] failed: {result.get('error') or result.get('message')}",
                        "WARNING",
                    )
                except Exception:
                    pass
            return result
        except Exception as exc:
            try:
                self.logger.log(f"[react_context_preview] failed: {traceback.format_exc()}", "ERROR")
            except Exception:
                pass
            return {"ok": False, "error": str(exc), "status": 500}

    async def on_bundle_load(self, **kwargs) -> None:
        """
        Optional one-time hook called when the bundle is first loaded
        (per process, per tenant/project). Override in bundles that need
        to prepare local assets or indexes.

        Supported kwargs (pass only what you accept):
          - bundle_spec
          - agentic_spec
          - storage_root
          - config
          - comm_context
          - pg_pool
          - redis
          - logger
        """
        state = {
            "tenant": getattr(getattr(self.comm_context, "actor", None), "tenant_id", None),
            "project": getattr(getattr(self.comm_context, "actor", None), "project_id", None),
        }
        if state["tenant"] and state["project"]:
            await self.refresh_bundle_props(
                state=state,
                notify=False,
                reason="bundle.on_load",
            )
        await self._ensure_ui_build()
        return None

    async def on_props_changed(
        self,
        *,
        previous_props: Dict[str, Any],
        current_props: Dict[str, Any],
        reason: str = "refresh_bundle_props",
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        updated_by: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """
        Optional hook fired after effective bundle props changed for this bundle instance.

        Default behavior is no-op. Override in bundles that need to reconcile
        long-lived side effects when props change.
        """
        previous_ui = previous_props.get("ui") if isinstance(previous_props, dict) else None
        current_ui = current_props.get("ui") if isinstance(current_props, dict) else None
        if previous_ui != current_ui:
            bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None)
            current_widgets = []
            if isinstance(current_ui, dict):
                widgets = current_ui.get("widgets")
                if isinstance(widgets, dict):
                    current_widgets = sorted(str(alias) for alias in widgets.keys())
            self.logger.log(
                "[bundle.ui] props changed; reconciling UI builds "
                f"bundle={bundle_id} reason={reason} tenant={tenant} project={project} "
                f"ui_widgets={current_widgets}",
                "INFO",
            )
            await self._ensure_ui_build()
        return None

    @staticmethod
    def _ui_source_signature(root: pathlib.Path) -> str:
        ignored_dirs = {"node_modules", ".git", "dist", "build", ".vite", ".vite-temp", "__pycache__"}
        ignored_suffixes = {".tsbuildinfo"}
        sha = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part in ignored_dirs for part in rel.parts):
                continue
            if path.is_dir():
                continue
            if path.suffix in ignored_suffixes:
                continue
            if BaseEntrypoint._is_ui_generated_shadow_file(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            sha.update(rel.as_posix().encode("utf-8"))
            sha.update(b"\0")
            sha.update(str(stat.st_size).encode("ascii"))
            sha.update(b"\0")
            sha.update(str(stat.st_mtime_ns).encode("ascii"))
            sha.update(b"\n")
        return sha.hexdigest()

    @staticmethod
    def _is_ui_generated_shadow_file(path: pathlib.Path) -> bool:
        """Return true for generated JS siblings that shadow TS/TSX source."""
        name = path.name
        parent = path.parent

        suffix = path.suffix
        if suffix in {".js", ".jsx"}:
            stem = path.stem
        elif name.endswith(".js.map"):
            stem = name[: -len(".js.map")]
        elif name.endswith(".jsx.map"):
            stem = name[: -len(".jsx.map")]
        else:
            return False

        return (parent / f"{stem}.ts").exists() or (parent / f"{stem}.tsx").exists()

    @staticmethod
    def _ui_copy_ignore_patterns():
        import shutil

        base_ignore = shutil.ignore_patterns(
            "node_modules",
            "dist",
            "build",
            ".vite",
            ".vite-temp",
            ".react_workspace_git",
            ".git",
            "__pycache__",
            "*.tsbuildinfo",
        )

        def ignore_generated_shadow_files(directory: str, names: list[str]) -> set[str]:
            ignored = set(base_ignore(directory, names))
            parent = pathlib.Path(directory)
            for name in names:
                if BaseEntrypoint._is_ui_generated_shadow_file(parent / name):
                    ignored.add(name)
            return ignored

        return ignore_generated_shadow_files

    @staticmethod
    def _ui_config_enabled(cfg: Dict[str, Any]) -> bool:
        value = cfg.get("enabled", True)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        return str(value).strip().lower() not in {"false", "disable", "disabled", "off", "0"}

    @staticmethod
    def _prepare_ui_build_command(build_command: str, tmp_dest: pathlib.Path) -> str:
        """
        Resolve KDCube UI build placeholders without turning the output directory
        into a positional build argument.
        """
        placeholder = "<VI_BUILD_DEST_ABSOLUTE_PATH>"
        alt_placeholder = "<VITE_BUILD_DEST_ABSOLUTE_PATH>"
        placeholder_re = re.escape(placeholder)
        alt_placeholder_re = re.escape(alt_placeholder)
        quoted_placeholder_re = (
            rf"(?:{placeholder_re}|{alt_placeholder_re}|\"{placeholder_re}\"|'{placeholder_re}'|"
            rf"\"{alt_placeholder_re}\"|'{alt_placeholder_re}')"
        )
        tmp_dest_re = re.escape(str(tmp_dest))
        quoted_tmp_dest_re = rf"(?:{tmp_dest_re}|\"{tmp_dest_re}\"|'{tmp_dest_re}')"
        tmp_build_dir_arg_re = (
            rf"(?:{quoted_placeholder_re}|{quoted_tmp_dest_re}|"
            rf"\"[^\"]*\.ui\.build\.tmp\.[^\"]*\"|'[^']*\.ui\.build\.tmp\.[^']*'|"
            rf"\S*\.ui\.build\.tmp\.\S*)"
        )
        command = str(build_command or "").strip()

        for var_name in ("OUTDIR", "VI_BUILD_DEST_ABSOLUTE_PATH", "VITE_BUILD_DEST_ABSOLUTE_PATH"):
            command = re.sub(
                rf"(?<!\S){re.escape(var_name)}=(?:{quoted_placeholder_re}|{quoted_tmp_dest_re})\s+",
                "",
                command,
            )

        # If the temp output path is passed as a positional npm build argument,
        # npm appends it to the package script and Vite treats the output folder
        # as the project root. The runner provides this value through env.
        for separator in ("", r"\s+--"):
            command = re.sub(
                rf"(?<!\S)(npm\s+run\s+build){separator}\s+{tmp_build_dir_arg_re}(?=$|\s|[;&|])",
                r"\1",
                command,
            )

        return (
            command
            .replace(placeholder, shlex.quote(str(tmp_dest)))
            .replace(alt_placeholder, shlex.quote(str(tmp_dest)))
        )

    @staticmethod
    def _is_standard_npm_ui_build(command: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(command or "").strip())
        return normalized == "npm install --no-package-lock && npm run build"

    @staticmethod
    def _npm_install_args_for_ui_build(command: str) -> Optional[list[str]]:
        """
        Recognize npm-install plus npm-run-build commands so the build script can
        be executed directly. This avoids npm appending descriptor/output args to
        the package script.
        """
        normalized = re.sub(r"\s+", " ", str(command or "").strip())
        parts = [part.strip() for part in normalized.split("&&", 1)]
        if len(parts) != 2:
            return None
        install_part, build_part = parts
        try:
            install_args = shlex.split(install_part)
            build_args = shlex.split(build_part)
        except ValueError:
            return None
        while build_args and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", build_args[0]):
            build_args.pop(0)
        if len(install_args) < 2 or install_args[:2] != ["npm", "install"]:
            return None
        if len(build_args) < 3 or build_args[:3] != ["npm", "run", "build"]:
            return None
        return install_args

    @staticmethod
    def _build_env_from_command(command: str) -> Dict[str, str]:
        """Lift inline ``VAR=val`` assignments on the BUILD step (after the last
        ``&&``) into env vars — e.g. ``… && VITE_CHAT_UI=package npm run build``.

        The direct-build path runs the package.json ``build`` script with a fixed
        env, so without lifting these they are silently dropped (which is why the
        chat engine switch never reached vite). OUTDIR-family is excluded — it is
        already injected into the build env explicitly.
        """
        skip = {"OUTDIR", "VI_BUILD_DEST_ABSOLUTE_PATH", "VITE_BUILD_DEST_ABSOLUTE_PATH", "VITE_APP_OUT_DIR"}
        normalized = re.sub(r"\s+", " ", str(command or "").strip())
        build_part = normalized.split("&&")[-1].strip()
        try:
            tokens = shlex.split(build_part)
        except ValueError:
            return {}
        out: Dict[str, str] = {}
        for token in tokens:
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", token)
            if not match:
                break  # first non-assignment token is the command itself
            if match.group(1) not in skip:
                out[match.group(1)] = match.group(2)
        return out

    def _resolve_ui_src_path(self, *, src_folder: str, bundle_root: str) -> pathlib.Path:
        raw = str(src_folder or "").strip()
        if raw.startswith("sdk://") or raw.startswith("bundle://"):
            return self._resolve_ui_shared_source_path(source=raw, bundle_root=bundle_root)
        src_path = pathlib.Path(src_folder)
        if not src_path.is_absolute():
            return (pathlib.Path(bundle_root) / src_folder).resolve()
        return src_path.resolve()

    @staticmethod
    def _sdk_source_root() -> pathlib.Path:
        return pathlib.Path(__file__).resolve().parents[2]

    @staticmethod
    def _npm_packages_root() -> pathlib.Path:
        # The standalone components library (@kdcube/components-*) ships INSIDE the
        # installed app tree, at <kdcube-ai-app>/npm/packages, so it is copied into
        # the runtime image by the same `COPY src/kdcube-ai-app/ .` that ships the
        # Python package. That makes the relative position identical in the repo and
        # in the container:
        #   repo:      .../src/kdcube-ai-app/npm/packages
        #   container: /app/npm/packages
        # The SDK root resolves to <kdcube-ai-app>/kdcube_ai_app/apps/chat/sdk, so
        # the kdcube-ai-app root is parents[3] and `npm` sits beside kdcube_ai_app.
        #
        # An explicit override wins (useful for unusual layouts / tests); otherwise
        # we probe the consistent location and fall back to the legacy sibling path
        # (app/ai-app/src/npm) so an un-migrated checkout still resolves locally.
        override = os.environ.get("KDCUBE_NPM_PACKAGES_ROOT")
        if override:
            return pathlib.Path(override).expanduser().resolve()
        sdk_root = pathlib.Path(__file__).resolve().parents[2]
        candidates = (
            sdk_root.parents[3] / "npm" / "packages",   # shipped: <kdcube-ai-app>/npm (== /app/npm)
            sdk_root.parents[4] / "npm" / "packages",   # legacy local sibling: app/ai-app/src/npm
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _resolve_ui_shared_source_path(self, *, source: str, bundle_root: str) -> pathlib.Path:
        raw = str(source or "").strip()
        if raw.startswith("sdk://"):
            rel = raw[len("sdk://"):].strip().lstrip("/")
            if not rel:
                raise ValueError("shared UI source sdk:// path is empty")
            root = self._sdk_source_root()
            resolved = (root / rel).resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"shared UI source escapes SDK root: {source!r}") from exc
            return resolved
        if raw.startswith("npm://"):
            # Standalone components library, e.g. npm://components-core/src ->
            # app/ai-app/src/npm/packages/components-core/src.
            rel = raw[len("npm://"):].strip().lstrip("/")
            if not rel:
                raise ValueError("shared UI source npm:// path is empty")
            root = self._npm_packages_root()
            resolved = (root / rel).resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"shared UI source escapes npm packages root: {source!r}") from exc
            return resolved
        if raw.startswith("bundle://"):
            rel = raw[len("bundle://"):].strip().lstrip("/")
            if not rel:
                raise ValueError("shared UI source bundle:// path is empty")
            return (pathlib.Path(bundle_root) / rel).resolve()
        path = pathlib.Path(raw)
        if path.is_absolute():
            return path.resolve()
        return (pathlib.Path(bundle_root) / raw).resolve()

    @staticmethod
    def _safe_ui_shared_target_path(target: str) -> pathlib.Path:
        normalized = str(target or "").strip().replace("\\", "/").strip("/")
        if not normalized:
            raise ValueError("shared UI target is empty")
        path = pathlib.PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe shared UI target path: {target!r}")
        return pathlib.Path(*path.parts)

    def _ui_shared_source_specs(self, *, cfg: Dict[str, Any], bundle_root: str) -> list[Dict[str, Any]]:
        raw_sources = cfg.get("shared_sources") or cfg.get("materialized_sources") or {}
        if not raw_sources:
            return []

        if isinstance(raw_sources, dict):
            items = list(raw_sources.items())
        elif isinstance(raw_sources, list):
            items = [(str(idx), item) for idx, item in enumerate(raw_sources)]
        else:
            raise ValueError("ui shared_sources must be a mapping or list")

        specs: list[Dict[str, Any]] = []
        for key, raw_spec in items:
            name = str(key or "shared").strip() or "shared"
            if isinstance(raw_spec, str):
                source = raw_spec
                target = f"_shared/{name}"
            elif isinstance(raw_spec, dict):
                source = str(
                    raw_spec.get("src_folder")
                    or raw_spec.get("source_dir")
                    or raw_spec.get("source")
                    or ""
                ).strip()
                target = str(raw_spec.get("target") or raw_spec.get("target_dir") or f"_shared/{name}").strip()
            else:
                raise ValueError(f"shared UI source {name!r} must be a path string or mapping")
            if not source:
                raise ValueError(f"shared UI source {name!r} has no source path")
            source_path = self._resolve_ui_shared_source_path(source=source, bundle_root=bundle_root)
            if not source_path.exists():
                raise FileNotFoundError(f"shared UI source {name!r} not found: {source}")
            if not source_path.is_dir():
                raise ValueError(f"shared UI source {name!r} must be a directory: {source_path}")
            specs.append({
                "name": name,
                "source": source,
                "src_path": source_path,
                "target_path": self._safe_ui_shared_target_path(target),
            })
        return specs

    def _compute_ui_build_signature(
        self,
        *,
        kind: str,
        cfg: Dict[str, Any],
    ) -> Optional[str]:
        """
        Compute the deterministic source-tree fingerprint used to decide
        whether a UI app needs rebuilding.

        Returns the same string `_ensure_static_ui_app_build` would compute
        for the same `(kind, cfg)`, or `None` when the build is disabled or
        prerequisites are missing — callers (route signature-aware short-
        circuit, build coordinator) treat `None` as "no opinion" and fall
        back to membership-based behaviour.

        Extracted from `_ensure_static_ui_app_build` so the route layer can
        cheaply compare signatures without invoking the build itself.
        """
        # Expand a config-driven `engine:` selector (chat widget) into the concrete
        # build_command + shared_sources; no-op for every other widget config.
        cfg = apply_chat_widget_engine(cfg)
        src_folder = str(cfg.get("src_folder") or cfg.get("source_dir") or "").strip()
        build_command = str(cfg.get("build_command") or "").strip()
        if not src_folder or not build_command:
            return None

        storage_root = self.bundle_storage_root()
        if not storage_root:
            return None

        bundle_root = self._bundle_root()
        if not bundle_root:
            return None

        src_path = self._resolve_ui_src_path(src_folder=src_folder, bundle_root=bundle_root)
        if not src_path.exists():
            return None

        shared_specs = self._ui_shared_source_specs(cfg=cfg, bundle_root=bundle_root)
        bundle_delivery_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or "")
        source_signature_parts = [f"src:{src_path}:{self._ui_source_signature(src_path)}"]
        for spec in shared_specs:
            source_signature_parts.append(
                f"shared:{spec['name']}:{spec['src_path']}:{spec['target_path']}:{self._ui_source_signature(spec['src_path'])}"
            )
        source_signature = "|".join(source_signature_parts)
        return f"{kind}|{src_path}|{build_command}|{bundle_delivery_id}|{source_signature}"

    def compute_ui_main_view_signature(self) -> Optional[str]:
        """Public source-fingerprint accessor for the main-view UI build.

        Returns `None` when main-view is not configured for build. Used by
        the static-asset route to decide whether the cached build is still
        current without invoking the build coroutine.
        """
        ui_cfg = (self.bundle_props or {}).get("ui") or {}
        main_view = ui_cfg.get("main_view") or {}
        if not isinstance(main_view, dict) or not self._ui_config_enabled(main_view):
            return None
        return self._compute_ui_build_signature(kind="main-view", cfg=main_view)

    def compute_ui_widget_signature(self, alias: str) -> Optional[str]:
        """Public source-fingerprint accessor for a widget UI build.

        Returns `None` when the widget is not configured for build, the
        alias is unknown, or the build is disabled.
        """
        safe_alias = str(alias or "").strip().replace("/", "_")
        if not safe_alias:
            return None
        ui_cfg = (self.bundle_props or {}).get("ui") or {}
        widget_cfgs = ui_cfg.get("widgets") or {}
        if not isinstance(widget_cfgs, dict):
            return None
        cfg = widget_cfgs.get(safe_alias)
        if not isinstance(cfg, dict) or not self._ui_config_enabled(cfg):
            return None
        return self._compute_ui_build_signature(kind=f"widget:{safe_alias}", cfg=cfg)

    async def _ensure_static_ui_app_build(
        self,
        *,
        kind: str,
        cfg: Dict[str, Any],
        build_dest: pathlib.Path,
        signature_path: pathlib.Path,
        operation: str,
    ) -> None:
        import shutil
        import traceback as _tb
        import uuid as _uuid

        from kdcube_ai_app.infra.plugin.bundle_once import run_once_for_shared_bundle_storage

        # Config-driven `engine:` → build_command + shared_sources (chat widget);
        # no-op for other widgets. Mirrors compute_ui_widget_signature so the
        # signature and the build agree on the expanded command.
        cfg = apply_chat_widget_engine(cfg)
        src_folder = str(cfg.get("src_folder") or cfg.get("source_dir") or "").strip()
        build_command = str(cfg.get("build_command") or "").strip()

        if not src_folder or not build_command:
            return

        storage_root = self.bundle_storage_root()
        if not storage_root:
            self.logger.log(f"[bundle.ui] {kind} build skipped: storage_root unavailable", "WARNING")
            return

        bundle_root = self._bundle_root()
        if not bundle_root:
            self.logger.log(f"[bundle.ui] {kind} build skipped: bundle_root unavailable", "WARNING")
            return

        src_path = self._resolve_ui_src_path(src_folder=src_folder, bundle_root=bundle_root)

        if not src_path.exists():
            self.logger.log(f"[bundle.ui] {kind} build skipped: src_folder not found: {src_folder!r}", "WARNING")
            return

        shared_specs = self._ui_shared_source_specs(cfg=cfg, bundle_root=bundle_root)
        build_dest.parent.mkdir(parents=True, exist_ok=True)
        signature_path.parent.mkdir(parents=True, exist_ok=True)

        bundle_delivery_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or "")
        signature = self._compute_ui_build_signature(kind=kind, cfg=cfg)
        if signature is None:
            # Should not happen given the early returns above, but stay defensive
            # in case prerequisites change between the early-return checks and
            # signature computation.
            self.logger.log(f"[bundle.ui] {kind} build skipped: signature unavailable", "WARNING")
            return

        async def _build_ui() -> None:
            tmp_dest = storage_root / f".ui.build.tmp.{os.getpid()}.{_uuid.uuid4().hex}"
            tmp_src = storage_root / f".ui.src.tmp.{os.getpid()}.{_uuid.uuid4().hex}"
            previous_dest = storage_root / f".ui.previous.{os.getpid()}.{_uuid.uuid4().hex}"
            swapped_old = False
            try:
                shutil.rmtree(tmp_dest, ignore_errors=True)
                shutil.rmtree(tmp_src, ignore_errors=True)
                tmp_dest.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_path,
                    tmp_src,
                    ignore=self._ui_copy_ignore_patterns(),
                )
                tmp_src_resolved = tmp_src.resolve()
                for spec in shared_specs:
                    shared_target = (tmp_src / spec["target_path"]).resolve()
                    try:
                        shared_target.relative_to(tmp_src_resolved)
                    except ValueError as exc:
                        raise ValueError(f"shared UI target escapes build workspace: {spec['target_path']}") from exc
                    shutil.rmtree(shared_target, ignore_errors=True)
                    shared_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(
                        spec["src_path"],
                        shared_target,
                        ignore=self._ui_copy_ignore_patterns(),
                    )
                    self.logger.log(
                        f"[bundle.ui] {kind} materialized shared source {spec['name']}: "
                        f"{spec['src_path']} -> {shared_target}",
                        "INFO",
                    )
                final_command = self._prepare_ui_build_command(build_command=build_command, tmp_dest=tmp_dest)
                self.logger.log(
                    f"[bundle.ui] {kind} build start: src={src_path} build_src={tmp_src} dest={build_dest}",
                    "INFO",
                )

                env = os.environ.copy()
                env["OUTDIR"] = str(tmp_dest)
                env["VI_BUILD_DEST_ABSOLUTE_PATH"] = str(tmp_dest)
                env["VITE_BUILD_DEST_ABSOLUTE_PATH"] = str(tmp_dest)
                env["VITE_APP_OUT_DIR"] = str(tmp_dest)
                for env_key in list(env.keys()):
                    if env_key.startswith("npm_"):
                        env.pop(env_key, None)
                if bundle_delivery_id:
                    env["VI_BUNDLE_ID"] = bundle_delivery_id
                    env["VITE_BUNDLE_ID"] = bundle_delivery_id
                nvm_bin = os.path.expanduser("~/.nvm/versions/node")
                if os.path.exists(nvm_bin):
                    for version_dir in sorted(os.listdir(nvm_bin), reverse=True):
                        bin_path = os.path.join(nvm_bin, version_dir, "bin")
                        if os.path.exists(os.path.join(bin_path, "npm")):
                            env["PATH"] = bin_path + ":" + env.get("PATH", "")
                            break
                env["PATH"] = str(tmp_src / "node_modules" / ".bin") + ":" + env.get("PATH", "")
                # Lift inline build-step env (e.g. VITE_CHAT_UI=package) into the
                # subprocess env. The direct-build path runs the package.json script,
                # so inline assignments on `npm run build` would otherwise be lost.
                for _env_key, _env_val in self._build_env_from_command(final_command).items():
                    env[_env_key] = _env_val

                async def _run_build_process(args: Optional[list[str]] = None, shell_command: Optional[str] = None):
                    if args:
                        self.logger.log(f"[bundle.ui] {kind} build command: {' '.join(args)}", "INFO")
                        proc = await asyncio.create_subprocess_exec(
                            *args,
                            cwd=str(tmp_src),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                        )
                    else:
                        self.logger.log(f"[bundle.ui] {kind} build command: {shell_command}", "INFO")
                        proc = await asyncio.create_subprocess_shell(
                            str(shell_command or ""),
                            cwd=str(tmp_src),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                        )
                    try:
                        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=600)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                        raise TimeoutError("UI build timed out after 600s")
                    return (
                        int(proc.returncode or 0),
                        stdout_b.decode("utf-8", errors="replace") if stdout_b else "",
                        stderr_b.decode("utf-8", errors="replace") if stderr_b else "",
                    )

                stdout_parts: list[str] = []
                stderr_parts: list[str] = []
                exit_code = 0
                npm_install_args = self._npm_install_args_for_ui_build(final_command)
                if npm_install_args:
                    exit_code, stdout, stderr = await _run_build_process(args=npm_install_args)
                    stdout_parts.append(stdout)
                    stderr_parts.append(stderr)
                    if exit_code == 0:
                        package_json = tmp_src / "package.json"
                        try:
                            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts") or {}
                            build_script = str(scripts.get("build") or "").strip()
                        except Exception as exc:
                            raise RuntimeError(f"UI build failed: unable to read scripts.build from {package_json}: {exc}") from exc
                        if not build_script:
                            raise RuntimeError(f"UI build failed: scripts.build missing in {package_json}")
                        build_script = self._prepare_ui_build_command(build_command=build_script, tmp_dest=tmp_dest)
                        exit_code, stdout, stderr = await _run_build_process(shell_command=build_script)
                        stdout_parts.append(stdout)
                        stderr_parts.append(stderr)
                else:
                    exit_code, stdout, stderr = await _run_build_process(shell_command=final_command)
                    stdout_parts.append(stdout)
                    stderr_parts.append(stderr)

                stdout = "\n".join(part for part in stdout_parts if part)
                stderr = "\n".join(part for part in stderr_parts if part)
                if exit_code != 0:
                    build_output = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part)
                    raise RuntimeError(f"UI build failed (exit={exit_code}):\n{build_output[-4000:]}")
                if not (tmp_dest / "index.html").exists():
                    raise RuntimeError(f"UI build failed: index.html missing in temp output {tmp_dest}")

                shutil.rmtree(previous_dest, ignore_errors=True)
                if build_dest.exists():
                    build_dest.rename(previous_dest)
                    swapped_old = True
                try:
                    tmp_dest.rename(build_dest)
                except Exception:
                    if swapped_old and previous_dest.exists() and not build_dest.exists():
                        previous_dest.rename(build_dest)
                    raise

                shutil.rmtree(previous_dest, ignore_errors=True)
                self.logger.log(
                    f"[bundle.ui] {kind} build done: dest={build_dest} index_html={(build_dest / 'index.html').exists()}",
                    "INFO",
                )
            finally:
                shutil.rmtree(tmp_dest, ignore_errors=True)
                shutil.rmtree(tmp_src, ignore_errors=True)

        try:
            await run_once_for_shared_bundle_storage(
                storage_root=storage_root,
                operation=operation,
                signature_path=signature_path,
                signature=signature,
                ready=lambda: (build_dest / "index.html").exists(),
                action=_build_ui,
                logger=self.logger,
                owner_metadata={
                    "bundle_id": bundle_delivery_id,
                    "kind": kind,
                    "src": str(src_path),
                    "dest": str(build_dest),
                },
                lock_wait_seconds=max(1, int(os.environ.get("BUNDLE_UI_BUILD_LOCK_WAIT_SECONDS", "600") or "600")),
                lock_ttl_seconds=max(30, int(os.environ.get("BUNDLE_UI_BUILD_LOCK_TTL_SECONDS", "300") or "300")),
                allow_existing_on_timeout=False,
                allow_existing_while_locked=False,
                log_prefix="[bundle.ui]",
            )
        except TimeoutError:
            self.logger.log(f"[bundle.ui] {kind} build failed: timeout after 600s", "ERROR")
            raise
        except Exception:
            self.logger.log(f"[bundle.ui] {kind} build failed:\n{_tb.format_exc()}", "ERROR")
            raise

    def _active_ui_widget_aliases(self, widget_cfgs: Any) -> set[str]:
        aliases: set[str] = set()
        if not isinstance(widget_cfgs, dict):
            return aliases
        for alias, raw_cfg in widget_cfgs.items():
            if not isinstance(raw_cfg, dict) or not self._ui_config_enabled(raw_cfg):
                continue
            safe_alias = str(alias or "").strip().replace("/", "_")
            if safe_alias:
                aliases.add(safe_alias)
        return aliases

    def _cleanup_stale_ui_widget_storage(self, *, storage_root: pathlib.Path, active_aliases: set[str]) -> None:
        import shutil

        widgets_root = storage_root / "ui" / "widgets"
        signatures_root = storage_root / ".ui.widgets"

        if widgets_root.exists():
            try:
                for child in widgets_root.iterdir():
                    if child.name in active_aliases:
                        continue
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        self.logger.log(
                            f"[bundle.ui] stale widget output removed: alias={child.name} path={child}",
                            "INFO",
                        )
                    elif child.is_file():
                        child.unlink(missing_ok=True)
                        self.logger.log(
                            f"[bundle.ui] stale widget file removed: alias={child.name} path={child}",
                            "INFO",
                        )
            except Exception:
                self.logger.log(f"[bundle.ui] stale widget output cleanup failed:\n{traceback.format_exc()}", "WARNING")

        if signatures_root.exists():
            try:
                for sig in signatures_root.iterdir():
                    if not sig.is_file() or not sig.name.endswith(".signature"):
                        continue
                    alias = sig.name[: -len(".signature")]
                    if alias in active_aliases:
                        continue
                    sig.unlink(missing_ok=True)
                    self.logger.log(
                        f"[bundle.ui] stale widget signature removed: alias={alias} path={sig}",
                        "INFO",
                    )
            except Exception:
                self.logger.log(f"[bundle.ui] stale widget signature cleanup failed:\n{traceback.format_exc()}", "WARNING")

    async def _ensure_ui_build(self) -> None:
        """
        Build configured custom UI apps from source folders.

        Supported config:
        - `ui.main_view.src_folder/build_command` -> <bundle_storage_root>/ui
        - `ui.widgets.<alias>.src_folder/build_command`
          -> <bundle_storage_root>/ui/widgets/<alias>
        - optional `shared_sources` on any UI app copies extra source folders
          into the temporary build workspace, for example:
          `shared_sources.memory_widget.src_folder=sdk://context/memory/ui/widget/memories`
          and `shared_sources.memory_widget.target=_shared/memory-widget`.

        Uses a signature that includes source tree metadata to skip rebuilding when nothing changed.
        Bundles can override this method to customise the build behaviour.
        """
        ui_cfg = (self.bundle_props or {}).get("ui") or {}
        main_view = ui_cfg.get("main_view") or {}
        widget_cfgs = ui_cfg.get("widgets") or {}

        storage_root = self.bundle_storage_root()
        if not storage_root:
            self.logger.log("[bundle.ui] build skipped: storage_root unavailable", "WARNING")
            return

        active_widget_aliases = self._active_ui_widget_aliases(widget_cfgs)
        self._cleanup_stale_ui_widget_storage(storage_root=storage_root, active_aliases=active_widget_aliases)

        if not main_view and not widget_cfgs:
            return

        if isinstance(main_view, dict) and self._ui_config_enabled(main_view):
            await self._ensure_static_ui_app_build(
                kind="main-view",
                cfg=main_view,
                build_dest=storage_root / "ui",
                signature_path=storage_root / ".ui.signature",
                operation="ui-main-view",
            )

        if isinstance(widget_cfgs, dict):
            for alias, raw_cfg in sorted(widget_cfgs.items()):
                if not isinstance(raw_cfg, dict) or not self._ui_config_enabled(raw_cfg):
                    continue
                safe_alias = str(alias or "").strip().replace("/", "_")
                if not safe_alias:
                    continue
                await self._ensure_static_ui_app_build(
                    kind=f"widget:{safe_alias}",
                    cfg=raw_cfg,
                    build_dest=storage_root / "ui" / "widgets" / safe_alias,
                    signature_path=storage_root / ".ui.widgets" / f"{safe_alias}.signature",
                    operation=f"ui-widget-{safe_alias}",
                )

    def bundle_storage_root(self) -> Optional[pathlib.Path]:
        """
        Resolve the shared storage root for this bundle (if configured).
        Uses tenant/project if available so storage is isolated per tenant/project.
        """
        try:
            from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
            tenant = getattr(getattr(self.comm_context, "actor", None), "tenant_id", None)
            project = getattr(getattr(self.comm_context, "actor", None), "project_id", None)
            return storage_for_spec(
                spec=getattr(self.config, "ai_bundle_spec", None),
                tenant=tenant,
                project=project,
                ensure=True,
            )
        except Exception:
            return None

    def _bundle_runtime_scope(self) -> tuple[str, str, str]:
        bundle_spec = getattr(self.config, "ai_bundle_spec", None)
        bundle_id = str(getattr(bundle_spec, "id", None) or self.BUNDLE_ID or "").strip()
        tenant = str(getattr(getattr(self.comm_context, "actor", None), "tenant_id", None) or "").strip()
        project = str(getattr(getattr(self.comm_context, "actor", None), "project_id", None) or "").strip()
        return bundle_id, tenant, project

    def get_local_sidecar(self, name: str) -> Optional[LocalSidecarHandle]:
        bundle_id, tenant, project = self._bundle_runtime_scope()
        if not bundle_id:
            return None
        return get_runtime_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
        )

    def ensure_local_sidecar(
        self,
        *,
        name: str,
        command: list[str] | tuple[str, ...],
        cwd: str | os.PathLike[str] | None = None,
        env: Optional[Dict[str, Any]] = None,
        host: str = "127.0.0.1",
        port: Optional[int] = 0,
        ready_path: Optional[str] = None,
        ready_timeout_sec: float = 30.0,
        startup_fingerprint: Optional[str] = None,
        runtime_metadata: Optional[Dict[str, Any]] = None,
    ) -> LocalSidecarHandle:
        """
        Ensure a process-local sidecar service is running for this bundle scope.

        The sidecar is shared only within the current proc worker for the
        bundle/tenant/project triple and is terminated automatically during proc
        lifespan shutdown.
        """
        bundle_id, tenant, project = self._bundle_runtime_scope()
        if not bundle_id:
            raise RuntimeError("Bundle id is unavailable for local sidecar startup")

        bundle_root = self._bundle_root()
        storage_root = self.bundle_storage_root()
        merged_env: Dict[str, Any] = {
            "KDCUBE_BUNDLE_ID": bundle_id,
            "KDCUBE_TENANT": tenant,
            "KDCUBE_PROJECT": project,
        }
        if bundle_root:
            merged_env["KDCUBE_BUNDLE_ROOT"] = bundle_root
        if storage_root:
            merged_env["KDCUBE_BUNDLE_STORAGE_ROOT"] = str(storage_root)
        if env:
            merged_env.update(env)

        effective_cwd = cwd or bundle_root
        return ensure_runtime_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            command=list(command),
            cwd=effective_cwd,
            env={str(k): str(v) for k, v in merged_env.items() if v is not None},
            host=host,
            port=port,
            ready_path=ready_path,
            ready_timeout_sec=ready_timeout_sec,
            startup_fingerprint=startup_fingerprint,
            runtime_metadata=runtime_metadata,
        )

    def stop_local_sidecar(self, name: str) -> None:
        bundle_id, tenant, project = self._bundle_runtime_scope()
        if not bundle_id:
            return
        stop_runtime_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
        )

    def update_local_sidecar_runtime_metadata(
        self,
        name: str,
        *,
        runtime_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[LocalSidecarHandle]:
        bundle_id, tenant, project = self._bundle_runtime_scope()
        if not bundle_id:
            return None
        return update_runtime_local_sidecar_runtime_metadata(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            runtime_metadata=runtime_metadata or {},
        )

    @staticmethod
    def get_prop_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
        if not path:
            return default
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def bundle_prop(self, path: str, default: Any = None) -> Any:
        return self.get_prop_path(self.bundle_props or {}, path, default)

    def _deep_merge_props(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(base or {})
        for key, value in (patch or {}).items():
            base_value = merged.get(key)
            if isinstance(base_value, dict) and isinstance(value, dict):
                merged[key] = self._deep_merge_props(base_value, value)
            else:
                merged[key] = value
        return merged

    def _apply_bundle_props_overrides(self) -> None:
        """
        Apply runtime overrides from bundle props (Redis / admin UI / bundles.yaml).
        These are evaluated after refresh_bundle_props() and may override
        configuration-based defaults for this bundle instance.
        """
        props = self.bundle_props or {}

        role_models = self.get_prop_path(props, "role_models")
        changed = False
        if isinstance(role_models, dict) and role_models:
            self.config.set_role_models({**(self.config.role_models or {}), **role_models})
            changed = True

        embedding = self.get_prop_path(props, "embedding")
        if isinstance(embedding, dict) and embedding:
            self.config.set_embedding(embedding)
            changed = True

        if changed and hasattr(self, "models_service"):
            self._rebuild_models_service()

    def _sync_runtime_ctx_bundle_props(self) -> None:
        runtime_ctx = getattr(self, "runtime_ctx", None)
        if runtime_ctx is None:
            return
        raw = self.get_prop_path(self.bundle_props or {}, "execution.runtime", default=None)
        if raw is None:
            raw = self.get_prop_path(self.bundle_props or {}, "exec_runtime")
        runtime_ctx.bundle_props = copy.deepcopy(self.bundle_props or {})
        runtime_ctx.exec_runtime = copy.deepcopy(normalize_exec_runtime_config(raw))

    async def refresh_bundle_props(
        self,
        *,
        state: Dict[str, Any],
        notify: bool = True,
        reason: str = "refresh_bundle_props",
        updated_by: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        previous_props = copy.deepcopy(self.bundle_props or {})
        defaults = dict(self.bundle_props_defaults or {})
        if not self.kv_cache and not self.redis:
            self.bundle_props = defaults
            self._apply_bundle_props_overrides()
            self._sync_runtime_ctx_bundle_props()
            if notify and previous_props != self.bundle_props:
                await self.on_props_changed(
                    previous_props=previous_props,
                    current_props=copy.deepcopy(self.bundle_props),
                    reason=reason,
                    tenant=state.get("tenant"),
                    project=state.get("project"),
                    updated_by=updated_by,
                    source=source,
                )
            return self.bundle_props

        tenant = state.get("tenant")
        project = state.get("project")
        if not tenant or not project:
            self.bundle_props = defaults
            self._sync_runtime_ctx_bundle_props()
            if notify and previous_props != self.bundle_props:
                await self.on_props_changed(
                    previous_props=previous_props,
                    current_props=copy.deepcopy(self.bundle_props),
                    reason=reason,
                    tenant=tenant,
                    project=project,
                    updated_by=updated_by,
                    source=source,
                )
            return self.bundle_props

        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None)
        if not bundle_id:
            self.bundle_props = defaults
            self._sync_runtime_ctx_bundle_props()
            if notify and previous_props != self.bundle_props:
                await self.on_props_changed(
                    previous_props=previous_props,
                    current_props=copy.deepcopy(self.bundle_props),
                    reason=reason,
                    tenant=tenant,
                    project=project,
                    updated_by=updated_by,
                    source=source,
                )
            return self.bundle_props

        from kdcube_ai_app.infra import namespaces

        key = namespaces.CONFIG.BUNDLES.PROPS_KEY_FMT.format(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        overrides: Dict[str, Any] = {}
        if self.redis is not None:
            from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props as _get_bundle_props
            overrides = await _get_bundle_props(
                self.redis,
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
            )
        if not overrides and self.kv_cache:
            overrides = await self.kv_cache.get_json(key) or {}
        if overrides:
            defaults = self._deep_merge_props(defaults, overrides)

        self.bundle_props = defaults
        self._apply_bundle_props_overrides()
        self._sync_runtime_ctx_bundle_props()
        if notify and previous_props != self.bundle_props:
            await self.on_props_changed(
                previous_props=previous_props,
                current_props=copy.deepcopy(self.bundle_props),
                reason=reason,
                tenant=tenant,
                project=project,
                updated_by=updated_by,
                source=source,
            )
        return self.bundle_props

    @property
    def comm(self) -> ChatCommunicator:
        comm_cv = getattr(self, "_comm_cv", None)
        bound_comm = comm_cv.get() if comm_cv is not None else _REQUEST_LOCAL_UNSET
        if bound_comm is not _REQUEST_LOCAL_UNSET:
            if bound_comm is not None:
                return bound_comm
        elif getattr(self, "_comm", None):
            return self._comm

        current_comm_context = self.comm_context
        if not current_comm_context:
            raise RuntimeError("Workflow cannot build communicator: task missing")
        built = build_comm_from_comm_context(
            current_comm_context,
            relay=build_relay_from_env(),
            event_filter=self._event_filter,
        )
        if bound_comm is not _REQUEST_LOCAL_UNSET:
            if comm_cv is not None:
                comm_cv.set(built)
        else:
            self._comm = built
        return built

    @classmethod
    def project_app_state(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        out = {"context": {"bundle": cls.BUNDLE_ID}}
        for k in APP_STATE_KEYS:
            out[k] = json_safe(state.get(k))
        return out

    @staticmethod
    def user_type_from_comm_ctx(comm: ChatCommunicator) -> str:
        user_obj = (comm.service or {}).get("user_obj") or {}
        return user_obj.get("user_type") or "anonymous"

    def _current_agent_id(self) -> str:
        try:
            runtime_agent_id = getattr(getattr(self, "runtime_ctx", None), "agent_id", None)
            comm_agent_id = getattr(getattr(getattr(self, "comm_context", None), "event", None), "agent_id", None)
            service_agent_id = None
            try:
                service_agent_id = (getattr(self, "comm", None).service or {}).get("agent_id")
            except Exception:
                service_agent_id = None
            return normalize_agent_id(
                runtime_agent_id or comm_agent_id or service_agent_id,
                default=DEFAULT_REACT_AGENT_ID,
            )
        except Exception:
            return DEFAULT_REACT_AGENT_ID

    @staticmethod
    def create_initial_state(payload: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = normalize_agent_id(payload.get("agent_id"), default=DEFAULT_REACT_AGENT_ID)
        return {
            "request_id": payload.get("request_id") or _mid("req"),
            "tenant": payload.get("tenant"),
            "project": payload.get("project"),
            "user": payload.get("user"),
            "user_type": payload.get("user_type"),
            "identity_authority": dict(payload.get("identity_authority") or {}) if isinstance(payload.get("identity_authority"), Mapping) else {},
            "agent_id": agent_id,
            "session_id": payload.get("session_id"),
            "conversation_id": payload.get("conversation_id"),
            "external_events": payload.get("external_events") or [],
            "step_logs": [],
            "start_time": time.time(),
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self._app_state = dict(state or {})
        self._turn_id = self._app_state.get("turn_id")

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        raise NotImplementedError("execute_core() must be implemented by subclasses")

    def _events_record_config(self, section: str) -> dict:
        """Resolve events.record.<section> config: field-level merge of assembly defaults and bundle props.

        Assembly sets the platform baseline. Bundle props override individual fields
        (enabled, selector) without replacing the entire section. selector is replaced
        as a whole when present in bundle props — lists are not concatenated.
        """
        from kdcube_ai_app.apps.chat.sdk.config_scopes import _load_assembly_plain
        assembly = _load_assembly_plain(f"events.record.{section}") or {}
        bundle = self.get_prop_path(self.bundle_props or {}, f"events.record.{section}") or {}
        if not bundle:
            return assembly
        return {**assembly, **bundle}

    def _persist_events_config(self) -> dict:
        return self._events_record_config("persist")

    def _telemetry_events_config(self) -> dict:
        return self._events_record_config("telemetry")

    def _persist_event_types(self) -> list[str]:
        cfg = self._persist_events_config()
        selector = cfg.get("selector")
        if isinstance(selector, list) and selector:
            return [str(t) for t in selector if t]
        return list(self._PERSIST_EVENTS_DEFAULT)

    def _persist_events_enabled(self) -> bool:
        cfg = self._persist_events_config()
        v = cfg.get("enabled")
        if v is None:
            return self._PERSIST_EVENTS_ENABLED_DEFAULT
        return bool(v)

    def _telemetry_event_types(self) -> list[str]:
        from kdcube_ai_app.apps.chat.sdk.comm.sink.telemetry import STATS_COMM_EVENT_TYPES
        cfg = self._telemetry_events_config()
        selector = cfg.get("selector")
        if isinstance(selector, list) and selector:
            return [str(t) for t in selector if t]
        return list(STATS_COMM_EVENT_TYPES)

    def _telemetry_events_enabled(self) -> bool:
        cfg = self._telemetry_events_config()
        v = cfg.get("enabled")
        if v is None:
            return self._TELEMETRY_EVENTS_ENABLED_DEFAULT
        return bool(v)

    def _build_telemetry_selector(self) -> dict:
        """Build a comm recording selector from events.record.telemetry config."""
        from kdcube_ai_app.apps.chat.sdk.comm.sink.telemetry import STATS_COMM_DATA_KEYS
        return {
            "include": {"types": self._telemetry_event_types()},
            "privacy": {"data_keys": STATS_COMM_DATA_KEYS},
        }

    def _start_persist_events_recording(self) -> None:
        if not self._persist_events_enabled():
            return
        step_types = self._persist_event_types()
        if not step_types:
            return
        from kdcube_ai_app.apps.chat.sdk.comm.sink.telemetry import STATS_COMM_DATA_KEYS
        self.comm.record(
            {
                "include": {"types": step_types},
                "privacy": {"data_keys": STATS_COMM_DATA_KEYS + ["elapsed_ms"]},
            },
            scope={"owner": "persist_events"},
        )

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
        self._start_persist_events_recording()

    async def post_run_hook(self, *, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        return None

    async def on_turn_completed(
        self,
        *,
        state: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        error: Optional[BaseException] = None,
        status: str = "completed",
        reason: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Optional per-turn finalization hook.

        The proc runner calls this after the bundle handler exits, errors, or is
        cancelled. Keep implementations fast and idempotent; platform cleanup
        such as browser-session cleanup also runs independently.
        """
        return None

    async def report_turn_error(
        self,
        *,
        state: Dict[str, Any],
        exc: Exception,
        title: str = "Turn Error",
        step: str = "turn",
        agent: str = "turn.error",
        final_answer: Optional[str] = "An error occurred.",
    ) -> None:
        """
        Emit a user-visible error envelope and preserve turn-level error state.

        This is intended for bundle-local failures so the client receives a
        proper `chat.error` event instead of only a diagnostic step.
        """
        if isinstance(exc, EconomicsLimitException):
            raise exc

        message = str(exc)
        traceback_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        self.logger.log(traceback_text, "ERROR")

        state["error_message"] = message
        if final_answer and not state.get("final_answer"):
            state["final_answer"] = final_answer

        payload = {
            "error": message,
            "error_message": message,
            "error_type": type(exc).__name__,
        }

        error_emitted = False
        try:
            await self.comm.error(
                message=message,
                data=payload,
                agent=agent,
                step=step,
                title=title,
            )
            error_emitted = True
        except Exception as emit_exc:
            emit_traceback = "".join(
                traceback.format_exception(
                    type(emit_exc), emit_exc, emit_exc.__traceback__
                )
            )
            self.logger.log(
                f"Failed to emit chat.error for bundle failure:\n{emit_traceback}",
                "ERROR",
            )

        if not error_emitted:
            try:
                await self.comm.step(
                    step=step,
                    status="error",
                    title=title,
                    data=payload,
                    markdown=f"**Error:** {message}",
                )
            except Exception as emit_exc:
                emit_traceback = "".join(
                    traceback.format_exception(
                        type(emit_exc), emit_exc, emit_exc.__traceback__
                    )
                )
                self.logger.log(
                    f"Failed to emit diagnostic chat.step for bundle failure:\n{emit_traceback}",
                    "ERROR",
                )

    @on_reactive_event
    async def run(self, **params) -> Dict[str, Any]:
        state = dict(getattr(self, "_app_state", {}) or {})
        self._turn_id = self._turn_id or _mid("turn")
        state["turn_id"] = self._turn_id
        if "external_events" in params:
            state["external_events"] = params.get("external_events") or []

        tenant = state.get("tenant")
        project = state.get("project")
        user_id = state.get("user") or state.get("fingerprint")
        user_type = state.get("user_type") or "anonymous"
        thread_id = state.get("conversation_id") or state.get("session_id") or "default"
        turn_id = state.get("turn_id")
        agent_id = normalize_agent_id(
            state.get("agent_id")
            or getattr(getattr(getattr(self, "comm_context", None), "event", None), "agent_id", None),
            default=DEFAULT_REACT_AGENT_ID,
        )
        state["agent_id"] = agent_id
        bundle_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or getattr(self, "BUNDLE_ID", "") or "")
        request_id = (
            state.get("request_id")
            or getattr(getattr(getattr(self, "comm_context", None), "request", None), "request_id", None)
            or _mid("req")
        )
        timezone = (
            state.get("timezone")
            or getattr(getattr(getattr(self, "comm_context", None), "user", None), "timezone", None)
        )
        tenant = tenant or getattr(self.config, "tenant", None) or self.settings.TENANT
        project = project or getattr(self.config, "project", None) or self.settings.PROJECT

        from kdcube_ai_app.infra.accounting import AccountingSystem, _get_storage, with_accounting

        storage = _get_storage()
        if storage is None or storage.__class__.__name__ == "NoOpAccountingStorage":
            AccountingSystem.init_storage(create_storage_backend(get_settings().STORAGE_PATH), enabled=True)

        async with with_accounting(
            bundle_id or "chat.orchestrator",
            user_id=user_id,
            session_id=state.get("session_id") or thread_id,
            user_type=user_type,
            tenant_id=tenant,
            project_id=project,
            request_id=request_id,
            app_bundle_id=bundle_id,
            agent_id=agent_id,
            timezone=timezone,
            conversation_id=thread_id,
            turn_id=turn_id,
            metadata={
                "conversation_id": thread_id,
                "turn_id": turn_id,
                "bundle_id": bundle_id,
                "agent_id": agent_id,
                "entrypoint": self.__class__.__name__,
            },
        ):
            await self.refresh_bundle_props(state=state)
            await self.pre_run_hook(state=state)

            result = await self.execute_core(state=state, thread_id=thread_id, params=params)
            result = result or {}

            usage_from = datetime.utcnow().date().isoformat()
            await self.run_accounting(
                tenant=tenant,
                project=project,
                user_id=user_id,
                user_type=user_type,
                thread_id=thread_id,
                turn_id=turn_id,
                usage_from=usage_from,
            )

            await self.post_run_hook(state=state, result=result)
            return self.project_app_state(result)

    def _bundle_root(self) -> Optional[str]:
        spec = getattr(self.config, "ai_bundle_spec", None)
        module = getattr(spec, "module", None)
        path = getattr(spec, "path", None)
        if spec and module and path:
            from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle_root
            return str(resolve_bundle_root(path, module))
        return None

    def _ensure_privileged(self, *, user_id: Optional[str], feature: str) -> Optional[str]:
        user_type = self.user_type_from_comm_ctx(self.comm)
        if user_type not in ("privileged",):
            self.logger.log(
                f"[{feature}]. User {user_id} with type [{user_type}] has no permission to access {feature}",
                "WARN",
            )
            return None
        return user_type

    def _render_dashboard_html(
        self,
        *,
        content: str,
        title: str,
    ) -> str:
        transpiler = ClientSideTSXTranspiler()
        return transpiler.tsx_to_html(content, title=title)

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:currency-dollar",
            "lucide": "CircleDollarSign",
        },
        alias="opex",
        user_types=("privileged",),
    )
    def opex(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="opex")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[opex]. Generating Opex Report for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No opex available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.opex")
                fallback_path = Path(cp_mod.__file__).parent / "OpexDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="OPEX")
                return [html]
            except Exception:
                self.logger.log(f"Error loading opex by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:currency-dollar",
            "lucide": "CircleDollarSign",
        },
        alias="control_plane",
        user_types=("privileged",),
    )
    def control_plane(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="control_plane")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[control_plane]. Generating Control Plane Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No control plane interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "EconomicsDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Control Plane")
                return [html]
            except Exception:
                self.logger.log(f"Error loading control_plane by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-left-right",
            "lucide": "MessageSquareMore",
        },
        alias="conversation_browser",
        user_types=("privileged",),
    )
    def conversation_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="conversation_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(
            f"[conversation_browser]. Generating Conversation Browser Admin Dashboard for user {user_id} ({user_type})"
        )

        bundle_root = self._bundle_root()
        default_content = "<p>No conversation browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "ConversationBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Control Plane")
                return [html]
            except Exception:
                self.logger.log(
                    f"Error loading conversation browser by user {user_id}: {traceback.format_exc()}",
                    "ERROR",
                )
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:arrows-right-left",
            "lucide": "ArrowLeftRight",
        },
        alias="svc_gateway",
        user_types=("privileged",),
    )
    def svc_gateway(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="svc_gateway")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[svc_gateway]. Generating Gateway Monitoring Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No gateway monitoring interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                monitoring_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.monitoring")
                fallback_path = Path(monitoring_mod.__file__).parent / "ControlPlaneMonitoringDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Gateway Monitoring")
                return [html]
            except Exception:
                self.logger.log(f"Error loading svc_gateway by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:circle-stack",
            "lucide": "Database",
        },
        alias="redis_browser",
        user_types=("privileged",),
    )
    def redis_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="redis_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[redis_browser]. Generating Redis Browser Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No redis browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "RedisBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Redis Browser")
                return [html]
            except Exception:
                self.logger.log(f"Error loading redis browser by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:cpu-chip",
            "lucide": "Bot",
        },
        alias="ai_bundles",
        user_types=("privileged",),
    )
    def ai_bundles(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="ai_bundles")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[ai_bundles]. Generating AI Bundles Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No AI bundles dashboard available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        try:
            integrations_mod = importlib.import_module("kdcube_ai_app.apps.chat.proc.rest.integrations")
            fallback_path = Path(integrations_mod.__file__).parent / "AIBundleDashboard.tsx"
            content = fallback_path.read_text(encoding="utf-8")

            output_content = patch_dashboard(
                input_content=content,
                base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                access_token=None,
                default_tenant=self.settings.TENANT,
                default_project=self.settings.PROJECT,
                default_app_bundle_id=self.config.ai_bundle_spec.id,
                host_bundles_path=get_settings().HOST_BUNDLES_PATH,
                bundles_root=get_settings().PLATFORM.APPLICATIONS.BUNDLES_ROOT,
            )
            html = self._render_dashboard_html(content=output_content, title="AI Bundles")
            return [html]
        except Exception:
            self.logger.log(f"Error loading ai_bundles by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=())
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:credit-card",
            "lucide": "CreditCard",
        },
        alias="economic_usage",
        user_types=(),
    )
    def economic_usage(self, user_id: Optional[str] = None, **kwargs):
        user_type = self.user_type_from_comm_ctx(self.comm)
        if user_type == "anonymous":
            return ["<p>No permission. Please log in.</p>"]

        self.logger.log(f"[economic_usage]. Generating User Billing Dashboard for user {user_id} ({user_type})")

        default_content = "<p>No user billing interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        try:
            economics_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.economics")
            fallback_path = Path(economics_mod.__file__).parent / "UserBillingDashboard.tsx"
            content = fallback_path.read_text(encoding="utf-8")

            output_content = patch_dashboard(
                input_content=content,
                base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                access_token=None,
                default_tenant=self.settings.TENANT,
                default_project=self.settings.PROJECT,
                default_app_bundle_id=self.config.ai_bundle_spec.id,
            )
            html = self._render_dashboard_html(content=output_content, title="Billing & Plans")
            return [html]
        except Exception:
            self.logger.log(f"Error loading economic_usage by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    def configuration_defaults(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        sonnet_46 = "claude-sonnet-4-6"
        haiku_3 = "claude-3-5-haiku-20241022"
        haiku_4 = "claude-haiku-4-5-20251001"


        return {
            "role_models": {
                "ctx.reconciler":  {"provider": "anthropic", "model": haiku_4},
                "turn.summary": {"provider": "anthropic", "model": haiku_4},
                "attachment.summary": {"provider": "anthropic", "model": haiku_4},
                "format_fixer": {"provider": "anthropic", "model": haiku_3},

                "solver.tool_router": {"provider": "anthropic", "model": haiku_4},
                "solver.solvability": {"provider": "anthropic", "model": haiku_4},
                "solver.codegen": {"provider": "anthropic", "model": sonnet_45},
                "solver.coordinator": {"provider": "anthropic", "model": sonnet_45},
                "solver.unified-planner": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision.strong": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision.regular": {"provider": "anthropic", "model": haiku_4},
                "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_46}, # Solver — hard reasoning
                "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},  # Solver — routine steps
                "solver.react.summary": {"provider": "anthropic", "model": haiku_4},
                "context.compaction.summary": {"provider": "anthropic", "model": sonnet_46},
                "context.compaction.turn_prefix": {"provider": "anthropic", "model": sonnet_46},

                "tool.generator": {"provider": "anthropic", "model": sonnet_45},
                "tool.generator.strong": {"provider": "anthropic", "model": sonnet_45},
                "tool.generator.regular": {"provider": "anthropic", "model": haiku_4},

                "tool.source.reconciler": {"provider": "anthropic", "model": haiku_4},
                "tool.sources.filter.by.content": {"provider": "anthropic", "model": haiku_4},
                "tool.sources.filter.by.content.and.segment": {"provider": "anthropic", "model": haiku_4},
            },
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
            },
        }

    @property
    def configuration(self) -> Dict[str, Any]:
        """
        Effective configuration = defaults (base + subclass) deep-merged with
        external bundle props overrides.
        """
        base = self.configuration_defaults() or {}
        overrides = self.bundle_props or {}
        return self._deep_merge_props(base, overrides)
    async def apply_accounting(
        self,
        tenant: str,
        project: str,
        user_id: str,
        user_type: str,
        thread_id: str,
        turn_id: str,
        usage_from: str,
        emit_turn_event: bool = True,
    ):
        """Calculate and report turn costs using calculator."""
        settings = get_settings()
        kdcube_path = settings.STORAGE_PATH
        backend = create_storage_backend(kdcube_path)
        calc = RateCalculator(backend, base_path="accounting")
        bundle_id = self.config.ai_bundle_spec.id

        from datetime import datetime
        try:
            date_to = datetime.utcnow().date().isoformat()
        except Exception:
            date_to = usage_from

        self.logger.log(
            f"[apply_accounting]. tenant={tenant};project={project};user_id={user_id};"
            f"conversation_id={thread_id};turn_id={turn_id};usage_from={usage_from};"
            f"date_to={date_to};bundle_id={bundle_id};"
        )
        ref_provider = "anthropic"
        ref_model = "claude-sonnet-4-5-20250929"

        result = await calc.calculate_turn_costs(
            tenant_id=tenant,
            project_id=project,
            conversation_id=thread_id,
            turn_id=turn_id,
            app_bundle_id=bundle_id,
            date_from=usage_from,
            date_to=date_to,
            service_types=["llm", "embedding", "web_search"],
            use_memory_cache=True,
            ref_provider=ref_provider,
            ref_model=ref_model,
        )

        cost_total_usd = result["cost_total_usd"]
        cost_breakdown = result["cost_breakdown"]
        agent_costs = result["agent_costs"]
        token_summary = result["token_summary"]

        weighted_tokens = token_summary["weighted_tokens"]
        ranked_tokens = (
            token_summary.get("billable_equivalent_tokens")
            or token_summary.get("llm_equivalent_tokens")
            or token_summary["weighted_tokens"]
        )
        agent_id = self._current_agent_id()

        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Token breakdown - Uncached: {token_summary['llm_input_sum']}, "
            f"Cache write: {token_summary['llm_cache_creation_sum']}, "
            f"Cache read: {token_summary['llm_cache_read_sum']}, "
            f"Output: {token_summary['llm_output_sum']}, "
            f"Total input: {token_summary['total_input_tokens']}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Weighted tokens (LLM only): {weighted_tokens}; Billable equivalent tokens: {ranked_tokens}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Estimated spend (with cache): {cost_total_usd:.6f} USD; "
            f"breakdown: {json.dumps(cost_breakdown, ensure_ascii=False)}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Cost by agent: {json.dumps({k: v['total_cost_usd'] for k, v in agent_costs.items()}, ensure_ascii=False)}"
        )

        cost_markdown = _format_cost_table_markdown(
            cost_breakdown=cost_breakdown,
            total_cost=cost_total_usd,
            show_detailed=True,
        )
        agent_markdown = _format_agent_breakdown_markdown(agent_costs, cost_total_usd)
        full_markdown = cost_markdown + "\n\n" + agent_markdown

        compact_summary = _format_cost_summary_compact(
            cost_breakdown=cost_breakdown,
            total_cost=cost_total_usd,
            weighted_tokens=weighted_tokens,
            total_input_tokens=token_summary["total_input_tokens"],
            llm_output_sum=token_summary["llm_output_sum"],
        )

        if emit_turn_event:
            await self.comm.event(
                agent=agent_id,
                type="accounting.usage",
                title=f"💰 Turn Cost: ${cost_total_usd:.6f}",
                step="accounting",
                data={
                    "breakdown": cost_breakdown,
                    "cost_total_usd": cost_total_usd,
                    "weighted_tokens": weighted_tokens,
                    "ranked_tokens": ranked_tokens,
                    "total_input_tokens": token_summary["total_input_tokens"],
                    "llm_output_sum": token_summary["llm_output_sum"],
                    "summary": compact_summary,
                    "agent_costs": agent_costs,
                    "metadata": {
                        "agent_id": agent_id,
                        "bundle_id": bundle_id,
                        "conversation_id": thread_id,
                        "turn_id": turn_id,
                    },
                },
                markdown=full_markdown,
                status="completed",
            )

        await self.comm.service_event(
            type="accounting.usage",
            step="accounting",
            status="completed",
            title=f"💰 Turn Cost: ${cost_total_usd:.6f}",
            data={
                "breakdown": cost_breakdown,
                "cost_total_usd": cost_total_usd,
                "weighted_tokens": weighted_tokens,
                "ranked_tokens": ranked_tokens,
                "total_input_tokens": token_summary["total_input_tokens"],
                "llm_output_sum": token_summary["llm_output_sum"],
                "summary": compact_summary,
                "agent_costs": agent_costs,
                "metadata": {
                    "agent_id": agent_id,
                    "bundle_id": bundle_id,
                    "conversation_id": thread_id,
                    "turn_id": turn_id,
                },
            },
            agent=agent_id,
            markdown=full_markdown,
            broadcast=True,
        )

        return ranked_tokens, result

    async def run_accounting(
        self,
        tenant: str,
        project: str,
        user_id: str,
        user_type: str,
        thread_id: str,
        turn_id: str,
        usage_from: str,
        emit_turn_event: bool = True,
    ):
        return await self.apply_accounting(
            tenant=tenant,
            project=project,
            user_id=user_id,
            user_type=user_type,
            thread_id=thread_id,
            turn_id=turn_id,
            usage_from=usage_from,
            emit_turn_event=emit_turn_event,
        )

    # ---------- Optional SDK services ----------

    async def get_conv_index(self):
        if self._conv_idx is not None:
            return self._conv_idx
        if not self.pg_pool:
            return None
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

        self._conv_idx = ConvIndex(pool=self.pg_pool)
        await self._conv_idx.init()
        return self._conv_idx

    async def get_kb_client(self):
        if self._kb is not None:
            return self._kb
        if not self.pg_pool:
            return None
        from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient

        self._kb = KBClient(pool=self.pg_pool)
        await self._kb.init()
        return self._kb

    async def get_ctx_client(self):
        if self.ctx_client is not None:
            return self.ctx_client
        conv_idx = await self.get_conv_index()
        if conv_idx is None:
            return None
        if self._store is None:
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            self._store = ConversationStore(self.settings.STORAGE_PATH)
        from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

        self.ctx_client = ContextRAGClient(
            conv_idx=conv_idx,
            store=self._store,
            model_service=self.models_service,
        )
        return self.ctx_client

    async def _save_events_artifact(self, *, state: Dict[str, Any]) -> None:
        try:
            if not self._persist_events_enabled():
                return
            step_types = self._persist_event_types()
            if not step_types:
                return
            ctx_client = await self.get_ctx_client()
            if ctx_client is None:
                return
            tenant = state.get("tenant") or getattr(self.config, "tenant", None) or self.settings.TENANT
            project = state.get("project") or getattr(self.config, "project", None) or self.settings.PROJECT
            user_id = state.get("user") or state.get("fingerprint") or ""
            user_type = state.get("user_type") or "anonymous"
            conversation_id = state.get("conversation_id") or state.get("session_id") or ""
            turn_id = state.get("turn_id") or getattr(self, "_turn_id", None) or ""
            bundle_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or "")
            if not (tenant and project and user_id and conversation_id and turn_id):
                return
            raw_items = self.comm.export_recorded_events({"include": {"types": step_types}})
            # accounting.usage is emitted on both comm.event() (chat_step) and
            # comm.service_event() (chat_service). Drop the chat_service copy to
            # avoid duplicates while keeping all other chat_service events intact
            # (e.g. react.tool.call is only emitted via service_event).
            step_items = [
                item for item in raw_items
                if not (
                    item.get("type") == "accounting.usage"
                    and item.get("route_key") == "chat_service"
                )
            ]
            if not step_items:
                return
            agent_id = index_agent_id(
                getattr(getattr(self, "runtime_ctx", None), "agent_id", None)
                or state.get("agent_id")
            )
            await ctx_client.save_artifact(
                kind="conv.artifacts.events",
                tenant=tenant, project=project,
                turn_id=turn_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
                user_type=user_type,
                content={"version": "v1", "items": step_items},
                extra_tags=["conversation", "events"],
            )
        except Exception:
            self.logger.log(traceback.format_exc(), "WARNING")

    async def close_optional_services(self) -> None:
        if self._kb:
            try:
                await self._kb.close()
            except Exception:
                pass
        if self._conv_idx:
            try:
                await self._conv_idx.close()
            except Exception:
                pass
        self._store = None

        self._kb = None
        self._conv_idx = None
