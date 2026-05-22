# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Backward-compatible import shim for the renamed bundle loader.

New code should import from ``kdcube_ai_app.infra.plugin.bundle_loader``.
This module remains so existing released bundles and third-party code that
still import ``agentic_loader`` continue to use the same runtime objects.
"""

import sys as _sys

from . import bundle_loader as _bundle_loader
from .bundle_loader import *  # noqa: F401,F403

_sys.modules[__name__] = _bundle_loader
