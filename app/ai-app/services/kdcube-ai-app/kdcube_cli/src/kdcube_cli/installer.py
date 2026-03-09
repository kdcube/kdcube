# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import re
import shutil
import json
import subprocess
from dataclasses import dataclass
import secrets
import tempfile
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text


ENV_FILES = [
    ".env",
    ".env.ingress",
    ".env.proc",
    ".env.metrics",
    ".env.postgres.setup",
    ".env.proxylogin",
]


DEFAULT_BUNDLES_JSON = [
    "AGENTIC_BUNDLES_JSON='{",
    "  \"default_bundle_id\": \"demo.bundle@1.0.0\",",
    "  \"bundles\": {",
    "        \"demo.bundle@1.0.0\": {",
    "          \"id\": \"demo.bundle@1.0.0\",",
    "          \"name\": \"Demo Bundle\",",
    "          \"path\": \"/bundles\",",
    "          \"module\": \"demo.entrypoint\",",
    "          \"singleton\": false,",
    "          \"description\": \"Example bundle used for quickstart.\"",
    "        }",
    "  }",
    "}'",
]


@dataclass
class EnvFile:
    path: Path
    lines: List[str]
    entries: Dict[str, Tuple[int, str]]


@dataclass
class PathsContext:
    lib_root: Path
    ai_app_root: Path
    docker_dir: Path
    sample_env_dir: Path
    workdir: Path
    config_dir: Path
    data_dir: Path


def is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"")
    if not stripped:
        return True
    if stripped.upper() in {"TENANT_ID", "PROJECT_ID"}:
        return True
    if "<" in stripped and ">" in stripped:
        return True
    if "/absolute/path" in stripped or "absolute/path" in stripped:
        return True
    if "..." in stripped:
        return True
    if "changeme" in stripped.lower():
        return True
    return False


def is_default_tenant_project(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"").lower()
    return stripped in {"default", "demo-tenant", "demo-project"}


def parse_env(lines: List[str]) -> Dict[str, Tuple[int, str]]:
    entries: Dict[str, Tuple[int, str]] = {}
    for idx, line in enumerate(lines):
        if not line or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        entries[key] = (idx, value)
    return entries


def update_env_value(env_file: EnvFile, key: str, value: str) -> None:
    if key in env_file.entries:
        idx, _ = env_file.entries[key]
        env_file.lines[idx] = f"{key}={value}"
    else:
        env_file.lines.append(f"{key}={value}")
    env_file.entries = parse_env(env_file.lines)


def update_if_placeholder(env_file: EnvFile, key: str, value: str) -> None:
    current = env_file.entries.get(key, (None, None))[1]
    if is_placeholder(current):
        update_env_value(env_file, key, value)


def _normalize_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return value


