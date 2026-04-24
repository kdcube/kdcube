# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.control import Control
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from kdcube_cli.banner import print_cli_banner
from kdcube_cli import installer as installer_mod
from kdcube_cli.export_live_bundles import export_live_bundle_descriptors
from kdcube_cli.tty_keys import (
    KEY_DOWN,
    KEY_ENTER,
    KEY_EOF,
    KEY_ESCAPE,
    KEY_INTERRUPT,
    KEY_UP,
    read_tty_key,
)


DEFAULT_REPO = "https://github.com/kdcube/kdcube-ai-app.git"
DEFAULT_DIR = Path.home() / ".kdcube" / "kdcube-ai-app"
DEFAULT_WORKDIR = Path.home() / ".kdcube" / "kdcube-runtime"
DEFAULT_DEFAULTS_FILE = Path.home() / ".kdcube" / "cli-defaults.json"
DEFAULT_REPO_DIRNAME = "repo"
KDCUBE_REPOS = {
    "kdcube-chat-ingress",
    "kdcube-chat-proc",
    "kdcube-metrics",
    "kdcube-postgres-setup",
    "kdcube-secrets",
    "kdcube-web-ui",
    "kdcube-web-proxy",
    "proxylogin",
    "py-code-exec",
}


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def _docker_output(
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    cwd: Path | None = None,
) -> str:
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd) if cwd else None,
        ).stdout
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = "\n".join([line for line in [stdout, stderr] if line])
        raise SystemExit(f"Docker command failed: {' '.join(cmd)}\n{details}") from exc


def _docker_output_soft(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return None


def _docker_run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Docker command failed: {' '.join(cmd)} (exit {exc.returncode})") from exc


def _compose_env_from_cmd(cmd: list[str]) -> dict[str, str] | None:
    if "--env-file" not in cmd:
        return None
    idx = cmd.index("--env-file")
    if idx + 1 >= len(cmd):
        return None
    env = os.environ.copy()
    env["COMPOSE_ENV_FILES"] = cmd[idx + 1]
    return env


def _run_compose(console: Console, cmd: list[str], *, cwd: Path) -> None:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_compose_env_from_cmd(cmd))
    if proc.stdout:
        console.print(proc.stdout.strip())
    if proc.stderr:
        console.print(proc.stderr.strip())
    if proc.returncode != 0:
        raise SystemExit(f"Command failed with exit code {proc.returncode}.")


def _run_compose_optional(console: Console, cmd: list[str], *, cwd: Path, label: str) -> bool:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_compose_env_from_cmd(cmd))
    if proc.stdout:
        console.print(proc.stdout.strip())
    if proc.stderr:
        console.print(proc.stderr.strip())
    if proc.returncode != 0:
        console.print(f"[yellow]{label} (exit {proc.returncode}).[/yellow]")
        return False
    return True


def stop_compose_stack(
    console: Console,
    *,
    repo_root: Path,
    workdir: Path,
    remove_volumes: bool = False,
) -> None:
    ctx = _build_paths_for_repo(repo_root, workdir)
    env_file = ctx.config_dir / ".env"
    if not env_file.exists():
        raise SystemExit(
            f"Compose env file not found: {env_file}. "
            "Pass --workdir for the runtime you want to stop or re-run the installer first."
        )

    cmd = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "down",
        "--remove-orphans",
    ]
    if remove_volumes:
        cmd.append("-v")

    _run_compose(console, cmd, cwd=ctx.docker_dir)
    console.print("[green]Docker compose stopped.[/green]")
    console.print(f"[dim]Workdir:[/dim] {workdir}")
    if not remove_volumes:
        console.print("[dim]Host data under the workdir was preserved.[/dim]")


def clean_docker_images(console: Console) -> None:
    console.print("[bold]Cleaning Docker cache and unused KDCube images...[/bold]")
    try:
        # Remove dangling images + build cache
        _docker_run(["docker", "image", "prune", "-f"])
        _docker_run(["docker", "builder", "prune", "-f"])

        used_refs: set[str] = set()
        out = _docker_output_soft(["docker", "ps", "-a", "--format", "{{.ImageID}}"])
        if out is None:
            out = _docker_output(["docker", "ps", "-a", "--format", "{{.Image}}"])
        for line in out.splitlines():
            value = line.strip()
            if value:
                used_refs.add(value)

        images = _docker_output(
            ["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}} {{.Repository}} {{.Tag}}"]
        ).splitlines()

        to_remove: list[str] = []
        for line in images:
            parts = line.split(" ", 2)
            if len(parts) != 3:
                continue
            image_id, repo, tag = parts
            if tag == "<none>":
                continue
            if repo.startswith("kdcube/"):
                pass
            elif repo in KDCUBE_REPOS or repo.startswith("kdcube-"):
                pass
            else:
                continue
            if image_id in used_refs or f"{repo}:{tag}" in used_refs:
                continue
            to_remove.append(f"{repo}:{tag}")

        if to_remove:
            console.print("[dim]Removing old KDCube image tags:[/dim]")
            for ref in to_remove:
                console.print(f"  {ref}")
            subprocess.run(["docker", "rmi", *to_remove], check=False)
        else:
            console.print("[dim]No old KDCube image tags to remove.[/dim]")
    except FileNotFoundError:
        raise SystemExit("Docker not found. Please install Docker and retry.")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Docker cleanup failed with exit code {exc.returncode}.") from exc


def _build_paths_for_repo(repo_root: Path, workdir: Path) -> installer_mod.PathsContext:
    workdir = _resolve_cli_workdir(workdir)
    ai_app_root = repo_root / "app/ai-app"
    if not (ai_app_root / "deployment/docker/all_in_one_kdcube/docker-compose.yaml").exists():
        raise SystemExit(f"Could not find deployment/docker/all_in_one_kdcube under {ai_app_root}")
    lib_root = ai_app_root / "src/kdcube-ai-app"
    if not (lib_root / "kdcube_ai_app").exists():
        raise SystemExit(f"Could not locate kdcube_ai_app under {lib_root}")
    config_dir = workdir / "config"
    compose_mode = "all-in-one"
    env_main_path = config_dir / ".env"
    if env_main_path.exists():
        env_main = installer_mod.load_env_file(env_main_path)
        compose_mode_raw = env_main.entries.get("KDCUBE_COMPOSE_MODE", (None, None))[1]
        if _strip_env_value(compose_mode_raw) == "custom-ui-managed-infra":
            compose_mode = "custom-ui-managed-infra"
    if compose_mode == "custom-ui-managed-infra":
        docker_dir = ai_app_root / "deployment/docker/custom-ui-managed-infra"
    else:
        docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    return installer_mod.PathsContext(
        lib_root=lib_root,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=docker_dir / "sample_env",
        workdir=workdir,
        config_dir=config_dir,
        data_dir=workdir / "data",
    )


def _runtime_env_exists(workdir: Path) -> bool:
    return (workdir / "config" / ".env").exists()


def _runtime_candidates(base_workdir: Path) -> list[Path]:
    if not base_workdir.exists() or not base_workdir.is_dir():
        return []
    candidates: list[Path] = []
    for child in base_workdir.iterdir():
        if child.is_dir() and _runtime_env_exists(child):
            candidates.append(child.resolve())
    return sorted(candidates)


def _descriptor_context_hint(
    *,
    descriptors_location: Path | None = None,
    assembly_path: Path | None = None,
) -> tuple[str | None, str | None]:
    source: Path | None = None
    if descriptors_location is not None:
        source = descriptors_location / "assembly.yaml"
    elif assembly_path is not None:
        source = assembly_path
    assembly = installer_mod.load_release_descriptor_soft(source)
    return installer_mod.descriptor_context_from_assembly(assembly)


def _resolve_cli_workdir(
    workdir: Path,
    *,
    descriptors_location: Path | None = None,
    assembly_path: Path | None = None,
) -> Path:
    workdir = workdir.expanduser().resolve()
    if _runtime_env_exists(workdir) or (workdir / "config").exists():
        return workdir

    tenant_hint, project_hint = _descriptor_context_hint(
        descriptors_location=descriptors_location,
        assembly_path=assembly_path,
    )
    namespace = installer_mod.workspace_namespace(tenant_hint, project_hint)
    if workdir.name == namespace or "__" in workdir.name:
        return workdir

    if descriptors_location is not None or assembly_path is not None:
        return installer_mod.workspace_runtime_dir(workdir, tenant_hint, project_hint).resolve()

    candidates = _runtime_candidates(workdir)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise SystemExit(
            f"Multiple runtime workdirs found under {workdir}. "
            "Pass the namespaced runtime directory explicitly with --workdir."
        )
    return installer_mod.workspace_runtime_dir(workdir, tenant_hint, project_hint).resolve()


