# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
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


def is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"")
    if not stripped:
        return True
    if "<" in stripped and ">" in stripped:
        return True
    if "..." in stripped:
        return True
    if "changeme" in stripped.lower():
        return True
    return False


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


def ensure_env_files(docker_dir: Path, sample_env_dir: Path) -> None:
    for env_name in ENV_FILES:
        target = docker_dir / env_name
        if target.exists():
            continue
        sample = sample_env_dir / env_name
        if not sample.exists():
            raise FileNotFoundError(f"Missing sample env file: {sample}")
        shutil.copyfile(sample, target)


def load_env_file(path: Path) -> EnvFile:
    lines = path.read_text().splitlines()
    entries = parse_env(lines)
    return EnvFile(path=path, lines=lines, entries=entries)


def save_env_file(env_file: EnvFile) -> None:
    text = "\n".join(env_file.lines).rstrip() + "\n"
    env_file.path.write_text(text)


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
        raw = Prompt.ask("Path to ai-app root (contains deployment/docker/all_in_one_kdcube)")
        candidate = Path(raw).expanduser().resolve()
        compose = candidate / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return candidate
        console.print("[red]Could not find docker-compose.yaml under that path.[/red]")


def prompt_optional(console: Console, label: str, secret: bool = False) -> str:
    console.print(f"{label} [dim](leave blank to skip)[/dim]")
    return console.input("> ", password=secret).strip()


def ensure_absolute(console: Console, label: str, current: Optional[str], default: Optional[str]) -> str:
    current_value = None if is_placeholder(current) else current
    if current_value and Path(current_value).is_absolute():
        return current_value
    while True:
        value = Prompt.ask(label, default=default or "")
        if not value:
            console.print("[red]Please provide a value.[/red]")
            continue
        resolved = Path(value).expanduser().resolve()
        return str(resolved)


def compute_paths(ai_app_root: Path, lib_root: Path) -> Dict[str, str]:
    docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    repo_root = ai_app_root
    defaults: Dict[str, str] = {
        "docker_dir": str(docker_dir),
        "host_kb_storage": str(docker_dir / "data/kdcube-storage"),
        "host_exec_workspace": str(docker_dir / "data/exec-workspace"),
        "host_bundles": str(lib_root / "kdcube_ai_app/apps/chat/sdk/examples/bundles"),
        "ui_dockerfile_path": "app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_UI",
        "ui_source_path": "app/ai-app/ui/chat-web-app",
        "ui_env_build_relative": "app/ai-app/ui/chat-web-app/.env.sample",
        "nginx_ui_config": "app/ai-app/deployment/docker/all_in_one_kdcube/nginx_ui.conf",
        "frontend_config_json": "app/ai-app/ui/chat-web-app/public/config.hardcoded.json",
    }

    common_parent = repo_root
    defaults["proxy_build_context"] = str(common_parent)
    defaults["proxy_dockerfile_path"] = str(
        (ai_app_root / "deployment/docker/all_in_one_kdcube/Dockerfile_Proxy").relative_to(common_parent)
    )
    defaults["ui_build_context"] = str(repo_root)
    defaults["ui_env_file_path"] = str(repo_root / "app/ai-app/ui/chat-web-app/.env")
    defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl.conf"
    return defaults


def should_replace_bundles_config(value: Optional[str]) -> bool:
    if is_placeholder(value):
        return True
    if value and ("kdcube.demo.1" in value or "<project>" in value):
        return True
    return False


