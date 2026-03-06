# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table


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
        if tenant == "<TENANT_ID>":
            tenant = None
        if project == "<PROJECT_ID>":
            project = None
        return tenant, project
    except json.JSONDecodeError:
        tenant_match = re.search(r'"tenant"\s*:\s*"([^"]+)"', json_text)
        project_match = re.search(r'"project"\s*:\s*"([^"]+)"', json_text)
        tenant = tenant_match.group(1) if tenant_match else None
        project = project_match.group(1) if project_match else None
        if tenant == "<TENANT_ID>":
            tenant = None
        if project == "<PROJECT_ID>":
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


def write_frontend_config(path: Path, tenant: str, project: str, token: str = "test-admin-token-123") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    else:
        data = {}

    data["tenant"] = tenant
    data["project"] = project
    if "tenant_id" in data:
        data["tenant_id"] = tenant
    if "project_id" in data:
        data["project_id"] = project
    data.setdefault("routesPrefix", "/chatbot")

    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    auth.setdefault("authType", "hardcoded")
    if auth.get("token") in (None, "", "test-admin-token-123"):
        auth["token"] = token
    data["auth"] = auth

    path.write_text(json.dumps(data, indent=2) + "\n")


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
    for name in ("nginx_ui.conf", "nginx_proxy.conf"):
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


def ensure_absolute(console: Console, label: str, current: Optional[str], default: Optional[str]) -> str:
    current_value = None if is_placeholder(current) else current
    if current_value and Path(current_value).is_absolute():
        return current_value
    while True:
        value = ask(console, label, default=default or "")
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
    required: bool = False,
) -> Optional[str]:
    current = env_file.entries.get(key, (None, None))[1]
    if not is_placeholder(current):
        return current
    if required:
        value = ask(console, label, secret=True)
    else:
        value = prompt_optional(console, label, secret=True)
    if value:
        update_env_value(env_file, key, value)
        console.print(f"{_label(label)}: [dim]{_mask(value)}[/]")
        return value
    return None