def _is_git_repo(path: Path) -> bool:
    return path.exists() and (path / ".git").is_dir()


def _default_repo_path_for_workdir(workdir: Path) -> Path:
    return workdir / DEFAULT_REPO_DIRNAME


def _read_install_meta_raw(workdir: Path) -> dict | None:
    meta_path = workdir / "config" / "install-meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return None


def _repo_path_from_install_meta(workdir: Path) -> Path | None:
    meta = _read_install_meta_raw(workdir)
    if not isinstance(meta, dict):
        return None
    raw = str(meta.get("repo_root") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if _is_git_repo(path) else None


def _canonical_descriptor_dir_from_initialized_workdir(workdir: Path) -> Path | None:
    concrete_workdir = _resolve_cli_workdir(workdir)
    config_dir = (concrete_workdir / "config").resolve()
    if not config_dir.exists():
        return None
    meta = _read_install_meta_raw(concrete_workdir)
    if not isinstance(meta, dict):
        return None
    missing = [
        name
        for name in installer_mod.CANONICAL_DESCRIPTOR_FILENAMES
        if not (config_dir / name).exists()
    ]
    if missing:
        return None
    return config_dir


def _resolve_cli_repo_path(
    repo_path: Path,
    *,
    workdir: Path,
    path_provided: bool,
    descriptors_location: Path | None = None,
    assembly_path: Path | None = None,
) -> Path:
    if path_provided:
        return repo_path.expanduser().resolve()

    concrete_workdir = _resolve_cli_workdir(
        workdir,
        descriptors_location=descriptors_location,
        assembly_path=assembly_path,
    )

    meta_repo = _repo_path_from_install_meta(concrete_workdir)
    if meta_repo is not None:
        return meta_repo

    if descriptors_location is not None or assembly_path is not None:
        return _default_repo_path_for_workdir(concrete_workdir).resolve()

    return repo_path.expanduser().resolve()


def _ensure_secrets_service_available(docker_dir: Path) -> None:
    compose_path = docker_dir / "docker-compose.yaml"
    if not compose_path.exists():
        raise SystemExit(f"Compose file not found: {compose_path}")
    try:
        content = compose_path.read_text()
    except Exception as exc:
        raise SystemExit(f"Failed to read compose file: {compose_path}") from exc
    if "kdcube-secrets:" not in content:
        raise SystemExit(
            "Compose file does not include kdcube-secrets. "
            "Update your repo (or pass --path to a newer checkout) and retry."
        )


def _compose_services(docker_dir: Path, env_file: Path) -> set[str]:
    try:
        env = os.environ.copy()
        env["COMPOSE_ENV_FILES"] = str(env_file)
        output = _docker_output(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "config",
                "--services",
            ],
            env=env,
            cwd=docker_dir,
        )
        return {line.strip() for line in output.splitlines() if line.strip()}
    except SystemExit:
        return set()


def _compose_running_services(docker_dir: Path, env_file: Path) -> set[str]:
    try:
        env = os.environ.copy()
        env["COMPOSE_ENV_FILES"] = str(env_file)
        output = _docker_output(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "ps",
                "--services",
                "--filter",
                "status=running",
            ],
            env=env,
            cwd=docker_dir,
        )
        return {line.strip() for line in output.splitlines() if line.strip()}
    except SystemExit:
        return set()


def _strip_env_value(raw: str | None) -> str:
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def _runtime_config_dir(env_main: installer_mod.EnvFile) -> Path:
    return env_main.path.parent.resolve()


def _resolve_bundle_reload_source(env_main: installer_mod.EnvFile, env_proc: installer_mod.EnvFile) -> Path:
    del env_proc

    bundles_path = _runtime_config_dir(env_main) / "bundles.yaml"
    if bundles_path.exists():
        return bundles_path.resolve()

    raise SystemExit(
        f"No bundles.yaml found under the runtime config directory: {_runtime_config_dir(env_main)}"
    )


def _resolve_live_bundle_export_sources(
    env_main: installer_mod.EnvFile,
    env_proc: installer_mod.EnvFile,
) -> tuple[Path, Path | None] | None:
    del env_proc

    config_dir = _runtime_config_dir(env_main)
    bundles_path = (config_dir / "bundles.yaml").resolve()
    if not bundles_path.exists():
        return None

    bundles_secrets_path = (config_dir / "bundles.secrets.yaml").resolve()
    return bundles_path, bundles_secrets_path if bundles_secrets_path.exists() else None


def _collect_runtime_info(*, repo_root: Path, workdir: Path) -> dict[str, object]:
    ctx = _build_paths_for_repo(repo_root, workdir)
    env_main_path = ctx.config_dir / ".env"
    env_main = installer_mod.load_env_file(env_main_path) if env_main_path.exists() else None
    assembly_path = ctx.config_dir / "assembly.yaml"
    assembly = installer_mod.load_release_descriptor_soft(assembly_path)
    bundles_path = ctx.config_dir / "bundles.yaml"
    bundles_data = installer_mod.load_release_descriptor_soft(bundles_path)
    bundles = bundles_data.get("bundles") if isinstance(bundles_data, dict) and "bundles" in bundles_data else bundles_data
    bundle_items = []
    default_bundle_id = None
    if isinstance(bundles, dict):
        default_bundle_id = bundles.get("default_bundle_id")
        items = bundles.get("items")
        if isinstance(items, list):
            bundle_items = [item for item in items if isinstance(item, dict)]
        else:
            for key, value in bundles.items():
                if key in {"version", "default_bundle_id"}:
                    continue
                if isinstance(value, dict):
                    spec = dict(value)
                    spec.setdefault("id", str(key))
                    bundle_items.append(spec)
    install_meta = _read_install_meta_raw(ctx.workdir) or {}
    env_entries = env_main.entries if env_main is not None else {}

    def _env_value(name: str) -> str | None:
        raw = env_entries.get(name, (None, None))[1] if env_entries else None
        value = _strip_env_value(raw)
        return value or None

    return {
        "workdir": str(ctx.workdir),
        "config_dir": str(ctx.config_dir),
        "data_dir": str(ctx.data_dir),
        "docker_dir": str(ctx.docker_dir),
        "repo_root": str(repo_root),
        "install_meta": install_meta,
        "assembly_path": str(assembly_path) if assembly_path.exists() else None,
        "bundles_path": str(bundles_path) if bundles_path.exists() else None,
        "default_bundle_id": default_bundle_id,
        "bundle_count": len(bundle_items),
        "host_bundles_path": _env_value("HOST_BUNDLES_PATH"),
        "container_bundles_root": _env_value("BUNDLES_ROOT"),
        "host_managed_bundles_path": _env_value("HOST_MANAGED_BUNDLES_PATH"),
        "container_managed_bundles_root": _env_value("MANAGED_BUNDLES_ROOT"),
        "host_bundle_storage_path": _env_value("HOST_BUNDLE_STORAGE_PATH"),
        "container_bundle_storage_root": _env_value("BUNDLE_STORAGE_ROOT"),
        "host_exec_workspace_path": _env_value("HOST_EXEC_WORKSPACE_PATH"),
        "compose_mode": _env_value("KDCUBE_COMPOSE_MODE"),
        "tenant": _get_nested(assembly, "context", "tenant"),
        "project": _get_nested(assembly, "context", "project"),
    }