def gather_configuration(console: Console, ctx: PathsContext) -> Dict[str, str]:
    env_main = load_env_file(ctx.docker_dir / ".env")
    env_ingress = load_env_file(ctx.docker_dir / ".env.ingress")
    env_proc = load_env_file(ctx.docker_dir / ".env.proc")
    env_metrics = load_env_file(ctx.docker_dir / ".env.metrics")
    env_pg = load_env_file(ctx.docker_dir / ".env.postgres.setup")

    defaults = compute_paths(ctx.ai_app_root, ctx.lib_root)

    tenant = Prompt.ask("Tenant ID", default="demo-tenant")
    project = Prompt.ask("Project name", default="demo-project")
    minimal_gateway_json = (
        f'{{"tenant":"{tenant}","project":"{project}","profile":"development"}}'
    )
    for env in (env_ingress, env_proc, env_metrics):
        if is_placeholder(env.entries.get("GATEWAY_CONFIG_JSON", (None, None))[1]):
            update_env_value(env, "GATEWAY_CONFIG_JSON", f"'{minimal_gateway_json}'")

    if is_placeholder(env_pg.entries.get("POSTGRES_USER", (None, None))[1]):
        pg_user = Prompt.ask("Postgres user", default="postgres")
        update_env_value(env_pg, "POSTGRES_USER", pg_user)
        update_env_value(env_ingress, "POSTGRES_USER", pg_user)
        update_env_value(env_proc, "POSTGRES_USER", pg_user)
    if is_placeholder(env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]):
        pg_pass = Prompt.ask("Postgres password", password=True)
        update_env_value(env_pg, "POSTGRES_PASSWORD", pg_pass)
        update_env_value(env_ingress, "POSTGRES_PASSWORD", pg_pass)
        update_env_value(env_proc, "POSTGRES_PASSWORD", pg_pass)
    if is_placeholder(env_main.entries.get("REDIS_PASSWORD", (None, None))[1]):
        redis_pass = Prompt.ask("Redis password", password=True)
        update_env_value(env_main, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_ingress, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_proc, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_metrics, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_ingress, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_env_value(env_proc, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")
        update_env_value(env_metrics, "REDIS_URL", f"redis://:{redis_pass}@redis:6379/0")

    if is_placeholder(env_ingress.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_ingress, "POSTGRES_HOST", "postgres-db")
    if is_placeholder(env_proc.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_proc, "POSTGRES_HOST", "postgres-db")

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
    host_exec = ensure_absolute(
        console,
        "Host exec workspace path",
        env_main.entries.get("HOST_EXEC_WORKSPACE_PATH", (None, None))[1],
        defaults.get("host_exec_workspace"),
    )

    update_env_value(env_main, "HOST_KDCUBE_STORAGE_PATH", host_storage)
    update_env_value(env_main, "HOST_BUNDLES_PATH", host_bundles)
    update_env_value(env_main, "HOST_EXEC_WORKSPACE_PATH", host_exec)
    if is_placeholder(env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_main, "AGENTIC_BUNDLES_ROOT", "/bundles")

    if should_replace_bundles_config(env_proc.entries.get("AGENTIC_BUNDLES_JSON", (None, None))[1]):
        replace_multiline_block(env_proc, "AGENTIC_BUNDLES_JSON", DEFAULT_BUNDLES_JSON)

    ui_build_context = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(ui_build_context):
        ui_build_context = ensure_absolute(
            console,
            "UI build context (platform repo root)",
            ui_build_context,
            defaults.get("ui_build_context"),
        )
        update_env_value(env_main, "UI_BUILD_CONTEXT", ui_build_context)

    for key, default_key in [
        ("UI_DOCKERFILE_PATH", "ui_dockerfile_path"),
        ("UI_SOURCE_PATH", "ui_source_path"),
        ("NGINX_UI_CONFIG_FILE_PATH", "nginx_ui_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            update_env_value(env_main, key, defaults.get(default_key, ""))

    if is_placeholder(env_main.entries.get("PATH_TO_FRONTEND_CONFIG_JSON", (None, None))[1]):
        update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", defaults.get("frontend_config_json", ""))

    proxy_build_context = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(proxy_build_context):
        proxy_build_context = ensure_absolute(
            console,
            "Proxy build context (platform repo root)",
            proxy_build_context,
            defaults.get("proxy_build_context"),
        )
        update_env_value(env_main, "PROXY_BUILD_CONTEXT", proxy_build_context)

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
                update_env_value(env_main, key, Prompt.ask(f"{key} (relative to PROXY_BUILD_CONTEXT)"))

    save_env_file(env_main)
    save_env_file(env_ingress)
    save_env_file(env_proc)
    save_env_file(env_metrics)
    save_env_file(env_pg)

    return {
        ".env": str(env_main.path),
        ".env.ingress": str(env_ingress.path),
        ".env.proc": str(env_proc.path),
        ".env.metrics": str(env_metrics.path),
        ".env.postgres.setup": str(env_pg.path),
    }


def main() -> None:
    console = Console()
    console.print(
        Panel.fit(
            "KDCube Platform Setup\nQuick-start Docker Compose wizard",
            title="kdcube-cli",
        )
    )

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

    ctx = PathsContext(
        lib_root=lib_root,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=sample_env_dir,
    )

    ensure_env_files(docker_dir, sample_env_dir)
    env_paths = gather_configuration(console, ctx)

    table = Table(show_header=True, header_style="bold")
    table.add_column("File")
    table.add_column("Location")
    for name, path in env_paths.items():
        table.add_row(name, path)
    console.print(table)

    if Confirm.ask("Build core platform images (ingress/proc/metrics/ui/proxy/postgres-setup)?", default=True):
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
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

    if Confirm.ask("Build the code execution image (py-code-exec:latest)?", default=True):
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

    if Confirm.ask("Run docker compose now?", default=False):
        console.print("Run this from the docker folder:")
        console.print(f"  cd {docker_dir}")
        console.print("  docker compose up -d --build")


if __name__ == "__main__":
    main()
