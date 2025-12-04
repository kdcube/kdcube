# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/environment.py

import fnmatch
from typing import Dict

# Environment variables that should NEVER be passed from host to container
BLOCKED_ENV_VARS = {
    # Python/Path overrides
    "PYTHONPATH",
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",

    # User/Home
    "HOME",
    "USER",
    "LOGNAME",
    "USERNAME",

    # Working directories
    "PWD",
    "OLDPWD",
    "CWD",

    # Virtual environments
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",

    # Shell configuration
    "SHELL",
    "TERM",
    "TERM_PROGRAM",
    "TERM_SESSION_ID",
    "PS1",
    "PS2",
    "PROMPT_COMMAND",

    # Temporary directories
    "TMPDIR",
    "TEMP",
    "TMP",

    # SSH/Authentication (host-specific)
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "SSH_CONNECTION",
    "SSH_CLIENT",
    "SSH_TTY",

    # Development tools
    "LIBRARY_ROOTS",

    # macOS specific
    "LaunchInstanceID",
    "SECURITYSESSIONID",

    # IDE patterns (will match with fnmatch)
    "PYCHARM_*",
    "IDEA_*",
    "IDE_*",
    "INTELLIJ_*",
    "VSCODE_*",
    "VISUAL_STUDIO_*",

    # PyCharm/IntelliJ specific
    "PYDEVD_*",
    "IPYTHONENABLE",
    "ASYNCIO_DEBUGGER_ENV",
    "HALT_VARIABLE_RESOLVE_THREADS_ON_STEP_RESUME",
    "USE_LOW_IMPACT_MONITORING",

    # Apple/XPC
    "__CF*",
    "__PYVENV_LAUNCHER__",
    "XPC_*",

    # Build tools
    "PYENV_*",
    "HOMEBREW_*",
    "CARGO_*",
    "RUSTUP_*",

    # Display/GUI
    "DISPLAY",
    "WINDOWID",
    "XAUTHORITY",

    # System/Process info that shouldn't cross boundary
    "SHLVL",
    "PPID",
    "_",

    # Language/Locale (container should define its own)
    "LC_*",
    "LANG",
    "LANGUAGE",

    # Command mode
    "COMMAND_MODE",
    "INFOPATH",

    # # If using Jupyter
    # "JPY_*",
    # "JUPYTER_*",
    #
    # # If using git/version control
    # "GIT_*",
    #
    # # If you see issues with pkg-config
    # "PKG_CONFIG_PATH",
    #
    # # Docker-in-docker issues
    # "DOCKER_*",
}


def filter_host_environment(host_env: Dict[str, str]) -> Dict[str, str]:
    """
    Filter host environment variables, removing those that shouldn't
    be passed to Docker containers.

    Supports both exact matches and wildcard patterns (e.g., "PYCHARM_*").

    Args:
        host_env: The host environment dict (typically os.environ.copy())

    Returns:
        Filtered environment dict safe to pass to Docker
    """
    filtered = {}

    # Separate exact matches from patterns
    exact_blocks = {var for var in BLOCKED_ENV_VARS if '*' not in var}
    patterns = [var for var in BLOCKED_ENV_VARS if '*' in var]

    for key, value in host_env.items():
        # Skip if exact match
        if key in exact_blocks:
            continue

        # Skip if matches any pattern
        if any(fnmatch.fnmatch(key, pattern) for pattern in patterns):
            continue

        # This variable is safe to pass
        filtered[key] = value

    return filtered