def print_runtime_info(console: Console, *, repo_root: Path, workdir: Path) -> None:
    info = _collect_runtime_info(repo_root=repo_root, workdir=workdir)

    console.print("[bold]KDCube Runtime Info[/bold]")
    console.print(f"[dim]Workdir:[/dim] {info['workdir']}")
    console.print(f"[dim]Config dir:[/dim] {info['config_dir']}")
    console.print(f"[dim]Data dir:[/dim] {info['data_dir']}")
    console.print(f"[dim]Docker dir:[/dim] {info['docker_dir']}")
    console.print(f"[dim]Repo root:[/dim] {info['repo_root']}")

    install_meta = info["install_meta"] if isinstance(info["install_meta"], dict) else {}
    if install_meta:
        console.print(f"[dim]Install mode:[/dim] {install_meta.get('install_mode') or 'unknown'}")
        console.print(f"[dim]Platform ref:[/dim] {install_meta.get('platform_ref') or 'unknown'}")

    console.print(f"[dim]Tenant / project:[/dim] {info['tenant'] or 'unknown'} / {info['project'] or 'unknown'}")
    console.print(f"[dim]Compose mode:[/dim] {info['compose_mode'] or 'unknown'}")
    console.print(f"[dim]Assembly descriptor:[/dim] {info['assembly_path'] or 'missing'}")
    console.print(f"[dim]Bundles descriptor:[/dim] {info['bundles_path'] or 'missing'}")
    console.print(
        f"[dim]Bundle registry snapshot:[/dim] default={info['default_bundle_id'] or '<none>'}, "
        f"items={info['bundle_count']}"
    )

    console.print("\n[bold]Bundle Mounts[/bold]")
    console.print(f"[dim]Host non-managed bundles:[/dim] {info['host_bundles_path'] or 'unset'}")
    console.print(f"[dim]Container non-managed bundles root:[/dim] {info['container_bundles_root'] or 'unset'}")
    console.print(f"[dim]Host managed bundles:[/dim] {info['host_managed_bundles_path'] or 'unset'}")
    console.print(f"[dim]Container managed bundles root:[/dim] {info['container_managed_bundles_root'] or 'unset'}")
    console.print(f"[dim]Host bundle storage:[/dim] {info['host_bundle_storage_path'] or 'unset'}")
    console.print(f"[dim]Container bundle storage root:[/dim] {info['container_bundle_storage_root'] or 'unset'}")
    console.print(f"[dim]Host exec workspace:[/dim] {info['host_exec_workspace_path'] or 'unset'}")

    host_bundles_path = str(info["host_bundles_path"] or "").strip()
    container_bundles_root = str(info["container_bundles_root"] or "").strip()
    if host_bundles_path and container_bundles_root:
        console.print("\n[bold]Non-git Bundle Path Rule[/bold]")
        console.print(
            "A non-managed local-path bundle host path must live under the host non-managed bundles root. "
            "In bundles.yaml, use the matching container path under the container non-managed bundles root."
        )
        console.print(f"[dim]Example mapping:[/dim] {host_bundles_path}/my.bundle -> {container_bundles_root}/my.bundle")

    host_managed_bundles_path = str(info["host_managed_bundles_path"] or "").strip()
    container_managed_bundles_root = str(info["container_managed_bundles_root"] or "").strip()
    if host_managed_bundles_path and container_managed_bundles_root:
        console.print("\n[bold]Managed Bundle Path Rule[/bold]")
        console.print(
            "Platform-managed bundles are materialized under the managed bundles root. "
            "This includes git-resolved bundles and built-in example bundles."
        )
        console.print(
            f"[dim]Example mapping:[/dim] {host_managed_bundles_path}/repo__bundle.demo__main "
            f"-> {container_managed_bundles_root}/repo__bundle.demo__main"
        )


def _load_bundle_ids_from_descriptor(path: Path) -> set[str]:
    if not path.exists():
        raise SystemExit(f"Bundle descriptor source not found: {path}")

    text = path.read_text()
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)

    if not isinstance(data, dict):
        raise SystemExit(f"Unsupported bundle descriptor format in {path}")

    raw_bundles = data.get("bundles") if "bundles" in data else data
    if isinstance(raw_bundles, dict):
        items = raw_bundles.get("items")
        if isinstance(items, list):
            bundle_ids = {
                str(item.get("id"))
                for item in items
                if isinstance(item, dict) and item.get("id")
            }
            if bundle_ids:
                return bundle_ids
        return {str(key) for key in raw_bundles.keys() if key not in {"items", "version", "default_bundle_id"}}

    if isinstance(raw_bundles, list):
        bundle_ids = {
            str(item.get("id"))
            for item in raw_bundles
            if isinstance(item, dict) and item.get("id")
        }
        if bundle_ids:
            return bundle_ids

    raise SystemExit(f"Descriptor {path} does not contain supported bundle declarations")


def reload_bundle_from_descriptor(
    console: Console,
    *,
    repo_root: Path,
    workdir: Path,
    bundle_id: str,
) -> None:
    ctx = _build_paths_for_repo(repo_root, workdir)
    env_main_path = ctx.config_dir / ".env"
    env_proc_path = ctx.config_dir / ".env.proc"
    if not env_main_path.exists() or not env_proc_path.exists():
        raise SystemExit(
            f"Runtime env files not found under {ctx.config_dir}. "
            "Run the installer first for this workdir."
        )

    env_main = installer_mod.load_env_file(env_main_path)
    env_proc = installer_mod.load_env_file(env_proc_path)
    descriptor_path = _resolve_bundle_reload_source(env_main, env_proc)
    bundle_ids = _load_bundle_ids_from_descriptor(descriptor_path)
    if bundle_id not in bundle_ids:
        known = ", ".join(sorted(bundle_ids)) or "<none>"
        raise SystemExit(
            f"Bundle '{bundle_id}' is not declared in {descriptor_path}. "
            f"Known bundles: {known}"
        )

    running = _compose_running_services(ctx.docker_dir, env_main_path)
    if "chat-proc" not in running:
        raise SystemExit(
            "chat-proc is not running for this workdir. "
            "Start the stack first, then rerun --bundle-reload."
        )

    payload = json.dumps({"bundle_id": bundle_id})
    script = (
        "import json,sys,urllib.request;"
        f"data={payload!r}.encode('utf-8');"
        "req=urllib.request.Request("
        "'http://127.0.0.1:8020/internal/bundles/reload-authority',"
        "data=data,"
        "headers={'content-type':'application/json'},"
        "method='POST');"
        "resp=urllib.request.urlopen(req);"
        "sys.stdout.write(resp.read().decode('utf-8'))"
    )
    cmd = [
        "docker",
        "compose",
        "--env-file",
        str(env_main_path),
        "exec",
        "-T",
        "chat-proc",
        "python",
        "-c",
        script,
    ]

    console.print(
        f"[dim]Reapplying descriptor from[/dim] {descriptor_path}\n"
        f"[dim]Requested bundle[/dim] {bundle_id}"
    )
    _run_compose(console, cmd, cwd=ctx.docker_dir)
    console.print(
        "[green]Bundle descriptor reapplied and target bundle evicted from proc caches.[/green]\n"
        "[dim]The next request will re-import that bundle from the runtime workspace descriptor path.[/dim]"
    )


def _docker_running_names() -> list[str]:
    try:
        output = _docker_output(["docker", "ps", "--format", "{{.Names}}"])
    except SystemExit:
        return []
    names = [line.strip() for line in output.splitlines() if line.strip()]
    return names


def _parse_secret_pairs(items: list[str]) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid secret '{item}'. Use KEY=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Invalid secret '{item}'. Key is empty.")
        secrets[key] = value
    return secrets


