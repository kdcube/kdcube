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


ENV_FILES = [".env", ".env.backend", ".env.ui.build"]


DEFAULT_BUNDLES_JSON = [
    "AGENTIC_BUNDLES_JSON='{",
    "  \"default_bundle_id\": \"with.codegen\",",
    "  \"bundles\": {",
    "        \"with.codegen.demo\": {",
    "          \"id\": \"with.codegen\",",
    "          \"name\": \"Security Leader Marketing Chatbot\",",
    "          \"path\": \"/bundles\",",
    "          \"module\": \"codegen.entrypoint\",",
    "          \"singleton\": false,",
    "          \"description\": \"Security Leader Marketing Chatbott\"",
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
        compose = candidate / "deployment/docker/all_in_one/docker-compose.yaml"
        if compose.exists():
            return candidate

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        compose = parent / "deployment/docker/all_in_one/docker-compose.yaml"
        if compose.exists():
            return compose.parents[3]
    return None


def prompt_for_ai_app_root(console: Console) -> Path:
    while True:
        raw = Prompt.ask("Path to ai-app root (contains deployment/docker/all_in_one)")
        candidate = Path(raw).expanduser().resolve()
        compose = candidate / "deployment/docker/all_in_one/docker-compose.yaml"
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
    docker_dir = ai_app_root / "deployment/docker/all_in_one"
    repo_root = ai_app_root.parents[1]
    customer_repo = repo_root.parent / "ai-customers-solutions"
    if not customer_repo.exists():
        customer_repo = None

    defaults: Dict[str, str] = {
        "docker_dir": str(docker_dir),
        "host_kb_storage": str(docker_dir / "data/kdcube-storage"),
        "host_exec_workspace": str(docker_dir / "data/exec-workspace"),
        "host_bundles": str(lib_root / "kdcube_ai_app/apps/chat/sdk/examples/bundles"),
        "ui_dockerfile_path": "ops/cicd/customer-c/local/Dockerfile_UI",
        "ui_source_path": "customer-c/ui",
        "ui_env_build_relative": "ops/cicd/customer-c/local/.env.ui.build",
        "nginx_ui_config": "ops/cicd/customer-c/local/nginx_ui.conf",
    }

    common_parent = repo_root.parent
    defaults["proxy_build_context"] = str(common_parent)
    defaults["proxy_dockerfile_path"] = str(
        (ai_app_root / "deployment/docker/all_in_one/Dockerfile_Proxy").relative_to(common_parent)
    )

    if customer_repo:
        defaults["ui_build_context"] = str(customer_repo)
        defaults["ui_env_file_path"] = str(customer_repo / "ops/cicd/customer-c/local/.env.ui.build")

        common_parent = Path(os.path.commonpath([repo_root, customer_repo]))
        defaults["proxy_build_context"] = str(common_parent)
        defaults["proxy_dockerfile_path"] = str(
            (ai_app_root / "deployment/docker/all_in_one/Dockerfile_Proxy").relative_to(common_parent)
        )
        defaults["nginx_proxy_config"] = str(
            (customer_repo / "ops/cicd/customer-c/local/nginx_proxy.conf").relative_to(common_parent)
        )

    return defaults


def should_replace_bundles_config(value: Optional[str]) -> bool:
    if is_placeholder(value):
        return True
    if value and ("kdcube.demo.1" in value or "<customer>" in value):
        return True
    return False


def gather_configuration(console: Console, ctx: PathsContext) -> Dict[str, str]:
    env_main = load_env_file(ctx.docker_dir / ".env")
    env_backend = load_env_file(ctx.docker_dir / ".env.backend")
    env_ui = load_env_file(ctx.docker_dir / ".env.ui.build")

    defaults = compute_paths(ctx.ai_app_root, ctx.lib_root)

    project = env_backend.entries.get("DEFAULT_PROJECT_NAME", (None, None))[1]
    tenant = env_backend.entries.get("DEFAULT_TENANT", (None, None))[1]

    if is_placeholder(project):
        project = Prompt.ask("Project name", default="demo-project")
    if is_placeholder(tenant):
        tenant = Prompt.ask("Tenant ID", default="demo-tenant")

    update_env_value(env_backend, "DEFAULT_PROJECT_NAME", project)
    update_env_value(env_backend, "DEFAULT_TENANT", tenant)
    update_env_value(env_backend, "TENANT_ID", tenant)

    update_env_value(env_ui, "CHAT_WEB_APP_DEFAULT_TENANT", tenant)
    update_env_value(env_ui, "CHAT_WEB_APP_DEFAULT_PROJECT", project)
    update_env_value(env_ui, "CHAT_WEB_APP_PROJECT", project)

    if is_placeholder(env_backend.entries.get("POSTGRES_USER", (None, None))[1]):
        pg_user = Prompt.ask("Postgres user", default="kdcube")
        update_env_value(env_backend, "POSTGRES_USER", pg_user)
    if is_placeholder(env_backend.entries.get("POSTGRES_PASSWORD", (None, None))[1]):
        pg_pass = Prompt.ask("Postgres password", password=True)
        update_env_value(env_backend, "POSTGRES_PASSWORD", pg_pass)
    if is_placeholder(env_backend.entries.get("REDIS_PASSWORD", (None, None))[1]):
        redis_pass = Prompt.ask("Redis password", password=True)
        update_env_value(env_backend, "REDIS_PASSWORD", redis_pass)

    storage_value = env_backend.entries.get("KDCUBE_STORAGE_PATH", (None, None))[1]
    storage_backend = "s3" if storage_value and storage_value.startswith("s3://") else "local"
    if is_placeholder(storage_value):
        storage_backend = Prompt.ask(
            "Storage backend", choices=["local", "s3"], default="local"
        )

    if storage_backend == "local":
        update_env_value(env_backend, "KDCUBE_STORAGE_PATH", "file:///kdcube-storage")
    else:
        if is_placeholder(storage_value):
            s3_path = Prompt.ask("S3 storage path (s3://bucket/path)")
            update_env_value(env_backend, "KDCUBE_STORAGE_PATH", s3_path)
        aws_region_value = env_backend.entries.get("AWS_REGION", (None, None))[1]
        if is_placeholder(aws_region_value):
            aws_region = Prompt.ask("AWS region")
            update_env_value(env_backend, "AWS_REGION", aws_region)
            update_env_value(env_backend, "AWS_DEFAULT_REGION", aws_region)
        elif is_placeholder(env_backend.entries.get("AWS_DEFAULT_REGION", (None, None))[1]):
            update_env_value(env_backend, "AWS_DEFAULT_REGION", aws_region_value)

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
    update_env_value(env_backend, "HOST_KDCUBE_STORAGE_PATH", host_storage)
    update_env_value(env_backend, "HOST_BUNDLES_PATH", host_bundles)
    update_env_value(env_backend, "HOST_EXEC_WORKSPACE_PATH", host_exec)

    if is_placeholder(env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_main, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_backend.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_backend, "AGENTIC_BUNDLES_ROOT", "/bundles")

    openai_key = env_backend.entries.get("OPENAI_API_KEY", (None, None))[1]
    anthropic_key = env_backend.entries.get("ANTHROPIC_API_KEY", (None, None))[1]
    if is_placeholder(openai_key) and is_placeholder(anthropic_key):
        openai_key = prompt_optional(console, "OpenAI API key", secret=True)
        if openai_key:
            update_env_value(env_backend, "OPENAI_API_KEY", openai_key)
        else:
            anthropic_key = Prompt.ask("Anthropic API key", password=True)
            update_env_value(env_backend, "ANTHROPIC_API_KEY", anthropic_key)

    if is_placeholder(env_backend.entries.get("BRAVE_API_KEY", (None, None))[1]):
        brave_key = prompt_optional(console, "Brave API key", secret=True)
        if brave_key:
            update_env_value(env_backend, "BRAVE_API_KEY", brave_key)

    if should_replace_bundles_config(env_backend.entries.get("AGENTIC_BUNDLES_JSON", (None, None))[1]):
        replace_multiline_block(env_backend, "AGENTIC_BUNDLES_JSON", DEFAULT_BUNDLES_JSON)

    ui_build_context = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(ui_build_context):
        ui_build_context = ensure_absolute(
            console,
            "UI build context (customer repo root)",
            ui_build_context,
            defaults.get("ui_build_context"),
        )
        update_env_value(env_main, "UI_BUILD_CONTEXT", ui_build_context)

    ui_env_file_path = env_main.entries.get("UI_ENV_FILE_PATH", (None, None))[1]
    if is_placeholder(ui_env_file_path):
        ui_env_file_path = ensure_absolute(
            console,
            "UI env file path",
            ui_env_file_path,
            defaults.get("ui_env_file_path"),
        )
        update_env_value(env_main, "UI_ENV_FILE_PATH", ui_env_file_path)

    for key, default_key in [
        ("UI_DOCKERFILE_PATH", "ui_dockerfile_path"),
        ("UI_SOURCE_PATH", "ui_source_path"),
        ("UI_ENV_BUILD_RELATIVE", "ui_env_build_relative"),
        ("NGINX_UI_CONFIG_FILE_PATH", "nginx_ui_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            update_env_value(env_main, key, defaults.get(default_key, ""))

    proxy_build_context = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    if is_placeholder(proxy_build_context):
        proxy_build_context = ensure_absolute(
            console,
            "Proxy build context (common parent for platform + customer repos)",
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
    save_env_file(env_backend)
    save_env_file(env_ui)

    return {
        ".env": str(env_main.path),
        ".env.backend": str(env_backend.path),
        ".env.ui.build": str(env_ui.path),
    }


def main() -> None:
    console = Console()
    console.print(
        Panel.fit(
            "KDCube Chatbot Setup\nQuick-start Docker Compose wizard",
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

    docker_dir = ai_app_root / "deployment/docker/all_in_one"
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
