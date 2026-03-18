# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from .platform_env import (
    EXTERNAL_RUNTIME_ENV_KEYS,
    PLATFORM_ENV_GROUPS,
    build_external_runtime_base_env,
    build_external_runtime_inline_env,
    collect_platform_env_groups,
)
from .external_runtime import prepare_external_runtime_globals

__all__ = [
    "EXTERNAL_RUNTIME_ENV_KEYS",
    "PLATFORM_ENV_GROUPS",
    "build_external_runtime_base_env",
    "build_external_runtime_inline_env",
    "collect_platform_env_groups",
    "prepare_external_runtime_globals",
]
