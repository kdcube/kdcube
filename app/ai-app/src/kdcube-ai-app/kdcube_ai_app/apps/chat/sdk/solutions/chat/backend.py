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
DEFAULT_CHAT_WIDGET_ENGINE = "local"


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

    ``engine`` is the single switch between the in-tree engine (``"local"``,
    default) and the framework-agnostic package engine (``"package"``). Selecting
    ``"package"`` both sets ``VITE_CHAT_ENGINE=package`` and materializes the
    ``@kdcube/components-*`` source via ``npm://`` ``shared_sources``; ``"local"``
    does neither. Pass ``shared_sources`` explicitly to override the default set.
    """

    use_package = str(engine or "").strip().lower() == "package"

    # Merge the engine selector into the Vite env (explicit vite_env wins).
    merged_env: dict[str, Any] = {}
    if use_package:
        merged_env["VITE_CHAT_ENGINE"] = "package"
    if vite_env:
        merged_env.update(vite_env)

    if merged_env:
        env_prefix = " ".join(
            f"{key}={shlex.quote(str(value))}"
            for key, value in merged_env.items()
            if str(key).startswith("VITE_") and value is not None
        )
        if env_prefix:
            build_command = f"{env_prefix} {build_command}"

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


__all__ = [
    "CHAT_WIDGET_SDK_SOURCE",
    "DEFAULT_CHAT_WIDGET_BUILD_COMMAND",
    "DEFAULT_CHAT_WIDGET_SHARED_SOURCES",
    "DEFAULT_CHAT_WIDGET_ENGINE",
    "chat_widget_ui_config",
]