def _extract_multiline_value(env: EnvFile, key: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    start_idx = None
    for idx, line in enumerate(env.lines):
        if line.startswith(f"{key}="):
            start_idx = idx
            break
    if start_idx is None:
        return None, None, None
    value = env.lines[start_idx].split("=", 1)[1]
    end_idx = start_idx
    if value.count("'") % 2 == 1:
        while end_idx + 1 < len(env.lines):
            end_idx += 1
            value += "\n" + env.lines[end_idx]
            if env.lines[end_idx].count("'") % 2 == 1:
                break
    return value, start_idx, end_idx


def _format_json_multiline(key: str, data: Dict[str, object]) -> List[str]:
    json_text = json.dumps(data, indent=2)
    lines = json_text.splitlines()
    lines[0] = f"{key}='" + lines[0]
    lines[-1] = lines[-1] + "'"
    return lines


def _extract_tenant_project(env: EnvFile) -> Tuple[Optional[str], Optional[str]]:
    raw, _, _ = _extract_multiline_value(env, "GATEWAY_CONFIG_JSON")
    if raw is None:
        return None, None
    stripped = raw.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        json_text = stripped[1:-1]
    else:
        json_text = stripped
    try:
        data = json.loads(json_text)
        tenant = data.get("tenant")
        project = data.get("project")
        if tenant in {"<TENANT_ID>", "TENANT_ID"}:
            tenant = None
        if project in {"<PROJECT_ID>", "PROJECT_ID"}:
            project = None
        return tenant, project
    except json.JSONDecodeError:
        tenant_match = re.search(r'"tenant"\s*:\s*"([^"]+)"', json_text)
        project_match = re.search(r'"project"\s*:\s*"([^"]+)"', json_text)
        tenant = tenant_match.group(1) if tenant_match else None
        project = project_match.group(1) if project_match else None
        if tenant in {"<TENANT_ID>", "TENANT_ID"}:
            tenant = None
        if project in {"<PROJECT_ID>", "PROJECT_ID"}:
            project = None
        return tenant, project


def patch_gateway_config_json(env: EnvFile, tenant: str, project: str) -> None:
    raw, start_idx, end_idx = _extract_multiline_value(env, "GATEWAY_CONFIG_JSON")
    if raw is None:
        return

    stripped = raw.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        json_text = stripped[1:-1]
    else:
        json_text = stripped

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        updated = re.sub(r'"tenant"\s*:\s*"[^"]*"', f'"tenant":"{tenant}"', json_text)
        updated = re.sub(r'"project"\s*:\s*"[^"]*"', f'"project":"{project}"', updated)
        if updated != json_text:
            replace_multiline_block(env, "GATEWAY_CONFIG_JSON", [f"GATEWAY_CONFIG_JSON='{updated}'"])
        return

    data["tenant"] = tenant
    data["project"] = project
    replace_multiline_block(env, "GATEWAY_CONFIG_JSON", _format_json_multiline("GATEWAY_CONFIG_JSON", data))


def _load_json_file(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def normalize_routes_prefix(value: Optional[str]) -> str:
    prefix = (value or "").strip()
    if not prefix:
        return "/chatbot"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix != "/" and prefix.endswith("/"):
        prefix = prefix.rstrip("/")
    return prefix


def sync_nginx_proxy_config(target_path: Path, ai_app_root: Path, template_rel: str) -> None:
    repo_root = ai_app_root.parent.parent
    src = repo_root / template_rel
    if not src.exists():
        return
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target_path)
    except Exception:
        return


def update_nginx_routes_prefix(path: Path, routes_prefix: str) -> None:
    routes_prefix = normalize_routes_prefix(routes_prefix)
    if not path.exists():
        return
    try:
        current = path.read_text()
    except Exception:
        return
    updated = current.replace("/chatbot", routes_prefix)
    if updated != current:
        path.write_text(updated)


def write_frontend_config(
    path: Path,
    tenant: str,
    project: str,
    token: str = "test-admin-token-123",
    *,
    template_path: Optional[Path] = None,
    cognito_region: Optional[str] = None,
    cognito_user_pool_id: Optional[str] = None,
    cognito_app_client_id: Optional[str] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    template_data: Dict[str, object] = {}
    if template_path and template_path.exists():
        template_data = _load_json_file(template_path)

    data: Dict[str, object] = {}
    if path.exists():
        data = _load_json_file(path)

    merged: Dict[str, object] = {}
    merged.update(template_data)
    merged.update(data)

    merged["tenant"] = tenant
    merged["project"] = project
    if "tenant_id" in merged:
        merged["tenant_id"] = tenant
    if "project_id" in merged:
        merged["project_id"] = project
    merged.setdefault("routesPrefix", "/chatbot")

    auth = merged.get("auth") if isinstance(merged.get("auth"), dict) else {}
    auth_type = auth.get("authType") or "hardcoded"
    auth["authType"] = auth_type
    if auth_type == "hardcoded":
        if auth.get("token") in (None, "", "test-admin-token-123"):
            auth["token"] = token
    elif auth_type == "cognito":
        if "token" in auth:
            auth.pop("token", None)
        oidc_cfg = auth.get("oidcConfig") if isinstance(auth.get("oidcConfig"), dict) else {}
        if cognito_region and cognito_user_pool_id:
            oidc_cfg["authority"] = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"
        if cognito_app_client_id:
            oidc_cfg["client_id"] = cognito_app_client_id
        auth["oidcConfig"] = oidc_cfg
    merged["auth"] = auth

    path.write_text(json.dumps(merged, indent=2) + "\n")


def replace_multiline_block(env_file: EnvFile, key: str, new_lines: List[str]) -> None:
    start_idx = None
    for idx, line in enumerate(env_file.lines):
        if line.startswith(f"{key}="):
            start_idx = idx
            break
    if start_idx is None:
        if env_file.lines and env_file.lines[-1].strip():
            env_file.lines.append("")
        env_file.lines.extend(new_lines)
        env_file.entries = parse_env(env_file.lines)
        return

    end_idx = start_idx
    quote_open = env_file.lines[start_idx].count("'") % 2 == 1
    while quote_open and end_idx + 1 < len(env_file.lines):
        end_idx += 1
        if env_file.lines[end_idx].count("'") % 2 == 1:
            quote_open = False
    env_file.lines[start_idx : end_idx + 1] = new_lines
    env_file.entries = parse_env(env_file.lines)


def ensure_env_files(target_dir: Path, sample_env_dir: Path) -> None:
    for env_name in ENV_FILES:
        target = target_dir / env_name
        if target.exists():
            continue
        sample = sample_env_dir / env_name
        if not sample.exists():
            raise FileNotFoundError(f"Missing sample env file: {sample}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, target)


def ensure_nginx_configs(target_dir: Path, ai_app_root: Path) -> None:
    src_dir = ai_app_root / "deployment/docker/all_in_one_kdcube/nginx/conf"
    for name in ("nginx_ui.conf", "nginx_proxy.conf", "nginx_proxy_delegated.conf"):
        target = target_dir / name
        if target.exists():
            continue
        src = src_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Missing nginx config template: {src}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)


def ensure_local_dirs(data_dir: Path, logs_dir: Path) -> None:
    for path in [
        data_dir / "kdcube-storage",
        data_dir / "exec-workspace",
        data_dir / "bundle-storage",
        data_dir / "bundles",
        data_dir / "postgres",
        data_dir / "redis",
        data_dir / "clamav-db",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("chat-ingress", "chat-proc"):
        (logs_dir / subdir).mkdir(parents=True, exist_ok=True)
    for path in (logs_dir, logs_dir / "chat-ingress", logs_dir / "chat-proc"):
        try:
            os.chmod(path, 0o777)
        except Exception:
            pass


def compose_env(env_file: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env["COMPOSE_ENV_FILES"] = str(env_file)
    return env


def list_compose_services(ctx: PathsContext, env_file: Path) -> List[str]:
    try:
        output = subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "config",
                "--services",
            ],
            cwd=ctx.docker_dir,
            env=compose_env(env_file),
            text=True,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]
    except Exception:
        return []


def apply_runtime_secrets(console: Console, ctx: PathsContext, secrets: Dict[str, str], env_file: Path) -> None:
    if not secrets:
        return
    if not wait_for_secrets_ready(console, ctx, env_file):
        console.print("[red]Secrets service not ready. Skipping secret injection.[/red]")
        return
    console.print("[dim]Injecting runtime secrets into secrets service...[/dim]")
    for key, value in secrets.items():
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "exec",
                    "-T",
                    "kdcube-secrets",
                    "python",
                    "/app/secretsctl.py",
                    "set",
                    key,
                    value,
                ],
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(env_file),
            )
        except FileNotFoundError:
            console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
            return
        except subprocess.CalledProcessError:
            console.print("[red]Failed to inject secrets. Ensure kdcube-secrets is running.[/red]")
            return


def wait_for_secrets_ready(console: Console, ctx: PathsContext, env_file: Path, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "exec",
                    "-T",
                    "kdcube-secrets",
                    "python",
                    "-c",
                    (
                        "import sys,urllib.request\n"
                        "try:\n"
                        "    r=urllib.request.urlopen('http://127.0.0.1:7777/health',timeout=1)\n"
                        "    sys.exit(0 if r.status==200 else 1)\n"
                        "except Exception:\n"
                        "    sys.exit(1)\n"
                    ),
                ],
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(env_file),
            )
            return True
        except Exception:
            time.sleep(1)
    console.print("[yellow]Timed out waiting for secrets service.[/yellow]")
    return False


def generate_runtime_tokens() -> Dict[str, str]:
    admin = secrets.token_urlsafe(24)
    ingress = secrets.token_urlsafe(16)
    proc = secrets.token_urlsafe(16)
    return {
        "SECRETS_ADMIN_TOKEN": admin,
        "SECRETS_READ_TOKENS": f"{ingress},{proc}",
        "SECRETS_TOKEN_INGRESS": ingress,
        "SECRETS_TOKEN_PROC": proc,
    }


def write_env_overlay(base_env: Path, overrides: Dict[str, str]) -> Path:
    env = load_env_file(base_env)
    for key, value in overrides.items():
        update_env_value(env, key, value)
    fd, tmp_path = tempfile.mkstemp(prefix="kdcube-env-", suffix=".env")
    os.close(fd)
    env.path = Path(tmp_path)
    save_env_file(env)
    return env.path


def load_env_file(path: Path) -> EnvFile:
    lines = path.read_text().splitlines()
    entries = parse_env(lines)
    return EnvFile(path=path, lines=lines, entries=entries)


def save_env_file(env_file: EnvFile) -> None:
    text = "\n".join(env_file.lines).rstrip() + "\n"
    env_file.path.write_text(text)

