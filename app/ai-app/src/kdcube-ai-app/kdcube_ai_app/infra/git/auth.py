# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


DEFAULT_GIT_HTTP_USER = "x-access-token"
_WARNED_HTTP_SSH = False


def _clean(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_get_secret(key: str) -> str | None:
    try:
        return get_secret(key)
    except Exception:
        return None


def ssh_url_to_https_url(git_url: str) -> str:
    raw = str(git_url or "").strip()
    if raw.startswith("git@") and ":" in raw:
        host_and_path = raw.split("git@", 1)[1]
        host, path = host_and_path.split(":", 1)
        return f"https://{host}/{path}"
    if raw.startswith("ssh://git@"):
        rest = raw.split("ssh://git@", 1)[1]
        if "/" not in rest:
            return raw
        host, path = rest.split("/", 1)
        if ":" in host:
            return raw
        return f"https://{host}/{path}"
    return raw


def _resolved_http_credentials(
    *,
    git_http_token: str | None,
    git_http_user: str | None,
    base_env: Mapping[str, str],
) -> tuple[str | None, str]:
    settings = get_settings()
    token = (
        _clean(git_http_token)
        or _clean(getattr(settings, "GIT_HTTP_TOKEN", None))
        or _clean(_safe_get_secret("services.git.http_token"))
        or _clean(base_env.get("GIT_HTTP_TOKEN"))
    )
    user = (
        _clean(git_http_user)
        or _clean(getattr(settings, "GIT_HTTP_USER", None))
        or _clean(_safe_get_secret("services.git.http_user"))
        or _clean(base_env.get("GIT_HTTP_USER"))
        or DEFAULT_GIT_HTTP_USER
    )
    return token, user


def _resolved_ssh_config(
    *,
    git_ssh_key_path: str | None,
    git_ssh_known_hosts: str | None,
    git_ssh_strict_host_key_checking: str | None,
    base_env: Mapping[str, str],
) -> tuple[str | None, str | None, str | None, str | None]:
    settings = get_settings()
    inherited_ssh_command = _clean(base_env.get("GIT_SSH_COMMAND"))
    key_path = (
        _clean(git_ssh_key_path)
        or _clean(getattr(settings, "GIT_SSH_KEY_PATH", None))
        or _clean(base_env.get("GIT_SSH_KEY_PATH"))
    )
    known_hosts = (
        _clean(git_ssh_known_hosts)
        or _clean(getattr(settings, "GIT_SSH_KNOWN_HOSTS", None))
        or _clean(base_env.get("GIT_SSH_KNOWN_HOSTS"))
    )
    strict = (
        _clean(git_ssh_strict_host_key_checking)
        or _clean(getattr(settings, "GIT_SSH_STRICT_HOST_KEY_CHECKING", None))
        or _clean(base_env.get("GIT_SSH_STRICT_HOST_KEY_CHECKING"))
    )
    return inherited_ssh_command, key_path, known_hosts, strict


def build_git_env(
    *,
    git_http_token: str | None = None,
    git_http_user: str | None = None,
    git_ssh_key_path: str | None = None,
    git_ssh_known_hosts: str | None = None,
    git_ssh_strict_host_key_checking: str | None = None,
    askpass_script_path: pathlib.Path | None = None,
    base_env: Mapping[str, str] | None = None,
    logger: AgentLogger | None = None,
) -> dict[str, str]:
    """
    Build a git subprocess environment from descriptor-backed settings, with
    compatibility fallback to inherited process env when necessary.
    """
    env = dict(base_env if base_env is not None else os.environ)
    token, user = _resolved_http_credentials(
        git_http_token=git_http_token,
        git_http_user=git_http_user,
        base_env=env,
    )
    inherited_ssh_command, key_path, known_hosts, strict = _resolved_ssh_config(
        git_ssh_key_path=git_ssh_key_path,
        git_ssh_known_hosts=git_ssh_known_hosts,
        git_ssh_strict_host_key_checking=git_ssh_strict_host_key_checking,
        base_env=env,
    )

    if token:
        global _WARNED_HTTP_SSH
        if not _WARNED_HTTP_SSH and (inherited_ssh_command or key_path or known_hosts or strict):
            (logger or AgentLogger("git.auth")).log(
                "Both HTTPS token auth and SSH git settings are configured. HTTPS token auth will be used.",
                level="WARNING",
            )
            _WARNED_HTTP_SSH = True
        askpass_path = askpass_script_path or pathlib.Path("/tmp/kdcube_git_askpass.sh")
        askpass_contents = (
            "#!/bin/sh\n"
            "prompt=\"$1\"\n"
            "if echo \"$prompt\" | grep -qi \"username\"; then\n"
            "  echo \"${GIT_HTTP_USER:-x-access-token}\"\n"
            "else\n"
            "  echo \"${GIT_HTTP_TOKEN}\"\n"
            "fi\n"
        )
        try:
            if not askpass_path.exists() or askpass_path.read_text(encoding="utf-8") != askpass_contents:
                askpass_path.parent.mkdir(parents=True, exist_ok=True)
                askpass_path.write_text(askpass_contents, encoding="utf-8")
                askpass_path.chmod(0o700)
        except Exception:
            pass
        env["GIT_HTTP_TOKEN"] = token
        env["GIT_HTTP_USER"] = user
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS_REQUIRE"] = "force"
        if askpass_path.exists():
            env["GIT_ASKPASS"] = str(askpass_path)
        return env

    if inherited_ssh_command:
        env["GIT_SSH_COMMAND"] = inherited_ssh_command
        return env

    if not key_path and not known_hosts and not strict:
        return env

    cmd = ["ssh"]
    if key_path:
        cmd += ["-i", key_path, "-o", "IdentitiesOnly=yes"]
    if strict:
        cmd += ["-o", f"StrictHostKeyChecking={strict}"]
    if known_hosts:
        cmd += ["-o", f"UserKnownHostsFile={known_hosts}"]
    env["GIT_SSH_COMMAND"] = " ".join(cmd)
    return env


def normalize_git_remote_url(
    git_url: str,
    *,
    git_http_token: str | None = None,
    base_env: Mapping[str, str] | None = None,
) -> str:
    env = dict(base_env if base_env is not None else os.environ)
    token, _user = _resolved_http_credentials(
        git_http_token=git_http_token,
        git_http_user=None,
        base_env=env,
    )
    if not token:
        return str(git_url or "").strip()
    return ssh_url_to_https_url(str(git_url or "").strip())


def _git_repo_cmd(repo_root: pathlib.Path, args: list[str]) -> list[str]:
    repo = str(pathlib.Path(repo_root).resolve())
    return ["git", "-c", f"safe.directory={repo}", "-C", repo, *args]


def ensure_git_commit_identity(
    *,
    repo_root: pathlib.Path,
    name: str,
    email: str,
    env: Mapping[str, str] | None = None,
) -> None:
    """
    Ensure repo-local git commit identity without mutating process-global git config.
    """
    repo_root = pathlib.Path(repo_root).resolve()
    git_env = dict(env if env is not None else os.environ)

    def _git_config_get(key: str) -> str:
        proc = subprocess.run(
            _git_repo_cmd(repo_root, ["config", "--get", key]),
            capture_output=True,
            text=True,
            env=git_env,
        )
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").strip()

    current_name = _git_config_get("user.name")
    if current_name != name:
        subprocess.run(
            _git_repo_cmd(repo_root, ["config", "user.name", name]),
            check=True,
            capture_output=True,
            env=git_env,
        )

    current_email = _git_config_get("user.email")
    if current_email != email:
        subprocess.run(
            _git_repo_cmd(repo_root, ["config", "user.email", email]),
            check=True,
            capture_output=True,
            env=git_env,
        )