def _select_option(console: Console, title: str, options: list[str], default_index: int = 0) -> str:
    def _debug_enabled() -> bool:
        raw = os.environ.get("KDCUBE_CLI_DEBUG_SELECTOR", "").strip().lower()
        return raw not in {"", "0", "false", "no"}

    def _debug(msg: str) -> None:
        if _debug_enabled():
            try:
                debug_path = Path(
                    os.environ.get("KDCUBE_CLI_DEBUG_SELECTOR_PATH", "/tmp/kdcube-cli-selector.log")
                )
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                with debug_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[cli] {msg}\n")
            except Exception:
                pass

    def _plain_prompt_enabled() -> bool:
        raw = os.environ.get("KDCUBE_CLI_PLAIN_PROMPTS", "").strip().lower()
        return raw not in {"", "0", "false", "no"}

    def _use_alt_screen() -> bool:
        raw = os.environ.get("KDCUBE_CLI_ALT_SCREEN", "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))

    def _use_manual_redraw() -> bool:
        raw = os.environ.get("KDCUBE_CLI_MANUAL_REDRAW", "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        term = os.environ.get("TERM", "").lower()
        return bool(os.environ.get("STY") or os.environ.get("TMUX") or term.startswith("screen"))

    def _prompt_numbered() -> str:
        console.print(f"[bold]{title}[/bold]")
        for i, option in enumerate(options, start=1):
            marker = " (default)" if i - 1 == default_index else ""
            console.print(f"  {i}. {option}{marker}")
        choice = Prompt.ask(
            "Select option number",
            choices=[str(i) for i in range(1, len(options) + 1)],
            default=str(default_index + 1),
        )
        return options[int(choice) - 1]

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _debug("path=numbered reason=non-tty")
        return _prompt_numbered()

    if _plain_prompt_enabled():
        _debug("path=numbered reason=forced-plain")
        return _prompt_numbered()

    if not console.is_terminal or console.is_jupyter or os.environ.get("TERM", "").lower() == "dumb":
        _debug("path=numbered reason=terminal-capability")
        return _prompt_numbered()

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
        text.append("\nUse ↑/↓ and Enter. Press q to exit.", style="dim")
        return Panel(text, title="Select")

    if _use_manual_redraw():
        _debug(
            "path=manual-redraw "
            f"TERM={os.environ.get('TERM','')} "
            f"SSH_TTY={bool(os.environ.get('SSH_TTY'))} "
            f"STY={bool(os.environ.get('STY'))} "
            f"TMUX={bool(os.environ.get('TMUX'))}"
        )
        def _capture() -> tuple[str, int]:
            with console.capture() as capture:
                console.print(_render())
            rendered = capture.get()
            lines = rendered.splitlines()
            return rendered, max(1, len(lines))

        def _rewrite(rendered: str, line_count: int) -> None:
            if line_count > 0:
                sys.stdout.write(f"\x1b[{line_count}F")
            sys.stdout.write(rendered)
            sys.stdout.flush()

        rendered, line_count = _capture()
        sys.stdout.write(rendered)
        sys.stdout.flush()

        while True:
            k = read_tty_key()
            _debug(f"key={k!r}")
            if k in (KEY_UP, "k"):
                idx = (idx - 1) % len(options)
            elif k in (KEY_DOWN, "j"):
                idx = (idx + 1) % len(options)
            elif k == KEY_EOF:
                raise KeyboardInterrupt
            elif k == KEY_ENTER:
                return options[idx]
            elif k in ("q", KEY_ESCAPE):
                raise KeyboardInterrupt
            elif k == KEY_INTERRUPT:
                raise KeyboardInterrupt
            rendered, _ = _capture()
            _rewrite(rendered, line_count)

    with Live(
        _render(),
        console=console,
        screen=_use_alt_screen(),
        transient=True,
        auto_refresh=False,
        redirect_stdout=False,
        redirect_stderr=False,
    ) as live:
        _debug(
            "path=live "
            f"screen={_use_alt_screen()} "
            f"TERM={os.environ.get('TERM','')} "
            f"SSH_TTY={bool(os.environ.get('SSH_TTY'))} "
            f"STY={bool(os.environ.get('STY'))} "
            f"TMUX={bool(os.environ.get('TMUX'))}"
        )
        while True:
            k = read_tty_key()
            _debug(f"key={k!r}")
            if k in (KEY_UP, "k"):
                idx = (idx - 1) % len(options)
            elif k in (KEY_DOWN, "j"):
                idx = (idx + 1) % len(options)
            elif k == KEY_EOF:
                raise KeyboardInterrupt
            elif k == KEY_ENTER:
                return options[idx]
            elif k in ("q", KEY_ESCAPE):
                raise KeyboardInterrupt
            elif k == KEY_INTERRUPT:
                raise KeyboardInterrupt
            live.update(_render(), refresh=True)

def _extract_platform_ref(text: str) -> str | None:
    in_platform = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_platform = line.strip().startswith("platform:")
            continue
        if in_platform and "ref:" in line:
            _, value = line.split("ref:", 1)
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def _read_local_ref(repo_root: Path) -> str | None:
    path = repo_root / "release.yaml"
    if not path.exists():
        return None
    return _extract_platform_ref(path.read_text())


def _read_remote_ref(repo_root: Path) -> str | None:
    try:
        subprocess.run(["git", "fetch", "origin", "main"], cwd=repo_root, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return None
    try:
        proc = subprocess.run(
            ["git", "show", "origin/main:release.yaml"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    return _extract_platform_ref(proc.stdout)


def _get_nested(data: dict | None, *keys: str):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _has_value(value: object | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and not installer_mod.is_placeholder(text)


def _iter_bundle_specs(payload: dict | None):
    if not isinstance(payload, dict):
        return
    raw_bundles = payload.get("bundles") if "bundles" in payload else payload
    if not isinstance(raw_bundles, dict):
        return

    items = raw_bundles.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                yield item
        return

    for key, value in raw_bundles.items():
        if key in {"version", "default_bundle_id"}:
            continue
        if isinstance(value, dict):
            spec = dict(value)
            spec.setdefault("id", str(key))
            yield spec


def _uses_local_path_bundles(payload: dict | None) -> bool:
    for spec in _iter_bundle_specs(payload):
        if _has_value(spec.get("path")):
            return True
    return False


def _local_path_bundles_need_host_root(payload: dict | None) -> bool:
    for spec in _iter_bundle_specs(payload):
        raw_path = spec.get("path")
        if not _has_value(raw_path):
            continue
        candidate = Path(str(raw_path).strip()).expanduser()
        normalized = str(candidate).replace("\\", "/")
        if normalized == "/bundles" or normalized.startswith("/bundles/"):
            return True
        if not candidate.is_absolute():
            return True
    return False


def _descriptor_fast_path_reasons(
    assembly: dict,
    *,
    have_secrets: bool,
    have_gateway: bool,
    bundles_descriptor: dict | None = None,
    latest: bool,
    upstream: bool = False,
    release: str | None = None,
) -> list[str]:
    reasons: list[str] = []
    if not have_secrets:
        reasons.append("missing secrets.yaml")
    if not have_gateway:
        reasons.append("missing gateway.yaml")

    provider = installer_mod.normalize_secrets_provider(
        _get_nested(assembly, "secrets", "provider"),
        default="secrets-service",
    )
    if provider != "secrets-file":
        reasons.append("assembly secrets.provider must be secrets-file")

    if not latest and not upstream and not _has_value(release) and not _has_value(_get_nested(assembly, "platform", "ref")):
        reasons.append("assembly platform.ref is required unless --latest, --upstream, or --release is used")

    for field in (("context", "tenant"), ("context", "project")):
        if not _has_value(_get_nested(assembly, *field)):
            reasons.append(f"assembly {'.'.join(field)} is required")

    auth_type = str(_get_nested(assembly, "auth", "type") or "").strip().lower()
    if auth_type not in {"simple", "cognito", "delegated"}:
        reasons.append("assembly auth.type must be simple, cognito, or delegated")
    if auth_type in {"cognito", "delegated"}:
        for field in (
            ("auth", "cognito", "region"),
            ("auth", "cognito", "user_pool_id"),
            ("auth", "cognito", "app_client_id"),
        ):
            if not _has_value(_get_nested(assembly, *field)):
                reasons.append(f"assembly {'.'.join(field)} is required")

    if installer_mod.parse_bool(_get_nested(assembly, "proxy", "ssl")) and not _has_value(assembly.get("domain")):
        reasons.append("assembly domain is required when proxy.ssl=true")

    for storage_kind in ("workspace", "claude_code_session"):
        storage_type = str(_get_nested(assembly, "storage", storage_kind, "type") or "").strip().lower()
        if storage_type == "git" and not _has_value(_get_nested(assembly, "storage", storage_kind, "repo")):
            reasons.append(f"assembly storage.{storage_kind}.repo is required when type=git")

    uses_local_path_bundles = _uses_local_path_bundles(bundles_descriptor)
    if not uses_local_path_bundles:
        uses_local_path_bundles = _uses_local_path_bundles(assembly)
    if (
        uses_local_path_bundles
        and _local_path_bundles_need_host_root(bundles_descriptor if _uses_local_path_bundles(bundles_descriptor) else assembly)
        and not _has_value(_get_nested(assembly, "paths", "host_bundles_path"))
    ):
        reasons.append("assembly paths.host_bundles_path is required for non-interactive local bundle installs")

    frontend = assembly.get("frontend")
    if isinstance(frontend, dict) and frontend:
        frontend_image = frontend.get("image")
        if not _has_value(frontend_image):
            build = frontend.get("build") if isinstance(frontend.get("build"), dict) else frontend
            for field in ("repo", "ref", "dockerfile", "src"):
                if not _has_value(build.get(field) if isinstance(build, dict) else None):
                    reasons.append(f"assembly frontend.build.{field} is required when frontend.image is not set")

    return reasons


def _stage_descriptor_set(
    *,
    repo_root: Path,
    workdir: Path,
    descriptors_location: Path,
) -> dict[str, object]:
    return installer_mod.stage_descriptor_directory(
        workdir / "config",
        source_dir=descriptors_location,
        ai_app_root=repo_root / "app/ai-app",
        require_complete=True,
    )


def ensure_repo(console: Console, repo: str, target: Path) -> None:
    if target.exists() and (target / ".git").is_dir():
        console.print(f"Repo already exists at {target}")
        return

    normalized_repo = installer_mod.normalize_git_repo_source(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"Cloning {normalized_repo} to {target}")
    run(["git", "clone", normalized_repo, str(target)])


def _checkout_repo_ref(console: Console, repo_root: Path, ref: str) -> None:
    ref = str(ref or "").strip()
    if not ref:
        raise SystemExit("Release ref is required for source checkout.")
    console.print(f"[dim]Checking out repo ref:[/dim] {ref}")
    try:
        subprocess.run(["git", "fetch", "--tags", "origin"], cwd=repo_root, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        # Best effort only; later checkout may still work for local refs/commits.
        pass

    candidates = [ref, f"origin/{ref}", f"refs/tags/{ref}", f"tags/{ref}"]
    last_error: subprocess.CalledProcessError | None = None
    for candidate in candidates:
        try:
            subprocess.run(
                ["git", "checkout", "--detach", candidate],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            continue
    details = ""
    if last_error is not None:
        details = (last_error.stderr or last_error.stdout or "").strip()
    raise SystemExit(f"Could not checkout release ref '{ref}' in {repo_root}.{(' ' + details) if details else ''}")


def _checkout_repo_upstream(console: Console, repo_root: Path) -> str:
    console.print("[dim]Checking out latest upstream repo state:[/dim] origin/main")
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "checkout", "--detach", "origin/main"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise SystemExit(
            f"Could not checkout upstream repo state in {repo_root}.{(' ' + details) if details else ''}"
        ) from exc
    value = (proc.stdout or "").strip()
    if not value:
        raise SystemExit(f"Could not resolve HEAD after checking out origin/main in {repo_root}.")
    return value


def _read_install_meta(workdir: Path) -> dict | None:
    workdir = _resolve_cli_workdir(workdir)
    return _read_install_meta_raw(workdir)


def _git_status(repo_root: Path) -> tuple[str | None, str | None, str | None]:
    """Return (local_head, remote_head, status) where status is 'up-to-date'|'behind'|'diverged'|'ahead'|None."""
    try:
        subprocess.run(["git", "fetch", "origin", "main"], cwd=repo_root, check=True, capture_output=True, text=True)
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        remote_head = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        counts = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        ahead, behind = (int(x) for x in counts.split())
        if ahead == 0 and behind == 0:
            status = "up-to-date"
        elif behind > 0 and ahead == 0:
            status = f"behind ({behind} commits)"
        elif ahead > 0 and behind == 0:
            status = f"ahead ({ahead} commits)"
        else:
            status = f"diverged (ahead {ahead}, behind {behind})"
        return local_head, remote_head, status
    except Exception:
        return None, None, None


def run_installer(
    console: Console,
    repo_root: Path,
    workdir: Path,
    mode: str,
    release_ref: str | None,
    docker_namespace: str | None,
    dry_run: bool,
) -> None:
    installer_mod.run_setup(
        console,
        repo_root=repo_root,
        workdir=workdir,
        install_mode=mode,
        release_ref=release_ref,
        docker_namespace=docker_namespace,
        dry_run=dry_run,
    )


def _load_cli_defaults() -> dict:
    try:
        return json.loads(DEFAULT_DEFAULTS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cli_defaults(data: dict) -> None:
    DEFAULT_DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DEFAULTS_FILE.write_text(json.dumps(data, indent=2))


def _check_targeted_command_has_workdir(
    *,
    is_targeted_command: bool,
    workdir_arg: bool,
    cli_defaults: dict,
) -> None:
    """Raise SystemExit when a targeted command has no resolvable workdir.

    Targeted commands (--stop, --info, --bundle-reload, --export-live-bundles)
    require an explicit --workdir or a configured default_workdir.  Without
    either, the CLI would silently fall back to DEFAULT_WORKDIR and either
    pick the wrong deployment or emit a confusing "multiple candidates" error.
    """
    if is_targeted_command and not workdir_arg and "default_workdir" not in cli_defaults:
        raise SystemExit(
            "No target workdir specified.\n"
            "Pass --workdir explicitly or configure a default:\n"
            "  kdcube --set-defaults --default-workdir <path>"
        )


def _check_no_other_local_stack_running(
    console: Console,
    *,
    target_workdir: Path,
    repo_root: Path,
) -> None:
    """Refuse if any sibling local KDCube compose stack is already running.

    Scans runtime directories under target_workdir.parent (the base workdir)
    AND under all sibling base directories at target_workdir.parent.parent
    (the kdcube home dir, typically ~/.kdcube/).  The broader scan is required
    because two commands may use different --workdir bases (e.g. kdcube-runtime
    vs kdcube-runtime2), which would be invisible to a single-level scan.

    Errors from individual candidate checks are silently skipped so a missing
    or broken sibling does not block the operator.
    """
    base_workdir = target_workdir.parent
    kdcube_home = base_workdir.parent

    all_candidates: list[Path] = list(_runtime_candidates(base_workdir))
    if kdcube_home.exists() and kdcube_home.is_dir():
        for sibling_base in sorted(kdcube_home.iterdir()):
            if sibling_base.is_dir() and sibling_base.resolve() != base_workdir.resolve():
                all_candidates.extend(_runtime_candidates(sibling_base))

    for candidate in all_candidates:
        if candidate.resolve() == target_workdir.resolve():
            continue
        env_file = candidate / "config" / ".env"
        if not env_file.exists():
            continue
        try:
            ctx = _build_paths_for_repo(repo_root, candidate)
            running = _compose_running_services(ctx.docker_dir, env_file)
        except (SystemExit, Exception):
            continue
        if running:
            raise SystemExit(
                f"Another local KDCube deployment is already running.\n"
                f"  Workdir : {candidate}\n"
                f"  Services: {', '.join(sorted(running))}\n\n"
                f"Stop it first, then retry:\n"
                f"  kdcube --workdir {candidate} --stop"
            )


def _bootstrap_repo_for_defaults(
    console: Console,
    *,
    repo: str,
    repo_path: Path,
    path_provided: bool,
) -> tuple[Path, Path]:
    """Ensure the platform repo exists and return (repo_path, descriptors_location).

    Used when --descriptors-location is omitted but a source selector (--latest,
    --upstream, --release, --build) implies non-interactive mode.  The repo's
    deployment/ directory is used as the implicit descriptor source so that a
    plain ``kdcube --build --upstream`` works without pre-prepared descriptors.
    """
    if path_provided:
        if not _is_git_repo(repo_path):
            raise SystemExit(f"Provided --path is not a git repo: {repo_path}")
    else:
        ensure_repo(console, repo, repo_path)

    descriptors_location = repo_path / "app" / "ai-app" / "deployment"
    if not descriptors_location.is_dir() or not (descriptors_location / "assembly.yaml").exists():
        raise SystemExit(
            f"Could not find deployment descriptors under {descriptors_location}. "
            "Ensure the platform repo contains app/ai-app/deployment/assembly.yaml."
        )
    return repo_path, descriptors_location


def main() -> None:
    console = Console()
    print_cli_banner()
    parser = argparse.ArgumentParser(description="KDCube Apps bootstrap CLI")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="Git repo URL")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_DIR),
        help="Install directory for the repo",
    )
    parser.add_argument(
        "--workdir",
        default=str(DEFAULT_WORKDIR),
        help="Compose workdir (config+data root)",
    )
    parser.add_argument(
        "--descriptors-location",
        default="",
        help="Directory containing the canonical descriptor set: assembly.yaml, secrets.yaml, bundles.yaml, bundles.secrets.yaml, and gateway.yaml",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="With --descriptors-location, use the latest platform release from the platform repo instead of assembly platform.ref",
    )
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="With --build and either --descriptors-location or an initialized workdir config, use the latest upstream repo state (origin/main) instead of a released platform ref",
    )
    parser.add_argument(
        "--release",
        default="",
        help="With --descriptors-location, use the given platform release instead of assembly platform.ref",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="With --descriptors-location, checkout the selected platform release source and build images locally instead of pulling release images",
    )
    parser.add_argument(
        "--reset-config",
        action="store_true",
        help="Re-run config prompts and allow editing existing values",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Alias for --reset-config",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean dangling images, build cache, and old KDCube image tags",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the local Docker Compose stack for the selected workdir",
    )
    parser.add_argument(
        "--remove-volumes",
        action="store_true",
        help="With --stop, also pass -v to docker compose down",
    )
    parser.add_argument(
        "--secrets-set",
        action="append",
        default=[],
        help="Inject runtime secret as KEY=VALUE into the secrets sidecar (repeatable)",
    )
    parser.add_argument(
        "--secrets-prompt",
        action="store_true",
        help="Prompt for LLM keys and inject into the secrets sidecar",
    )
    parser.add_argument(
        "--proxy-ssl",
        action="store_true",
        help="Force SSL nginx proxy config (overrides assembly descriptor)",
    )
    parser.add_argument(
        "--no-proxy-ssl",
        action="store_true",
        help="Disable SSL nginx proxy config (overrides assembly descriptor)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate env files and print a preview without running Docker",
    )
    parser.add_argument(
        "--dry-run-print-env",
        action="store_true",
        help="With --dry-run, print full env file contents",
    )
    parser.add_argument(
        "--bundle-reload",
        default="",
        help="Reapply runtime workspace bundles.yaml and clear proc bundle caches for local development. Validates that the given bundle id exists in the current descriptor.",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print resolved runtime info for the selected workdir, including descriptor files, install metadata, and host/container bundle mount mappings.",
    )
    parser.add_argument(
        "--export-live-bundles",
        action="store_true",
        help="Export the current effective live bundles.yaml and bundles.secrets.yaml from the active bundle authority.",
    )
    parser.add_argument(
        "--tenant",
        default="",
        help="Tenant for --export-live-bundles when exporting from AWS SM. Ignored when exporting workspace descriptors directly.",
    )
    parser.add_argument(
        "--project",
        default="",
        help="Project for --export-live-bundles when exporting from AWS SM. Ignored when exporting workspace descriptors directly.",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output directory for --export-live-bundles. Defaults to the current directory.",
    )
    parser.add_argument(
        "--aws-region",
        default="",
        help="AWS region for --export-live-bundles when exporting from AWS SM. Falls back to current AWS CLI environment if omitted.",
    )
    parser.add_argument(
        "--aws-profile",
        default="",
        help="AWS profile for --export-live-bundles when exporting from AWS SM. Falls back to current AWS CLI environment if omitted.",
    )
    parser.add_argument(
        "--aws-sm-prefix",
        default="",
        help="Explicit AWS Secrets Manager prefix for --export-live-bundles when exporting from AWS SM. Default is kdcube/<tenant>/<project>.",
    )
    parser.add_argument(
        "--set-defaults",
        action="store_true",
        help="Save --default-tenant, --default-project, and --default-workdir as persistent operator defaults",
    )
    parser.add_argument(
        "--default-tenant",
        default="",
        help="Default tenant to persist with --set-defaults",
    )
    parser.add_argument(
        "--default-project",
        default="",
        help="Default project to persist with --set-defaults",
    )
    parser.add_argument(
        "--default-workdir",
        default="",
        help="Default workdir to persist with --set-defaults",
    )
    args = parser.parse_args()

    def _arg_provided(name: str) -> bool:
        return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])

    if args.dry_run and (args.secrets_set or args.secrets_prompt):
        console.print("[yellow]Dry run ignores --secrets-set/--secrets-prompt (env generation only).[/yellow]")
        args.secrets_set = []
        args.secrets_prompt = False

    repo_path = Path(os.path.expanduser(args.path)).resolve()
    path_provided = _arg_provided("--path")
    workdir_arg = _arg_provided("--workdir")
    workdir = Path(os.path.expanduser(args.workdir)).expanduser().resolve()
    cli_defaults = _load_cli_defaults()
    if not workdir_arg and "default_workdir" in cli_defaults:
        workdir = Path(cli_defaults["default_workdir"]).resolve()
    implicit_descriptors_location: Path | None = None
    if (
        workdir_arg
        and not args.descriptors_location
        and not args.secrets_set
        and not args.secrets_prompt
    ):
        implicit_descriptors_location = _canonical_descriptor_dir_from_initialized_workdir(workdir)
    effective_descriptors_location = (
        Path(os.path.expanduser(args.descriptors_location)).expanduser().resolve()
        if args.descriptors_location
        else implicit_descriptors_location
    )
    try:
        if args.set_defaults:
            updates: dict[str, str] = {}
            if args.default_tenant.strip():
                updates["default_tenant"] = args.default_tenant.strip()
            if args.default_project.strip():
                updates["default_project"] = args.default_project.strip()
            if args.default_workdir.strip():
                updates["default_workdir"] = str(
                    Path(os.path.expanduser(args.default_workdir)).resolve()
                )
            if not updates:
                raise SystemExit(
                    "Provide at least one of --default-tenant, --default-project, "
                    "or --default-workdir with --set-defaults."
                )
            cli_defaults.update(updates)
            _save_cli_defaults(cli_defaults)
            console.print(f"[green]Defaults saved to {DEFAULT_DEFAULTS_FILE}:[/green]")
            for k, v in cli_defaults.items():
                console.print(f"  {k}: {v}")
            return
        if args.clean:
            clean_docker_images(console)
            return
        _check_targeted_command_has_workdir(
            is_targeted_command=bool(
                args.stop
                or args.info
                or str(args.bundle_reload or "").strip()
                or args.export_live_bundles
            ),
            workdir_arg=workdir_arg,
            cli_defaults=cli_defaults,
        )
        if args.export_live_bundles:
            workdir = _resolve_cli_workdir(workdir)
            out_dir = Path(os.path.expanduser(args.out_dir or os.getcwd())).expanduser().resolve()
            bundles_path = None
            bundles_secrets_path = None
            try:
                ctx = _build_paths_for_repo(repo_path, workdir)
                env_main_path = ctx.config_dir / ".env"
                env_proc_path = ctx.config_dir / ".env.proc"
                if env_main_path.exists() and env_proc_path.exists():
                    env_main = installer_mod.load_env_file(env_main_path)
                    env_proc = installer_mod.load_env_file(env_proc_path)
                    sources = _resolve_live_bundle_export_sources(env_main, env_proc)
                    if sources is not None:
                        bundles_path, bundles_secrets_path = sources
            except SystemExit:
                raise
            except Exception:
                pass
            export_live_bundle_descriptors(
                console,
                tenant=str(args.tenant or cli_defaults.get("default_tenant", "") or "").strip(),
                project=str(args.project or cli_defaults.get("default_project", "") or "").strip(),
                out_dir=out_dir,
                aws_region=str(args.aws_region or "").strip() or None,
                aws_profile=str(args.aws_profile or "").strip() or None,
                aws_sm_prefix=str(args.aws_sm_prefix or "").strip() or None,
                bundles_path=bundles_path,
                bundles_secrets_path=bundles_secrets_path,
            )
            return
        if args.remove_volumes and not args.stop:
            raise SystemExit("--remove-volumes can only be used together with --stop.")
        selected_version_flags = int(bool(args.latest)) + int(bool(args.upstream)) + int(bool(str(args.release or "").strip()))
        if selected_version_flags > 1:
            raise SystemExit("Choose only one of --latest, --upstream, or --release.")
        if args.upstream and not args.build:
            raise SystemExit("--upstream requires --build because arbitrary upstream commits do not map to release images.")
        if args.proxy_ssl and args.no_proxy_ssl:
            raise SystemExit("Choose only one of --proxy-ssl or --no-proxy-ssl.")
        if args.proxy_ssl:
            os.environ["KDCUBE_PROXY_SSL"] = "1"
        elif args.no_proxy_ssl:
            os.environ["KDCUBE_PROXY_SSL"] = "0"
        if args.dry_run_print_env:
            os.environ["KDCUBE_DRY_RUN_PRINT_ENV"] = "1"
        if args.stop:
            stop_compose_stack(
                console,
                repo_root=_resolve_cli_repo_path(
                    repo_path,
                    workdir=workdir,
                    path_provided=path_provided,
                ),
                workdir=_resolve_cli_workdir(workdir),
                remove_volumes=args.remove_volumes,
            )
            return
        if args.bundle_reload:
            reload_bundle_from_descriptor(
                console,
                repo_root=_resolve_cli_repo_path(
                    repo_path,
                    workdir=workdir,
                    path_provided=path_provided,
                ),
                workdir=_resolve_cli_workdir(workdir),
                bundle_id=str(args.bundle_reload).strip(),
            )
            return
        if args.info:
            resolved_workdir = _resolve_cli_workdir(workdir)
            print_runtime_info(
                console,
                repo_root=_resolve_cli_repo_path(
                    repo_path,
                    workdir=resolved_workdir,
                    path_provided=path_provided,
                ),
                workdir=resolved_workdir,
            )
            return
        # No-descriptors fast path: when a source selector is given without
        # --descriptors-location (and no initialized workdir was found), bootstrap
        # the platform repo and use its deployment/ directory as the descriptor
        # source.  This lets operators run e.g. ``kdcube --build --upstream``
        # without pre-preparing a descriptor set.
        if (
            effective_descriptors_location is None
            and not args.secrets_set
            and not args.secrets_prompt
            and (args.latest or args.upstream or str(args.release or "").strip() or args.build)
        ):
            repo_path, effective_descriptors_location = _bootstrap_repo_for_defaults(
                console,
                repo=args.repo,
                repo_path=repo_path,
                path_provided=path_provided,
            )
            path_provided = True
            console.print(
                f"[dim]No --descriptors-location provided. "
                f"Using repo defaults from:[/dim] {effective_descriptors_location}"
            )

        if (
            not args.secrets_set
            and not args.secrets_prompt
            and effective_descriptors_location is None
            and not (args.dry_run and workdir_arg)
        ):
            workdir = Path(
                Prompt.ask("Compose workdir (config+data root)", default=str(workdir))
            ).expanduser().resolve()

        descriptor_bootstrap = None
        if effective_descriptors_location is not None and not args.secrets_set and not args.secrets_prompt:
            descriptors_location = effective_descriptors_location
            workdir = _resolve_cli_workdir(workdir, descriptors_location=descriptors_location)
            repo_path = _resolve_cli_repo_path(
                repo_path,
                workdir=workdir,
                path_provided=path_provided,
                descriptors_location=descriptors_location,
            )
            os.environ["KDCUBE_DESCRIPTORS_LOCATION"] = str(descriptors_location)
            descriptor_bootstrap = _stage_descriptor_set(
                repo_root=repo_path,
                workdir=workdir,
                descriptors_location=descriptors_location,
            )
            assembly_path = descriptor_bootstrap["assembly_path"]
            secrets_path = descriptor_bootstrap["secrets_path"]
            gateway_path = descriptor_bootstrap["gateway_path"]
            bundles_path = descriptor_bootstrap["bundles_path"]
            bundles_secrets_path = descriptor_bootstrap["bundles_secrets_path"]
            assembly = descriptor_bootstrap["assembly"]
            descriptor_is_runtime_config = descriptors_location.resolve() == (workdir / "config").resolve()

            os.environ["KDCUBE_ASSEMBLY_DESCRIPTOR_PATH"] = str(assembly_path)
            os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "0" if descriptor_is_runtime_config else "1"
            if secrets_path:
                os.environ["KDCUBE_SECRETS_DESCRIPTOR_PATH"] = str(secrets_path)
            if gateway_path:
                os.environ["KDCUBE_GATEWAY_DESCRIPTOR_PATH"] = str(gateway_path)
            if bundles_path:
                os.environ["KDCUBE_BUNDLES_DESCRIPTOR_PATH"] = str(bundles_path)
            if bundles_secrets_path:
                os.environ["KDCUBE_BUNDLES_SECRETS_PATH"] = str(bundles_secrets_path)

            os.environ["KDCUBE_ASSEMBLY_USE_BUNDLES"] = "1" if bool(_get_nested(assembly, "bundles")) else "0"
            os.environ["KDCUBE_ASSEMBLY_USE_FRONTEND"] = "1" if bool(_get_nested(assembly, "frontend")) else "0"
            os.environ["KDCUBE_ASSEMBLY_USE_PLATFORM"] = "0"
            os.environ["KDCUBE_USE_BUNDLES_DESCRIPTOR"] = "1" if bundles_path else "0"
            os.environ["KDCUBE_USE_BUNDLES_SECRETS"] = "1" if bundles_secrets_path else "0"

            reasons = _descriptor_fast_path_reasons(
                assembly,
                have_secrets=bool(descriptor_bootstrap["have_secrets"]),
                have_gateway=bool(descriptor_bootstrap["have_gateway"]),
                bundles_descriptor=descriptor_bootstrap.get("bundles_data"),
                latest=bool(args.latest),
                upstream=bool(args.upstream),
                release=str(args.release or "").strip() or None,
            )
            if reasons:
                rendered = "\n".join(f"  - {reason}" for reason in reasons)
                raise SystemExit(
                    "Descriptor directory is not valid for non-interactive install.\n"
                    f"{rendered}"
                )

            platform_repo = str(_get_nested(assembly, "platform", "repo") or args.repo).strip()
            if not platform_repo:
                platform_repo = args.repo
            if not _is_git_repo(repo_path):
                ensure_repo(console, platform_repo, repo_path)

            release_ref = None
            install_mode = "release"
            if args.upstream:
                release_ref = _checkout_repo_upstream(console, repo_path)
                install_mode = "upstream"
            elif args.latest:
                release_ref = _read_remote_ref(repo_path)
                if not release_ref:
                    release_ref = _read_local_ref(repo_path)
                if not release_ref:
                    raise SystemExit(
                        "Could not resolve the latest platform release from the platform repo. "
                        "Check platform.repo or pass a repo that contains release.yaml."
                    )
            elif str(args.release or "").strip():
                release_ref = str(args.release).strip()
            else:
                release_ref = str(_get_nested(assembly, "platform", "ref") or "").strip() or None
                if not release_ref:
                    raise SystemExit("assembly platform.ref is required unless --latest, --upstream, or --release is used.")

            if args.build:
                if not args.upstream:
                    _checkout_repo_ref(console, repo_path, release_ref)
                if install_mode != "upstream":
                    install_mode = "skip"
                console.print("[green]Descriptor set is complete. Running non-interactive source build install.[/green]")
            else:
                console.print("[green]Descriptor set is complete. Running non-interactive release-image install.[/green]")
            os.environ["KDCUBE_CLI_NONINTERACTIVE"] = "1"
            if not args.dry_run:
                _check_no_other_local_stack_running(
                    console, target_workdir=workdir, repo_root=repo_path
                )
            run_installer(console, repo_path, workdir, install_mode, release_ref, None, args.dry_run)
            return

        assembly_descriptor_path: Path | None = None
        secrets_descriptor_path: Path | None = None
        bundles_descriptor_path: Path | None = None
        bundles_secrets_path: Path | None = None
        use_descriptor_bundles = False
        use_bundles_descriptor = False
        use_bundles_secrets = False
        use_descriptor_frontend = False
        bundles_default = False
        frontend_default = False
        if not args.secrets_set and not args.secrets_prompt and not descriptor_bootstrap:
            default_descriptors_dir = str((workdir / "config").resolve())
            raw_dir = Prompt.ask("Descriptors directory", default=default_descriptors_dir).strip()
            source_dir = Path(os.path.expanduser(raw_dir)).expanduser().resolve()
            workdir = _resolve_cli_workdir(workdir, descriptors_location=source_dir)
            repo_path = _resolve_cli_repo_path(
                repo_path,
                workdir=workdir,
                path_provided=path_provided,
                descriptors_location=source_dir,
            )
            staged = installer_mod.stage_descriptor_directory(
                workdir / "config",
                source_dir=source_dir,
                ai_app_root=repo_path / "app/ai-app",
                require_complete=False,
            )
            user_supplied = source_dir.resolve() != (workdir / "config").resolve()

            assembly_descriptor_path = staged["assembly_path"]
            secrets_descriptor_path = staged["secrets_path"]
            bundles_descriptor_path = staged["bundles_path"]
            bundles_secrets_path = staged["bundles_secrets_path"]
            gateway_descriptor_path = staged["gateway_path"]
            descriptor = staged["assembly"]

            os.environ["KDCUBE_ASSEMBLY_DESCRIPTOR_PATH"] = str(assembly_descriptor_path)
            os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "1" if user_supplied else "0"
            if secrets_descriptor_path:
                os.environ["KDCUBE_SECRETS_DESCRIPTOR_PATH"] = str(secrets_descriptor_path)
            if gateway_descriptor_path:
                os.environ["KDCUBE_GATEWAY_DESCRIPTOR_PATH"] = str(gateway_descriptor_path)
            if bundles_descriptor_path:
                os.environ["KDCUBE_BUNDLES_DESCRIPTOR_PATH"] = str(bundles_descriptor_path)
            if bundles_secrets_path:
                os.environ["KDCUBE_BUNDLES_SECRETS_PATH"] = str(bundles_secrets_path)

            bundles_default = isinstance(descriptor, dict) and bool(descriptor.get("bundles"))
            frontend_default = isinstance(descriptor, dict) and bool(descriptor.get("frontend"))
            use_bundles_descriptor = bool(bundles_descriptor_path)
            use_bundles_secrets = bool(bundles_secrets_path)

            if frontend_default:
                use_descriptor_frontend = Confirm.ask(
                    "Use assembly descriptor for frontend?",
                    default=True,
                )

            os.environ["KDCUBE_ASSEMBLY_USE_BUNDLES"] = "0"
            os.environ["KDCUBE_ASSEMBLY_USE_FRONTEND"] = "1" if use_descriptor_frontend else "0"
            os.environ["KDCUBE_ASSEMBLY_USE_PLATFORM"] = "0"
            if bundles_descriptor_path:
                os.environ["KDCUBE_USE_BUNDLES_DESCRIPTOR"] = "1" if use_bundles_descriptor else "0"
            if bundles_secrets_path:
                os.environ["KDCUBE_USE_BUNDLES_SECRETS"] = "1" if use_bundles_secrets else "0"
            if not assembly_descriptor_path:
                os.environ["KDCUBE_ASSEMBLY_USE_BUNDLES"] = "0"
                os.environ["KDCUBE_ASSEMBLY_USE_FRONTEND"] = "0"
                os.environ["KDCUBE_ASSEMBLY_USE_PLATFORM"] = "0"
        elif descriptor_bootstrap:
            assembly_descriptor_path = descriptor_bootstrap["assembly_path"]
            secrets_descriptor_path = descriptor_bootstrap["secrets_path"]
            bundles_descriptor_path = descriptor_bootstrap["bundles_path"]
            bundles_secrets_path = descriptor_bootstrap["bundles_secrets_path"]

        if args.secrets_set or args.secrets_prompt:
            secrets = _parse_secret_pairs(args.secrets_set)
            if args.secrets_prompt:
                openai = Prompt.ask("OpenAI API key (leave blank to skip)", default="", password=True)
                anthropic = Prompt.ask("Anthropic API key (leave blank to skip)", default="", password=True)
                git_http_token = Prompt.ask("Git HTTPS token (leave blank to skip)", default="", password=True)
                if openai:
                    secrets["OPENAI_API_KEY"] = openai
                if anthropic:
                    secrets["ANTHROPIC_API_KEY"] = anthropic
                if git_http_token:
                    secrets["GIT_HTTP_TOKEN"] = git_http_token
            if not secrets:
                console.print("[yellow]No secrets provided. Nothing to inject.[/yellow]")
                return
            ctx = _build_paths_for_repo(repo_path, workdir)
            _ensure_secrets_service_available(ctx.docker_dir)
            base_env = workdir / "config" / ".env"
            token_overrides = installer_mod.generate_runtime_tokens()
            runtime_env = installer_mod.write_env_overlay(base_env, token_overrides)
            try:
                console.print("[dim]Restarting secrets-enabled services with fresh tokens...[/dim]")
                # Ensure secrets sidecar is (re)created with the new tokens.
                _run_compose(
                    console,
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        str(runtime_env),
                        "up",
                        "-d",
                        "--force-recreate",
                        "kdcube-secrets",
                    ],
                    cwd=ctx.docker_dir,
                )
                installer_mod.apply_runtime_secrets(console, ctx, secrets, runtime_env)

                # Restart ingress/proc so they pick up new read tokens.
                _run_compose(
                    console,
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        str(runtime_env),
                        "up",
                        "-d",
                        "--force-recreate",
                        "chat-ingress",
                        "chat-proc",
                    ],
                    cwd=ctx.docker_dir,
                )

                # Restart proxy to refresh upstream resolution.
                available = _compose_services(ctx.docker_dir, runtime_env)
                if available:
                    console.print(f"[dim]Compose services:[/dim] {', '.join(sorted(available))}")
                running = _compose_running_services(ctx.docker_dir, runtime_env)
                if running:
                    console.print(f"[dim]Running services:[/dim] {', '.join(sorted(running))}")
                else:
                    names = _docker_running_names()
                    if names:
                        console.print(f"[dim]Running containers:[/dim] {', '.join(sorted(names))}")
                # Restart only known proxy service names from compose config.
                proxy_targets = [
                    name
                    for name in ("web-proxy", "kdcube-web-proxy")
                    if name in available
                ]
                if proxy_targets:
                    for proxy_name in proxy_targets:
                        _run_compose_optional(
                            console,
                            [
                                "docker",
                                "compose",
                                "--env-file",
                                str(runtime_env),
                                "up",
                                "-d",
                                "--force-recreate",
                                proxy_name,
                            ],
                            cwd=ctx.docker_dir,
                            label=f"{proxy_name} restart skipped or failed",
                        )
                else:
                    # Fallback to container-level restart if compose service name was not detected.
                    names = _docker_running_names()
                    if "kdcube-web-proxy" in names:
                        _run_compose_optional(
                            console,
                            ["docker", "restart", "kdcube-web-proxy"],
                            cwd=ctx.docker_dir,
                            label="kdcube-web-proxy restart skipped or failed",
                        )
                    else:
                        console.print("[yellow]No web proxy service found to restart.[/yellow]")
                # Show final service state for debugging.
                _run_compose_optional(
                    console,
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        str(runtime_env),
                        "ps",
                    ],
                    cwd=ctx.docker_dir,
                    label="compose ps failed",
                )
            finally:
                runtime_env.unlink(missing_ok=True)
            return

        install_meta = _read_install_meta(workdir)
        if install_meta and install_meta.get("platform_ref"):
            console.print(f"[dim]Installed release (workdir):[/dim] {install_meta.get('platform_ref')}")
        elif (workdir / "config").exists():
            console.print("[dim]Installed release (workdir):[/dim] unknown (no metadata)")

        docker_namespace = None

        path_arg = _arg_provided("--path")
        if path_arg:
            # Explicit local repo path: use as-is for templates/builds, no source menu.
            if not _is_git_repo(repo_path):
                raise SystemExit(f"Provided --path is not a git repo: {repo_path}")
            local_ref = _read_local_ref(repo_path)
            if local_ref:
                console.print(f"[dim]Repo release.yaml:[/dim] {local_ref}")
            _, _, status = _git_status(repo_path)
            if status:
                console.print(f"[dim]Repo status:[/dim] {status}")
            mode = "skip"
            release_ref = None
        else:
            # Source selection menu (workspace, local, upstream, releases).
            workspace_repo = repo_path
            workspace_has_repo = _is_git_repo(workspace_repo)

            local_ref = None
            remote_ref = None
            if workspace_has_repo:
                local_ref = _read_local_ref(workspace_repo)
                remote_ref = _read_remote_ref(workspace_repo)
                if remote_ref:
                    console.print(f"[dim]Latest release (remote):[/dim] {remote_ref}")
                if local_ref:
                    console.print(f"[dim]Repo release.yaml:[/dim] {local_ref}")
                _, _, status = _git_status(workspace_repo)
                if status:
                    console.print(f"[dim]Repo status:[/dim] {status}")

            choices = ["upstream", "release-latest", "release-tag", "local"]
            if workspace_has_repo:
                choices.append("workspace")
            default_choice = "workspace" if workspace_has_repo else "upstream"
            default_index = choices.index(default_choice)
            console.print("[dim]Sources define templates + images. Only release-latest/release-tag pull images; all other choices build locally.[/dim]")
            choice = _select_option(
                console,
                "Install source",
                options=choices,
                default_index=default_index,
            )

            if choice == "local":
                default_local = str(repo_path) if _is_git_repo(repo_path) else ""
                while True:
                    local_path = Prompt.ask("Local repo path", default=default_local).strip()
                    if not local_path:
                        console.print("[yellow]Local repo path is required for 'local' source.[/yellow]")
                        continue
                    repo_path = Path(os.path.expanduser(local_path)).expanduser().resolve()
                    if not _is_git_repo(repo_path):
                        console.print(f"[yellow]Local repo path is not a git repo: {repo_path}[/yellow]")
                        continue
                    break
                mode = "skip"
                release_ref = None
            elif choice == "workspace":
                repo_path = workspace_repo
                if not workspace_has_repo:
                    console.print("[yellow]Workspace repo not found; falling back to upstream clone.[/yellow]")
                    mode = "upstream"
                else:
                    mode = "skip"
                release_ref = None
            elif choice == "upstream":
                repo_path = workspace_repo
                mode = "upstream"
                release_ref = None
            elif choice == "release-latest":
                repo_path = workspace_repo
                mode = "release"
                if not workspace_has_repo:
                    ensure_repo(console, args.repo, repo_path)
                release_ref = remote_ref or _read_remote_ref(repo_path) or Prompt.ask("Release version (platform.ref)")
            else:  # release-tag
                repo_path = workspace_repo
                mode = "release"
                if not workspace_has_repo:
                    ensure_repo(console, args.repo, repo_path)
                release_ref = Prompt.ask("Release version (platform.ref)")

            if mode in {"upstream", "skip"} and choice != "local":
                ensure_repo(console, args.repo, repo_path)
            if mode == "upstream":
                run(["git", "pull"], cwd=repo_path)

        if args.reset_config or args.reset:
            os.environ["KDCUBE_RESET_CONFIG"] = "1"
        if not args.dry_run:
            _check_no_other_local_stack_running(
                console, target_workdir=_resolve_cli_workdir(workdir), repo_root=repo_path
            )
        run_installer(console, repo_path, workdir, mode, release_ref, docker_namespace, args.dry_run)
    except FileNotFoundError as exc:
        detail = str(exc).strip()
        if detail:
            raise SystemExit(f"Missing dependency or file: {detail}") from exc
        raise SystemExit("Missing dependency or file (FileNotFoundError).") from exc
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise SystemExit(130)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}.") from exc


if __name__ == "__main__":
    main()
