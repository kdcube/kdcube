"""Reusable chat solution mount helpers.

Bundles can mount the shared chat widget with ``chat_widget_ui_config()`` and
provide their own event-source ids through Vite environment variables. The
widget defaults are intentionally generic; bundle-specific names belong in the
consumer bundle config.
"""

from __future__ import annotations

import shlex
from typing import Any, Mapping


CHAT_WIDGET_SDK_SOURCE = "sdk://solutions/chat/ui/widget"
DEFAULT_CHAT_WIDGET_BUILD_COMMAND = (
    "npm install --no-package-lock && "
    "OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
)

# The framework-agnostic engine lives in the standalone ``@kdcube/components-*``
# packages, which ship inside the app tree at ``…/npm/packages`` and resolve via the
# ``npm://`` shared-source scheme. They are materialized next to the widget at build
# time and the widget's ``vite.config`` aliases ``@kdcube/*`` onto them.
DEFAULT_CHAT_WIDGET_SHARED_SOURCES = {
    "components_core": "npm://components-core/src",
    "components_react": "npm://components-react/src",
}

# Engine selection is ONE knob. ``engine="package"`` flips both halves together:
#   - sets ``VITE_CHAT_ENGINE=package`` so the widget *runs* the package engine, and
#   - declares the ``npm://`` ``shared_sources`` so the package source is materialized.
# ``engine="local"`` (the default) does NEITHER — no ``npm://`` dependency at all — so
# a bundle mounting the chat widget the default way builds on any image, even one
# whose ``/app/npm`` is absent (e.g. before the next image rebuild). The npm:// path
# is only exercised when you explicitly opt in, which is the same moment you rebuild.
# The chat widget is package-only now (the in-tree App/engine were removed), so the
# build always materializes the @kdcube/components-* sources. "local" no longer
# builds; the default is the package UI.
DEFAULT_CHAT_WIDGET_ENGINE = "package-ui"


def _with_chat_vite_env(command: str, env: Mapping[str, Any]) -> str:
    """Scope VITE_* env to the BUILD step.

    A leading ``VAR=x npm install && … npm run build`` sets ``VAR`` only for
    ``npm install`` — vite (``npm run build``) never sees it. So inject the env
    inline right before ``npm run build`` (the same position ``OUTDIR`` uses, which
    is why output dir works but the engine switch silently didn't). Falls back to a
    whole-command prefix if there's no recognizable build step.
    """
    prefix = " ".join(
        f"{key}={shlex.quote(str(value))}"
        for key, value in (env or {}).items()
        if str(key).startswith("VITE_") and value is not None
    )
    if not prefix:
        return command
    if "npm run build" in command:
        return command.replace("npm run build", f"{prefix} npm run build", 1)
    return f"{prefix} {command}"


def chat_widget_ui_config(
    *,
    enabled: bool = True,
    src_folder: str = CHAT_WIDGET_SDK_SOURCE,
    build_command: str = DEFAULT_CHAT_WIDGET_BUILD_COMMAND,
    vite_env: Mapping[str, Any] | None = None,
    engine: str = DEFAULT_CHAT_WIDGET_ENGINE,
    shared_sources: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Return a bundle ``config.ui.widgets.<alias>`` entry for the chat widget.

    ``engine`` selects the chat implementation:

      - ``"local"`` (default) — in-tree engine + in-tree ``App.tsx`` UI; no npm://.
      - ``"package"`` — framework-agnostic package engine + in-tree UI
        (``VITE_CHAT_ENGINE=package``).
      - ``"package-ui"`` — package engine **and** the package's own ``<Chat/>`` UI
        (``VITE_CHAT_ENGINE=package`` + ``VITE_CHAT_UI=package``).

    ``"package"`` and ``"package-ui"`` materialize the ``@kdcube/components-*`` source
    via ``npm://`` ``shared_sources``; ``"local"`` carries no ``npm://`` reference.
    Pass ``shared_sources`` explicitly to override the default set.
    """

    engine_norm = str(engine or "").strip().lower()
    use_package = engine_norm in ("package", "package-ui")
    use_package_ui = engine_norm == "package-ui"

    # Merge the engine selector into the Vite env (explicit vite_env wins).
    merged_env: dict[str, Any] = {}
    if use_package:
        merged_env["VITE_CHAT_ENGINE"] = "package"
    if use_package_ui:
        merged_env["VITE_CHAT_UI"] = "package"
    if vite_env:
        merged_env.update(vite_env)

    build_command = _with_chat_vite_env(build_command, merged_env)

    config: dict[str, Any] = {
        "enabled": enabled,
        "src_folder": src_folder,
        "build_command": build_command,
    }

    # Only attach npm:// shared_sources when the package engine is selected (or when
    # a caller passes an explicit set). The local default carries no npm:// reference.
    if shared_sources is not None:
        config["shared_sources"] = dict(shared_sources)
    elif use_package:
        config["shared_sources"] = dict(DEFAULT_CHAT_WIDGET_SHARED_SOURCES)

    config.update(extra)
    return config


def apply_chat_widget_engine(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Expand a chat-widget config's ``engine`` selector into the concrete
    ``build_command`` (VITE env prefix) + ``npm://`` ``shared_sources`` — so a
    bundle config (e.g. ``bundles.yaml``) can say one field::

        versatile_chat:
          src_folder: sdk://solutions/chat/ui/widget
          engine: package-ui        # local | package | package-ui

    instead of hand-writing the build command and shared sources (and juggling
    commented blocks). Returns a shallow copy with ``engine`` consumed.

    **No-op when there is no ``engine`` key** — every other widget config and any
    explicit ``build_command`` is returned untouched, so this is safe to call on
    every widget in the generic build pipeline.
    """
    out = dict(cfg or {})
    if "engine" not in out:
        return out
    engine_norm = str(out.pop("engine") or "").strip().lower()
    use_package = engine_norm in ("package", "package-ui")
    use_package_ui = engine_norm == "package-ui"

    env: dict[str, str] = {}
    if use_package:
        env["VITE_CHAT_ENGINE"] = "package"
    if use_package_ui:
        env["VITE_CHAT_UI"] = "package"

    command = str(out.get("build_command") or DEFAULT_CHAT_WIDGET_BUILD_COMMAND)
    out["build_command"] = _with_chat_vite_env(command, env)
    out.setdefault("src_folder", CHAT_WIDGET_SDK_SOURCE)
    # Package engine needs the npm:// sources materialized; local carries none.
    if use_package and "shared_sources" not in out:
        out["shared_sources"] = dict(DEFAULT_CHAT_WIDGET_SHARED_SOURCES)
    return out


__all__ = [
    "CHAT_WIDGET_SDK_SOURCE",
    "DEFAULT_CHAT_WIDGET_BUILD_COMMAND",
    "DEFAULT_CHAT_WIDGET_SHARED_SOURCES",
    "DEFAULT_CHAT_WIDGET_ENGINE",
    "chat_widget_ui_config",
    "apply_chat_widget_engine",
]