def missing_build_keys(env_main: EnvFile) -> List[str]:
    keys = [
        "UI_BUILD_CONTEXT",
        "UI_DOCKERFILE_PATH",
        "UI_SOURCE_PATH",
        "NGINX_UI_CONFIG_FILE_PATH",
        "PROXY_BUILD_CONTEXT",
        "PROXY_DOCKERFILE_PATH",
        "NGINX_PROXY_CONFIG_FILE_PATH",
    ]
    missing = []
    for key in keys:
        val = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(val):
            missing.append(key)
    return missing


def discover_lib_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "kdcube_ai_app").is_dir():
            return parent
    return None


def find_ai_app_root(lib_root: Optional[Path]) -> Optional[Path]:
    if lib_root is not None:
        candidate = lib_root.parent.parent
        compose = candidate / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return candidate

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        compose = parent / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return compose.parents[3]
    return None


def prompt_for_ai_app_root(console: Console) -> Path:
    while True:
        raw = ask(console, "Path to ai-app root (contains deployment/docker/all_in_one_kdcube)")
        candidate = Path(raw).expanduser().resolve()
        compose = candidate / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return candidate
        console.print("[red]Could not find docker-compose.yaml under that path.[/red]")


def _label(text: str) -> str:
    return f"[bold blue]{text}[/]"


def _mask(value: str) -> str:
    return "*" * len(value)

def _abort_if_quit(value: str) -> None:
    if value.strip().lower() in {"q", "quit", "exit"}:
        raise SystemExit("Setup cancelled by user.")


def ask(console: Console, label: str, default: Optional[str] = None, secret: bool = False) -> str:
    value = Prompt.ask(_label(label), default=default or "", password=secret)
    _abort_if_quit(value)
    return value


def ask_confirm(console: Console, label: str, default: bool = False) -> bool:
    default_hint = "y" if default else "n"
    while True:
        raw = console.input(f"{label} [y/n] ({default_hint}): ").strip().lower()
        if not raw:
            return default
        if raw in {"q", "quit", "exit"}:
            raise SystemExit("Setup cancelled by user.")
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print("[red]Please enter y/n or q to quit.[/red]")


def prompt_optional(console: Console, label: str, secret: bool = False) -> str:
    console.print(f"{_label(label)} [dim](leave blank to skip)[/dim]")
    value = console.input("> ", password=secret).strip()
    _abort_if_quit(value)
    return value


def prompt_optional_keep(console: Console, label: str, current: Optional[str]) -> Optional[str]:
    if current and not is_placeholder(current):
        console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
    else:
        console.print(f"{_label(label)} [dim](leave blank to skip)[/dim]")
    value = console.input("> ").strip()
    _abort_if_quit(value)
    if not value:
        return current if current and not is_placeholder(current) else None
    return value


def ensure_absolute(
    console: Console,
    label: str,
    current: Optional[str],
    default: Optional[str],
    *,
    force_prompt: bool = False,
) -> str:
    current_value = None if is_placeholder(current) else current
    if not force_prompt and current_value and Path(current_value).is_absolute():
        return current_value
    while True:
        value = ask(console, label, default=current_value or default or "")
        if not value:
            console.print("[red]Please provide a value.[/red]")
            continue
        resolved = Path(value).expanduser().resolve()
        return str(resolved)


def prompt_secret(
    console: Console,
    env_file: EnvFile,
    key: str,
    label: str,
    *,
    required: bool = False,
    force_prompt: bool = False,
) -> Optional[str]:
    current = env_file.entries.get(key, (None, None))[1]
    if not force_prompt and not is_placeholder(current):
        return current
    while True:
        if force_prompt and current and not is_placeholder(current):
            console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
            value = console.input("> ", password=True).strip()
            _abort_if_quit(value)
            if not value:
                return current
        elif required:
            value = ask(console, label, secret=True)
        else:
            value = prompt_optional(console, label, secret=True)
        if value:
            update_env_value(env_file, key, value)
            console.print(f"{_label(label)}: [dim]{_mask(value)}[/]")
            return value
        if required:
            console.print("[red]This value is required. Please enter a value.[/red]")
            continue
        return current if force_prompt else None


def prompt_secret_value(
    console: Console,
    label: str,
    *,
    required: bool = False,
    current: Optional[str] = None,
    force_prompt: bool = False,
) -> Optional[str]:
    current_value = None if is_placeholder(current) else current
    if not force_prompt and current_value:
        return current_value
    while True:
        if force_prompt and current_value:
            console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
            value = console.input("> ", password=True).strip()
            _abort_if_quit(value)
            if not value:
                return current_value
        elif required:
            value = ask(console, label, secret=True)
        else:
            value = prompt_optional(console, label, secret=True)
        if value:
            console.print(f"{_label(label)}: [dim]{_mask(value)}[/]")
            return value
        if required:
            console.print("[red]This value is required. Please enter a value.[/red]")
            continue
        return current_value if force_prompt else None


def prompt_choice(console: Console, label: str, choices: List[str], default: str) -> str:
    value = Prompt.ask(_label(label), choices=choices, default=default)
    _abort_if_quit(value)
    return value


def select_option(console: Console, title: str, options: List[str], default_index: int = 0) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return prompt_choice(console, title, options, options[default_index])
    try:
        from readchar import readkey, key
    except Exception:
        return prompt_choice(console, title, options, options[default_index])

    idx = max(0, min(default_index, len(options) - 1))

    def _render() -> Panel:
        text = Text()
        text.append(title + "\n\n", style="bold")
        for i, option in enumerate(options):
            if i == idx:
                text.append("➤ ", style="bold cyan")
                text.append(option, style="bold cyan")
            else:
                text.append("  " + option)
            text.append("\n")
        text.append("\nUse ↑/↓ and Enter.", style="dim")
        return Panel(text, title="Select")

    with Live(_render(), console=console, refresh_per_second=30, transient=True) as live:
        while True:
            k = readkey()
            if k in (key.UP, "k"):
                idx = (idx - 1) % len(options)
            elif k in (key.DOWN, "j"):
                idx = (idx + 1) % len(options)
            elif k in (key.ENTER, "\r"):
                return options[idx]
            elif k in (key.CTRL_C, "\x03"):
                raise KeyboardInterrupt
            live.update(_render())


def compute_paths(ai_app_root: Path, lib_root: Path, workdir: Path) -> Dict[str, str]:
    docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    repo_root = ai_app_root.parent.parent
    defaults: Dict[str, str] = {
        "docker_dir": str(docker_dir),
        "host_kb_storage": str(workdir / "data/kdcube-storage"),
        "host_bundle_storage": str(workdir / "data/bundle-storage"),
        "host_exec_workspace": str(workdir / "data/exec-workspace"),
        "host_bundles": str(workdir / "data/bundles"),
        "ui_dockerfile_path": "deployment/docker/all_in_one_kdcube/Dockerfile_UI",
        "ui_source_path": "ui/chat-web-app",
        "ui_env_build_relative": "ui/chat-web-app/.env.sample",
        "nginx_ui_config": "deployment/docker/all_in_one_kdcube/nginx/conf/nginx_ui.conf",
        "frontend_config_json": str((workdir / "config/frontend.config.hardcoded.json").resolve()),
    }

    common_parent = repo_root
    defaults["proxy_build_context"] = str(common_parent)
    defaults["proxy_dockerfile_path"] = str(
        (ai_app_root / "deployment/docker/all_in_one_kdcube/Dockerfile_ProxyOpenResty").relative_to(common_parent)
    )
    defaults["ui_build_context"] = str(ai_app_root)
    defaults["ui_env_file_path"] = str(ai_app_root / "ui/chat-web-app/.env")
    defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
    return defaults