def compute_paths(ai_app_root: Path, lib_root: Path, workdir: Path) -> Dict[str, str]:
    docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    repo_root = ai_app_root.parent.parent
    defaults: Dict[str, str] = {
        "docker_dir": str(docker_dir),
        "host_kb_storage": str(workdir / "data/kdcube-storage"),
        "host_bundle_storage": str(workdir / "data/bundle-storage"),
        "host_exec_workspace": str(workdir / "data/exec-workspace"),
        "host_bundles": str(lib_root / "kdcube_ai_app/apps/chat/sdk/examples/bundles"),
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


def gather_configuration(console: Console, ctx: PathsContext) -> Dict[str, str]:
    env_main = load_env_file(ctx.config_dir / ".env")
    env_ingress = load_env_file(ctx.config_dir / ".env.ingress")
    env_proc = load_env_file(ctx.config_dir / ".env.proc")
    env_metrics = load_env_file(ctx.config_dir / ".env.metrics")
    env_pg = load_env_file(ctx.config_dir / ".env.postgres.setup")
    env_proxy = load_env_file(ctx.config_dir / ".env.proxylogin")

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

    pg_user = env_pg.entries.get("POSTGRES_USER", (None, None))[1]
    if is_placeholder(pg_user):
        pg_user = ask(console, "Postgres user", default="postgres")
        update_env_value(env_pg, "POSTGRES_USER", pg_user)
    update_if_placeholder(env_ingress, "POSTGRES_USER", pg_user or "postgres")
    update_if_placeholder(env_proc, "POSTGRES_USER", pg_user or "postgres")

    pg_pass = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]
    if is_placeholder(pg_pass):
        pg_pass = ask(console, "Postgres password", secret=True)
        console.print(f"{_label('Postgres password')}: [dim]{_mask(pg_pass)}[/]")
        update_env_value(env_pg, "POSTGRES_PASSWORD", pg_pass)
    update_if_placeholder(env_ingress, "POSTGRES_PASSWORD", pg_pass or "postgres")
    update_if_placeholder(env_proc, "POSTGRES_PASSWORD", pg_pass or "postgres")

    pg_db = env_pg.entries.get("POSTGRES_DATABASE", (None, None))[1]
    if is_placeholder(pg_db):
        pg_db = "kdcube"
        update_env_value(env_pg, "POSTGRES_DATABASE", pg_db)
    update_if_placeholder(env_ingress, "POSTGRES_DATABASE", pg_db or "kdcube")
    update_if_placeholder(env_proc, "POSTGRES_DATABASE", pg_db or "kdcube")
    update_if_placeholder(env_main, "PGUSER", pg_user or "postgres")
    update_if_placeholder(env_main, "PGPASSWORD", pg_pass or "postgres")
    update_if_placeholder(env_main, "PGDATABASE", pg_db or "kdcube")
    if is_placeholder(env_main.entries.get("REDIS_PASSWORD", (None, None))[1]):
        redis_pass = ask(console, "Redis password", secret=True)
        console.print(f"{_label('Redis password')}: [dim]{_mask(redis_pass)}[/]")
        update_env_value(env_main, "REDIS_PASSWORD", redis_pass)
    else:
        redis_pass = env_main.entries.get("REDIS_PASSWORD", (None, None))[1] or ""

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

    prompt_secret(console, env_proc, "OPENAI_API_KEY", "OpenAI API key", required=False)
    prompt_secret(console, env_proc, "ANTHROPIC_API_KEY", "Anthropic API key", required=False)
    prompt_secret(console, env_proc, "BRAVE_API_KEY", "Brave Search API key", required=False)

    host_storage = ensure_absolute(
        console,
        "Host KB storage path",
        env_main.entries.get("HOST_KDCUBE_STORAGE_PATH", (None, None))[1],
        defaults.get("host_kb_storage"),
    )
    host_bundles = ensure_absolute(
        console,
        "Host bundles path",
        env_main.entries.get("HOST_BUNDLES_PATH", (None, None))[1],
        defaults.get("host_bundles"),
    )
    host_bundle_storage = ensure_absolute(
        console,
        "Host bundle local storage path",
        env_main.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1],
        defaults.get("host_bundle_storage"),
    )
    host_exec = ensure_absolute(
        console,
        "Host exec workspace path",
        env_main.entries.get("HOST_EXEC_WORKSPACE_PATH", (None, None))[1],
        defaults.get("host_exec_workspace"),
    )

    update_env_value(env_main, "HOST_KDCUBE_STORAGE_PATH", host_storage)
    update_env_value(env_main, "HOST_BUNDLES_PATH", host_bundles)
    update_env_value(env_main, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)
    update_env_value(env_main, "HOST_EXEC_WORKSPACE_PATH", host_exec)
    update_if_placeholder(env_main, "KDCUBE_CONFIG_DIR", str(ctx.config_dir))
    update_if_placeholder(env_main, "KDCUBE_DATA_DIR", str(ctx.data_dir))
    update_if_placeholder(env_main, "KDCUBE_LOGS_DIR", str(ctx.workdir / "logs"))
    if is_placeholder(env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_main, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_main.entries.get("BUNDLE_STORAGE_ROOT", (None, None))[1]):
        update_env_value(env_main, "BUNDLE_STORAGE_ROOT", "/bundle-storage")

    if is_placeholder(env_main.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]):
        descriptor = prompt_optional(console, "Host bundle descriptor path (release.yaml)")
        update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", descriptor or "/dev/null")

    if is_placeholder(env_main.entries.get("HOST_GIT_SSH_KEY_PATH", (None, None))[1]):
        ssh_key = prompt_optional(console, "Host SSH key path for git bundles")
        update_env_value(env_main, "HOST_GIT_SSH_KEY_PATH", ssh_key or "/dev/null")
    if is_placeholder(env_main.entries.get("HOST_GIT_KNOWN_HOSTS_PATH", (None, None))[1]):
        known_hosts = prompt_optional(console, "Host known_hosts path for git bundles")
        update_env_value(env_main, "HOST_GIT_KNOWN_HOSTS_PATH", known_hosts or "/dev/null")

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
    if is_placeholder(env_proc.entries.get("HOST_BUNDLES_PATH", (None, None))[1]):
        update_env_value(env_proc, "HOST_BUNDLES_PATH", host_bundles)
    if is_placeholder(env_proc.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1]):
        update_env_value(env_proc, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)

    ui_build_context = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(ui_build_context):
        update_env_value(env_main, "UI_BUILD_CONTEXT", defaults.get("ui_build_context", ""))

    for key, default_key in [
        ("UI_DOCKERFILE_PATH", "ui_dockerfile_path"),
        ("UI_SOURCE_PATH", "ui_source_path"),
        ("NGINX_UI_CONFIG_FILE_PATH", "nginx_ui_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            update_env_value(env_main, key, defaults.get(default_key, ""))

    compose_ui_config = ctx.config_dir / "frontend.config.hardcoded.json"
    write_frontend_config(compose_ui_config, tenant, project)
    if is_placeholder(env_main.entries.get("PATH_TO_FRONTEND_CONFIG_JSON", (None, None))[1]):
        update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", str(compose_ui_config))

    dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.hardcoded.json"
    write_frontend_config(dev_ui_config, tenant, project)

    proxy_build_context = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(proxy_build_context):
        update_env_value(env_main, "PROXY_BUILD_CONTEXT", defaults.get("proxy_build_context", ""))

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
    }


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

        default_workdir = os.getenv("KDCUBE_WORKDIR") or str(Path.home() / ".kdcube" / "kdcube-runtime")
        workdir = Path(
            ask(console, "Compose workdir (config+data root)", default=default_workdir)
        ).expanduser().resolve()
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
        env_paths = gather_configuration(console, ctx)
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

        if ask_confirm(console, "Build core platform images (ingress/proc/metrics/ui/proxy/postgres-setup)?", default=False):
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
                        ],
                        cwd=ctx.docker_dir,
                        check=True,
                    )
                except FileNotFoundError:
                    console.print("[red]Docker not found. Please install Docker and rerun the build step.[/red]")
                except subprocess.CalledProcessError:
                    console.print("[red]Docker compose build failed. Check the output and retry.[/red]")

        if ask_confirm(console, "Build the code execution image (py-code-exec:latest)?", default=False):
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
            try:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        str(config_dir / ".env"),
                        "up",
                        "-d",
                        "--build",
                    ],
                    cwd=ctx.docker_dir,
                    check=True,
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
    except SystemExit as exc:
        console.print(f"[yellow]{exc}[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