def should_replace_bundles_config(value: Optional[str]) -> bool:
    if is_placeholder(value):
        return True
    if value and "/config/release.yaml" in value:
        return False
    if value and ("kdcube.demo.1" in value or "<project>" in value):
        return True
    return False


def gather_configuration(console: Console, ctx: PathsContext) -> Tuple[Dict[str, str], Dict[str, str]]:
    force_prompt = os.getenv("KDCUBE_RESET_CONFIG", "").lower() in {"1", "true", "yes", "on"}
    env_main = load_env_file(ctx.config_dir / ".env")
    env_ingress = load_env_file(ctx.config_dir / ".env.ingress")
    env_proc = load_env_file(ctx.config_dir / ".env.proc")
    env_metrics = load_env_file(ctx.config_dir / ".env.metrics")
    env_pg = load_env_file(ctx.config_dir / ".env.postgres.setup")
    env_proxy = load_env_file(ctx.config_dir / ".env.proxylogin")
    runtime_secrets: Dict[str, str] = {}

    defaults = compute_paths(ctx.ai_app_root, ctx.lib_root, ctx.workdir)

    existing_tenant, existing_project = _extract_tenant_project(env_ingress)
    if not existing_tenant or not existing_project:
        alt_tenant, alt_project = _extract_tenant_project(env_proc)
        existing_tenant = existing_tenant or alt_tenant
        existing_project = existing_project or alt_project
    if not existing_tenant or not existing_project:
        alt_tenant, alt_project = _extract_tenant_project(env_metrics)
        existing_tenant = existing_tenant or alt_tenant
        existing_project = existing_project or alt_project

    tenant = ask(console, "Tenant ID", default=existing_tenant or "demo-tenant")
    project = ask(console, "Project name", default=existing_project or "demo-project")
    if is_placeholder(tenant):
        tenant = "demo-tenant"
    if is_placeholder(project):
        project = "demo-project"
    for env in (env_ingress, env_proc, env_metrics):
        patch_gateway_config_json(env, tenant, project)
    if is_placeholder(env_pg.entries.get("TENANT_ID", (None, None))[1]) or is_default_tenant_project(
        env_pg.entries.get("TENANT_ID", (None, None))[1]
    ):
        update_env_value(env_pg, "TENANT_ID", tenant)
    if is_placeholder(env_pg.entries.get("PROJECT_ID", (None, None))[1]) or is_default_tenant_project(
        env_pg.entries.get("PROJECT_ID", (None, None))[1]
    ):
        update_env_value(env_pg, "PROJECT_ID", project)

    update_if_placeholder(env_ingress, "SECRETS_PROVIDER", "local")
    update_if_placeholder(env_proc, "SECRETS_PROVIDER", "local")
    update_if_placeholder(env_ingress, "SECRETS_URL", "http://kdcube-secrets:7777")
    update_if_placeholder(env_proc, "SECRETS_URL", "http://kdcube-secrets:7777")
    update_if_placeholder(env_ingress, "LINK_PREVIEW_ENABLED", "0")

    # Auth provider selection
    existing_auth = env_ingress.entries.get("AUTH_PROVIDER", (None, None))[1] or env_proc.entries.get("AUTH_PROVIDER", (None, None))[1]
    auth_options = ["simple", "cognito", "delegated (disabled)"]
    default_auth = "cognito" if (existing_auth or "").strip().lower() == "cognito" else "simple"
    default_idx = auth_options.index(default_auth)
    console.print("[bold]Authentication[/bold]")
    while True:
        auth_choice = select_option(
            console,
            "Auth type",
            options=auth_options,
            default_index=default_idx,
        )
        if auth_choice.startswith("delegated"):
            console.print("[yellow]Delegated auth is not available yet. Please choose simple or cognito.[/yellow]")
            continue
        break
    auth_provider = "simple" if auth_choice == "simple" else "cognito"
    update_env_value(env_ingress, "AUTH_PROVIDER", auth_provider)
    update_env_value(env_proc, "AUTH_PROVIDER", auth_provider)

    if auth_provider == "cognito":
        console.print("[dim]Cognito auth is used for both cognito and delegated modes (delegated is disabled for now).[/dim]")
        cognito_region = env_ingress.entries.get("COGNITO_REGION", (None, None))[1]
        if force_prompt or is_placeholder(cognito_region):
            cognito_region = ask(console, "COGNITO_REGION", default="eu-west-1")
        update_env_value(env_ingress, "COGNITO_REGION", cognito_region or "eu-west-1")
        update_env_value(env_proc, "COGNITO_REGION", cognito_region or "eu-west-1")

        for key in ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID", "COGNITO_SERVICE_CLIENT_ID"):
            current_val = env_ingress.entries.get(key, (None, None))[1]
            if force_prompt or is_placeholder(current_val):
                current_val = ask(console, key, default=current_val or "")
            update_env_value(env_ingress, key, current_val or "")
            update_env_value(env_proc, key, current_val or "")


    pg_user = env_pg.entries.get("POSTGRES_USER", (None, None))[1]
    if force_prompt or is_placeholder(pg_user):
        pg_user_default = pg_user if not is_placeholder(pg_user) else "postgres"
        pg_user = ask(console, "Postgres user", default=pg_user_default)
        update_env_value(env_pg, "POSTGRES_USER", pg_user)
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_USER", pg_user or "postgres")
        update_env_value(env_proc, "POSTGRES_USER", pg_user or "postgres")
    else:
        update_if_placeholder(env_ingress, "POSTGRES_USER", pg_user or "postgres")
        update_if_placeholder(env_proc, "POSTGRES_USER", pg_user or "postgres")

    # If .env.postgres.setup is empty, fall back to .env values
    pg_pass_env = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]
    if is_placeholder(pg_pass_env):
        fallback_pg = env_main.entries.get("POSTGRES_PASSWORD", (None, None))[1]
        if is_placeholder(fallback_pg):
            fallback_pg = env_main.entries.get("PGPASSWORD", (None, None))[1]
        if not is_placeholder(fallback_pg):
            update_env_value(env_pg, "POSTGRES_PASSWORD", fallback_pg)

    pg_pass = prompt_secret(
        console,
        env_pg,
        "POSTGRES_PASSWORD",
        "Postgres password",
        required=True,
        force_prompt=force_prompt,
    )
    if not pg_pass:
        pg_pass = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1] or ""
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_PASSWORD", pg_pass or "postgres")
        update_env_value(env_proc, "POSTGRES_PASSWORD", pg_pass or "postgres")
    else:
        update_if_placeholder(env_ingress, "POSTGRES_PASSWORD", pg_pass or "postgres")
        update_if_placeholder(env_proc, "POSTGRES_PASSWORD", pg_pass or "postgres")

    pg_db = env_pg.entries.get("POSTGRES_DATABASE", (None, None))[1]
    if force_prompt or is_placeholder(pg_db):
        pg_db_default = pg_db if not is_placeholder(pg_db) else "kdcube"
        pg_db = ask(console, "Postgres database", default=pg_db_default)
        update_env_value(env_pg, "POSTGRES_DATABASE", pg_db)
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_env_value(env_proc, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_env_value(env_main, "PGUSER", pg_user or "postgres")
        update_env_value(env_main, "PGPASSWORD", pg_pass or "postgres")
        update_env_value(env_main, "PGDATABASE", pg_db or "kdcube")
    else:
        update_if_placeholder(env_ingress, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_if_placeholder(env_proc, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_if_placeholder(env_main, "PGUSER", pg_user or "postgres")
        update_if_placeholder(env_main, "PGPASSWORD", pg_pass or "postgres")
        update_if_placeholder(env_main, "PGDATABASE", pg_db or "kdcube")

    redis_pass = prompt_secret(
        console,
        env_main,
        "REDIS_PASSWORD",
        "Redis password",
        required=True,
        force_prompt=force_prompt,
    )
    if not redis_pass:
        redis_pass = env_main.entries.get("REDIS_PASSWORD", (None, None))[1] or ""

    if force_prompt:
        update_env_value(env_ingress, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_proc, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_metrics, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_ingress, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_env_value(env_proc, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_env_value(env_metrics, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_env_value(env_proxy, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
    else:
        update_if_placeholder(env_ingress, "REDIS_PASSWORD", redis_pass)
        update_if_placeholder(env_proc, "REDIS_PASSWORD", redis_pass)
        update_if_placeholder(env_metrics, "REDIS_PASSWORD", redis_pass)
        update_if_placeholder(env_ingress, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_if_placeholder(env_proc, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_if_placeholder(env_metrics, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_if_placeholder(env_proxy, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")

    if is_placeholder(env_ingress.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_ingress, "POSTGRES_HOST", "postgres-db")
    if is_placeholder(env_proc.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_proc, "POSTGRES_HOST", "postgres-db")

    openai_key = prompt_secret_value(
        console,
        "OpenAI API key (leave blank to skip)",
        required=False,
        current=env_proc.entries.get("OPENAI_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    anthropic_key = prompt_secret_value(
        console,
        "Anthropic API key (leave blank to skip)",
        required=False,
        current=env_proc.entries.get("ANTHROPIC_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    brave_key = prompt_secret_value(
        console,
        "Brave Search API key (leave blank to skip)",
        required=False,
        current=env_proc.entries.get("BRAVE_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    if openai_key:
        runtime_secrets["OPENAI_API_KEY"] = openai_key
    if anthropic_key:
        runtime_secrets["ANTHROPIC_API_KEY"] = anthropic_key
    if brave_key:
        runtime_secrets["BRAVE_API_KEY"] = brave_key
    if force_prompt or is_placeholder(env_proc.entries.get("OPENAI_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "OPENAI_API_KEY", "")
    if force_prompt or is_placeholder(env_proc.entries.get("ANTHROPIC_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "ANTHROPIC_API_KEY", "")
    if force_prompt or is_placeholder(env_proc.entries.get("BRAVE_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "BRAVE_API_KEY", "")

    host_storage = ensure_absolute(
        console,
        "Host system storage path",
        env_main.entries.get("HOST_KDCUBE_STORAGE_PATH", (None, None))[1],
        defaults.get("host_kb_storage"),
        force_prompt=force_prompt,
    )
    host_bundles_current = env_main.entries.get("HOST_BUNDLES_PATH", (None, None))[1]
    agentic_root = env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1] or "/bundles"
    if host_bundles_current:
        normalized = str(host_bundles_current).strip()
        if normalized.startswith("/bundles") or normalized.startswith("/app/") or normalized == agentic_root:
            console.print(
                "[yellow]HOST_BUNDLES_PATH points to a container path; "
                "resetting to the local workdir bundles folder.[/yellow]"
            )
            host_bundles_current = None
    if force_prompt or not is_placeholder(host_bundles_current):
        host_bundles = ensure_absolute(
            console,
            "Host bundles root (git clones)",
            host_bundles_current,
            defaults.get("host_bundles"),
            force_prompt=force_prompt,
        )
    else:
        host_bundles = defaults.get("host_bundles", "")
    host_bundle_storage = ensure_absolute(
        console,
        "Host bundle local storage path",
        env_main.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1],
        defaults.get("host_bundle_storage"),
        force_prompt=force_prompt,
    )
    host_exec = ensure_absolute(
        console,
        "Host exec workspace path",
        env_main.entries.get("HOST_EXEC_WORKSPACE_PATH", (None, None))[1],
        defaults.get("host_exec_workspace"),
        force_prompt=force_prompt,
    )

    update_env_value(env_main, "HOST_KDCUBE_STORAGE_PATH", host_storage)
    update_env_value(env_main, "HOST_BUNDLES_PATH", host_bundles)
    update_env_value(env_main, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)
    update_env_value(env_main, "HOST_EXEC_WORKSPACE_PATH", host_exec)
    # Always align compose paths to the selected workdir.
    update_env_value(env_main, "KDCUBE_CONFIG_DIR", str(ctx.config_dir))
    update_env_value(env_main, "KDCUBE_DATA_DIR", str(ctx.data_dir))
    # Always keep logs in the workdir for compose mounts.
    update_env_value(env_main, "KDCUBE_LOGS_DIR", str(ctx.workdir / "logs"))
    if is_placeholder(env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_main, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_main.entries.get("BUNDLE_STORAGE_ROOT", (None, None))[1]):
        update_env_value(env_main, "BUNDLE_STORAGE_ROOT", "/bundle-storage")

    current_descriptor = env_main.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]
    descriptor_value = (current_descriptor or "").strip().strip("'\"")
    if force_prompt or is_placeholder(current_descriptor) or descriptor_value in {"", "/dev/null"}:
        use_descriptor = ask_confirm(console, "Use a custom bundle descriptor (release.yaml)?", default=False)
        if use_descriptor:
            if force_prompt:
                descriptor = prompt_optional_keep(console, "Host bundle descriptor path (release.yaml)", current_descriptor)
            else:
                descriptor = prompt_optional(console, "Host bundle descriptor path (release.yaml)")
            if descriptor:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", descriptor)
            else:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")
        else:
            update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")

    # If a descriptor is set, force a one-time registry sync on startup.
    current_descriptor = env_main.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]
    descriptor_value = (current_descriptor or "").strip().strip("'\"")
    if descriptor_value and descriptor_value != "/dev/null" and not is_placeholder(current_descriptor):
        update_env_value(env_proc, "BUNDLES_FORCE_ENV_ON_STARTUP", "1")
        update_env_value(env_proc, "BUNDLE_GIT_RESOLUTION_ENABLED", "1")
    else:
        update_env_value(env_proc, "BUNDLES_FORCE_ENV_ON_STARTUP", "0")

    existing_http = env_proc.entries.get("GIT_HTTP_TOKEN", (None, None))[1]
    existing_ssh = env_proc.entries.get("GIT_SSH_KEY_PATH", (None, None))[1]
    if existing_http and not is_placeholder(existing_http):
        console.print(
            "[yellow]Found GIT_HTTP_TOKEN in .env.proc; it will be cleared and treated as runtime-only.[/yellow]"
        )
        update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        existing_http = None
    if not is_placeholder(existing_http):
        default_auth = "https-token"
    elif not is_placeholder(existing_ssh):
        default_auth = "ssh"
    else:
        default_auth = "skip"

    auth_options = ["ssh", "https-token", "skip"]
    try:
        default_idx = auth_options.index(default_auth)
    except ValueError:
        default_idx = 0
    console.print("[bold]Git bundle authentication[/bold]")
    auth_choice = select_option(
        console,
        "Git auth method for private bundles",
        options=auth_options,
        default_index=default_idx,
    )
    if auth_choice == "ssh":
        if force_prompt or is_placeholder(env_main.entries.get("HOST_GIT_SSH_KEY_PATH", (None, None))[1]):
            ssh_key = prompt_optional(console, "Host SSH key path for git bundles")
            update_env_value(env_main, "HOST_GIT_SSH_KEY_PATH", ssh_key or "/dev/null")
        if force_prompt or is_placeholder(env_main.entries.get("HOST_GIT_KNOWN_HOSTS_PATH", (None, None))[1]):
            known_hosts = prompt_optional(console, "Host known_hosts path for git bundles")
            update_env_value(env_main, "HOST_GIT_KNOWN_HOSTS_PATH", known_hosts or "/dev/null")

        update_if_placeholder(env_proc, "GIT_SSH_KEY_PATH", "/run/secrets/git_ssh_key")
        update_if_placeholder(env_proc, "GIT_SSH_KNOWN_HOSTS", "/run/secrets/git_known_hosts")
        update_if_placeholder(env_proc, "GIT_SSH_STRICT_HOST_KEY_CHECKING", "yes")
        # Clear HTTPS token if placeholder
        if is_placeholder(env_proc.entries.get("GIT_HTTP_TOKEN", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        if is_placeholder(env_proc.entries.get("GIT_HTTP_USER", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_USER", "")
    elif auth_choice == "https-token":
        console.print("[dim]Create a GitHub token at https://github.com/settings/tokens[/dim]")
        token = prompt_secret_value(
            console,
            "Git HTTPS token",
            required=True,
            force_prompt=True,
        )
        if token:
            runtime_secrets["GIT_HTTP_TOKEN"] = token
        # Never store the token in env files.
        update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        # Avoid dangling SSH placeholders if user chose token
        if is_placeholder(env_proc.entries.get("GIT_SSH_KEY_PATH", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_KEY_PATH", "")
        if is_placeholder(env_proc.entries.get("GIT_SSH_KNOWN_HOSTS", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_KNOWN_HOSTS", "")
        if is_placeholder(env_proc.entries.get("GIT_SSH_STRICT_HOST_KEY_CHECKING", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_STRICT_HOST_KEY_CHECKING", "")
        if is_placeholder(env_proc.entries.get("GIT_HTTP_USER", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_USER", "")
        # If host SSH paths are placeholders, disable mounts to avoid missing-path binds.
        if is_placeholder(env_main.entries.get("HOST_GIT_SSH_KEY_PATH", (None, None))[1]):
            update_env_value(env_main, "HOST_GIT_SSH_KEY_PATH", "/dev/null")
        if is_placeholder(env_main.entries.get("HOST_GIT_KNOWN_HOSTS_PATH", (None, None))[1]):
            update_env_value(env_main, "HOST_GIT_KNOWN_HOSTS_PATH", "/dev/null")

    bundles_json = env_proc.entries.get("AGENTIC_BUNDLES_JSON", (None, None))[1]
    if should_replace_bundles_config(bundles_json):
        update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/release.yaml")

    if is_placeholder(env_proc.entries.get("KDCUBE_STORAGE_PATH", (None, None))[1]):
        update_env_value(env_proc, "KDCUBE_STORAGE_PATH", "/kdcube-storage")
    if is_placeholder(env_proc.entries.get("CB_BUNDLE_STORAGE_URL", (None, None))[1]):
        update_env_value(env_proc, "CB_BUNDLE_STORAGE_URL", "/kdcube-storage")
    if is_placeholder(env_proc.entries.get("BUNDLE_STORAGE_ROOT", (None, None))[1]):
        update_env_value(env_proc, "BUNDLE_STORAGE_ROOT", "/bundle-storage")
    if is_placeholder(env_proc.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_proc, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_proc.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1]):
        update_env_value(env_proc, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)

    # For compose installs, always use the container log path.
    update_env_value(env_ingress, "LOG_DIR", "/logs")
    update_env_value(env_proc, "LOG_DIR", "/logs")

    ui_build_context = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    default_ui_context = defaults.get("ui_build_context", "")
    if is_placeholder(ui_build_context):
        update_env_value(env_main, "UI_BUILD_CONTEXT", default_ui_context)
    else:
        normalized_current = _normalize_path(ui_build_context)
        normalized_default = _normalize_path(default_ui_context)
        if normalized_default and normalized_current and normalized_current != normalized_default:
            if ".kdcube/kdcube-ai-app" in normalized_current:
                update_env_value(env_main, "UI_BUILD_CONTEXT", default_ui_context)

    for key, default_key in [
        ("UI_DOCKERFILE_PATH", "ui_dockerfile_path"),
        ("UI_SOURCE_PATH", "ui_source_path"),
        ("NGINX_UI_CONFIG_FILE_PATH", "nginx_ui_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            update_env_value(env_main, key, defaults.get(default_key, ""))

    if auth_provider == "simple":
        frontend_template = ctx.ai_app_root / "deployment/docker/all_in_one_kdcube/frontend/config.hardcoded.json"
        compose_ui_config = ctx.config_dir / "frontend.config.hardcoded.json"
        defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
    else:
        frontend_template = ctx.ai_app_root / "deployment/docker/all_in_one_kdcube/frontend/config.cognito.json"
        compose_ui_config = ctx.config_dir / "frontend.config.cognito.json"
        defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
    cognito_region_val = env_ingress.entries.get("COGNITO_REGION", (None, None))[1]
    cognito_user_pool_id_val = env_ingress.entries.get("COGNITO_USER_POOL_ID", (None, None))[1]
    cognito_app_client_id_val = env_ingress.entries.get("COGNITO_APP_CLIENT_ID", (None, None))[1]
    write_frontend_config(
        compose_ui_config,
        tenant,
        project,
        template_path=frontend_template,
        cognito_region=cognito_region_val,
        cognito_user_pool_id=cognito_user_pool_id_val,
        cognito_app_client_id=cognito_app_client_id_val,
    )
    routes_prefix = normalize_routes_prefix(_load_json_file(compose_ui_config).get("routesPrefix"))
    runtime_proxy_path = env_main.entries.get("NGINX_PROXY_RUNTIME_CONFIG_PATH", (None, None))[1]
    desired_runtime_path = str((ctx.config_dir / Path(defaults["nginx_proxy_config"]).name).resolve())
    if is_placeholder(runtime_proxy_path) or not runtime_proxy_path:
        runtime_proxy_path = desired_runtime_path
        update_env_value(env_main, "NGINX_PROXY_RUNTIME_CONFIG_PATH", runtime_proxy_path)
    else:
        normalized_current = _normalize_path(runtime_proxy_path)
        normalized_desired = _normalize_path(desired_runtime_path)
        if normalized_current and normalized_desired and normalized_current != normalized_desired:
            runtime_proxy_path = desired_runtime_path
            update_env_value(env_main, "NGINX_PROXY_RUNTIME_CONFIG_PATH", runtime_proxy_path)
    runtime_proxy = Path(runtime_proxy_path).expanduser()
    sync_nginx_proxy_config(runtime_proxy, ctx.ai_app_root, defaults["nginx_proxy_config"])
    update_nginx_routes_prefix(runtime_proxy, routes_prefix)
    desired_frontend_path = str(compose_ui_config)
    current_frontend_path = env_main.entries.get("PATH_TO_FRONTEND_CONFIG_JSON", (None, None))[1]
    if is_placeholder(current_frontend_path) or not current_frontend_path:
        current_frontend_path = desired_frontend_path
        update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", current_frontend_path)
    else:
        normalized_current = _normalize_path(current_frontend_path)
        normalized_desired = _normalize_path(desired_frontend_path)
        if normalized_current and normalized_desired and normalized_current != normalized_desired:
            current_frontend_path = desired_frontend_path
            update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", current_frontend_path)

    # Keep the configured frontend config in sync.
    try:
        write_frontend_config(
            Path(current_frontend_path).expanduser(),
            tenant,
            project,
            template_path=frontend_template,
            cognito_region=cognito_region_val,
            cognito_user_pool_id=cognito_user_pool_id_val,
            cognito_app_client_id=cognito_app_client_id_val,
        )
    except Exception:
        pass

    if auth_provider == "simple":
        dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.hardcoded.json"
    else:
        dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.cognito.demo.json"
    write_frontend_config(
        dev_ui_config,
        tenant,
        project,
        template_path=frontend_template,
        cognito_region=cognito_region_val,
        cognito_user_pool_id=cognito_user_pool_id_val,
        cognito_app_client_id=cognito_app_client_id_val,
    )

    proxy_build_context = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    default_proxy_context = defaults.get("proxy_build_context", "")
    if is_placeholder(proxy_build_context):
        update_env_value(env_main, "PROXY_BUILD_CONTEXT", default_proxy_context)
    else:
        normalized_current = _normalize_path(proxy_build_context)
        normalized_default = _normalize_path(default_proxy_context)
        if normalized_default and normalized_current and normalized_current != normalized_default:
            if ".kdcube/kdcube-ai-app" in normalized_current:
                update_env_value(env_main, "PROXY_BUILD_CONTEXT", default_proxy_context)

    for key, default_key in [
        ("PROXY_DOCKERFILE_PATH", "proxy_dockerfile_path"),
        ("NGINX_PROXY_CONFIG_FILE_PATH", "nginx_proxy_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            default_value = defaults.get(default_key, "")
            if default_value:
                update_env_value(env_main, key, default_value)
            else:
                update_env_value(env_main, key, ask(console, f"{key} (relative to PROXY_BUILD_CONTEXT)"))

    save_env_file(env_main)
    save_env_file(env_ingress)
    save_env_file(env_proc)
    save_env_file(env_metrics)
    save_env_file(env_pg)
    save_env_file(env_proxy)

    return {
        ".env": str(env_main.path),
        ".env.ingress": str(env_ingress.path),
        ".env.proc": str(env_proc.path),
        ".env.metrics": str(env_metrics.path),
        ".env.postgres.setup": str(env_pg.path),
        ".env.proxylogin": str(env_proxy.path),
    }, runtime_secrets


def run_setup(
    console: Console,
    *,
    repo_root: Optional[Path] = None,
    workdir: Optional[Path] = None,
    install_mode: Optional[str] = None,
    release_ref: Optional[str] = None,
    docker_namespace: Optional[str] = None,
) -> None:
    install_mode = (install_mode or os.getenv("KDCUBE_INSTALL_MODE", "upstream")).strip().lower()
    env_release_ref = os.getenv("KDCUBE_RELEASE_REF", "").strip()
    env_docker_namespace = os.getenv("KDCUBE_DOCKER_NAMESPACE", "").strip()
    if not release_ref and env_release_ref:
        release_ref = env_release_ref
    if not docker_namespace and env_docker_namespace:
        docker_namespace = env_docker_namespace

    if repo_root is not None:
        repo_root = repo_root.expanduser().resolve()
        ai_app_root = repo_root / "app/ai-app"
        if not (ai_app_root / "deployment/docker/all_in_one_kdcube/docker-compose.yaml").exists():
            raise FileNotFoundError(
                f"Could not find deployment/docker/all_in_one_kdcube under {ai_app_root}"
            )
        lib_root = ai_app_root / "services/kdcube-ai-app"
        if not (lib_root / "kdcube_ai_app").exists():
            raise FileNotFoundError(f"Could not locate kdcube_ai_app under {lib_root}")
    else:
        lib_root = discover_lib_root()
        ai_app_root = find_ai_app_root(lib_root)
        if ai_app_root is None:
            ai_app_root = prompt_for_ai_app_root(console)
        if lib_root is None:
            console.print("[yellow]Could not infer lib root; using ai-app root instead.[/yellow]")
            lib_root = ai_app_root

    docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    sample_env_dir = docker_dir / "sample_env"
    if not sample_env_dir.exists():
        raise FileNotFoundError(f"Missing sample_env at {sample_env_dir}")

    if workdir is None:
        workdir_env = os.getenv("KDCUBE_WORKDIR", "").strip()
        if workdir_env:
            workdir = Path(workdir_env).expanduser().resolve()
            console.print(f"[dim]Using workdir from environment:[/dim] {workdir}")
        else:
            default_workdir = str(Path.home() / ".kdcube" / "kdcube-runtime")
            workdir = Path(
                ask(console, "Compose workdir (config+data root)", default=default_workdir)
            ).expanduser().resolve()
    else:
        workdir = workdir.expanduser().resolve()

    config_dir = workdir / "config"
    data_dir = workdir / "data"
    logs_dir = workdir / "logs"

    ctx = PathsContext(
        lib_root=lib_root,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=sample_env_dir,
        workdir=workdir,
        config_dir=config_dir,
        data_dir=data_dir,
    )

    ensure_env_files(config_dir, sample_env_dir)
    ensure_nginx_configs(config_dir, ai_app_root)
    ensure_local_dirs(data_dir, logs_dir)
    # Record installer metadata for future runs.
    try:
        meta = {
            "install_mode": install_mode or "upstream",
            "platform_ref": release_ref or "",
            "dockerhub_namespace": docker_namespace or "",
        }
        (config_dir / "install-meta.json").write_text(json.dumps(meta, indent=2))
    except Exception:
        pass
    env_paths, runtime_secrets = gather_configuration(console, ctx)
    env_main = load_env_file(config_dir / ".env")

    console.print("\n[bold]Env files:[/bold]")
    for name, path in env_paths.items():
        console.print(f"  {name}: {path}")
    console.print("\n[dim]Review/edit these files before building images if needed.[/dim]")
    console.print("[dim]Build contexts (from .env):[/dim]")
    ui_ctx = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    proxy_ctx = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    console.print(f"  UI_BUILD_CONTEXT={ui_ctx}")
    console.print(f"  PROXY_BUILD_CONTEXT={proxy_ctx}")

    console.print("\n[dim]Small coffee break:[/dim] ☕\n")

    if install_mode == "release":
        console.print("[bold]Release mode[/bold]: pull prebuilt images from DockerHub.")
        if not docker_namespace:
            docker_namespace = "kdcube"
        tag = release_ref or ask(console, "Release version (platform.ref)")
        if ask_confirm(console, f"Pull platform images ({docker_namespace}, tag {tag})?", default=True):
            images = [
                "kdcube-chat-ingress",
                "kdcube-chat-proc",
                "kdcube-metrics",
                "kdcube-postgres-setup",
                "kdcube-web-ui",
                "kdcube-web-proxy",
                "kdcube-secrets",
                "proxylogin",
                "py-code-exec",
            ]
            try:
                for image in images:
                    subprocess.run(
                        ["docker", "pull", f"{docker_namespace}/{image}:{tag}"],
                        check=True,
                    )
                    subprocess.run(
                        ["docker", "tag", f"{docker_namespace}/{image}:{tag}", f"{image}:latest"],
                        check=True,
                    )
            except FileNotFoundError:
                console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
            except subprocess.CalledProcessError:
                console.print("[red]Docker pull/tag failed. Check the output and retry.[/red]")
    else:
        if ask_confirm(
            console,
            "Build core platform images (includes py-code-exec)?",
            default=False,
        ):
            missing = missing_build_keys(env_main)
            if missing:
                console.print("[yellow]Skipping build — missing required build settings in .env:[/yellow]")
                for key in missing:
                    console.print(f"  - {key}")
                console.print("[yellow]Fill these in .env and rerun the build step.[/yellow]")
            else:
                try:
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--env-file",
                            str(config_dir / ".env"),
                            "build",
                            "chat-ingress",
                            "chat-proc",
                            "metrics",
                            "web-ui",
                            "web-proxy",
                            "postgres-setup",
                            "kdcube-secrets",
                        ],
                        cwd=ctx.docker_dir,
                        check=True,
                        env=compose_env(config_dir / ".env"),
                    )
                except FileNotFoundError:
                    console.print("[red]Docker not found. Please install Docker and rerun the build step.[/red]")
                except subprocess.CalledProcessError:
                    console.print("[red]Docker compose build failed. Check the output and retry.[/red]")
                try:
                    subprocess.run(
                        ["docker", "build", "-t", "py-code-exec:latest", "-f", "Dockerfile_Exec", "../../.."],
                        cwd=ctx.docker_dir,
                        check=True,
                    )
                except FileNotFoundError:
                    console.print("[red]Docker not found. Please install Docker and rerun the build step.[/red]")
                except subprocess.CalledProcessError:
                    console.print("[red]Docker build failed. Check the output and retry.[/red]")

    if ask_confirm(console, "Run docker compose now?", default=False):
        runtime_env = None
        try:
            token_overrides = generate_runtime_tokens()
            runtime_env = write_env_overlay(config_dir / ".env", token_overrides)
            base_cmd = [
                "docker",
                "compose",
                "--env-file",
                str(runtime_env),
            ]
            build_flag = ["--build"] if install_mode != "release" else []
            if runtime_secrets:
                # Start secrets service first so secrets are available before ingress/proc boot.
                subprocess.run(
                    [*base_cmd, "up", "-d", "--force-recreate", *build_flag, "kdcube-secrets"],
                    cwd=ctx.docker_dir,
                    check=True,
                    env=compose_env(runtime_env),
                )
                apply_runtime_secrets(console, ctx, runtime_secrets, runtime_env)

            services = list_compose_services(ctx, runtime_env)
            if runtime_secrets and services:
                services = [svc for svc in services if svc != "kdcube-secrets"]
            up_cmd = [*base_cmd, "up", "-d", "--force-recreate", *build_flag]
            if services:
                up_cmd.extend(services)
            subprocess.run(
                up_cmd,
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(runtime_env),
            )
            console.print("[green]Docker compose started.[/green]")
            console.print("Open the UI:")
            ui_port = env_main.entries.get("KDCUBE_UI_PORT", (None, None))[1] or "80"
            if ui_port == "80":
                proxy_url = "http://localhost/chatbot/chat"
            else:
                proxy_url = f"http://localhost:{ui_port}/chatbot/chat"
            console.print(f"  [link={proxy_url}]{proxy_url}[/link]")
        except FileNotFoundError:
            console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
        except subprocess.CalledProcessError:
            console.print("[red]Docker compose up failed. Check the output and retry.[/red]")
        finally:
            if runtime_env and runtime_env.exists():
                runtime_env.unlink(missing_ok=True)
    elif runtime_secrets:
        console.print(
            "[yellow]LLM secrets were provided but docker compose was not started. "
            "Start compose and inject secrets using the secrets service.[/yellow]"
        )


def main() -> None:
    console = Console()
    console.print(
        Panel.fit(
            "KDCube Platform Setup\nQuick-start Docker Compose wizard",
            title="kdcube-cli",
        )
    )
    console.print("[dim]Tip: type 'q' at any prompt to exit.[/dim]\n")

    try:
        run_setup(console)
    except SystemExit as exc:
        console.print(f"[yellow]{exc}[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
