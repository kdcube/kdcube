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


def _docker_output(cmd: list[str], env: dict[str, str] | None = None) -> str:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, env=env).stdout
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
    ai_app_root = repo_root / "app/ai-app"
    if not (ai_app_root / "deployment/docker/all_in_one_kdcube/docker-compose.yaml").exists():
        raise SystemExit(f"Could not find deployment/docker/all_in_one_kdcube under {ai_app_root}")
    lib_root = ai_app_root / "src/kdcube-ai-app"
    if not (lib_root / "kdcube_ai_app").exists():
        raise SystemExit(f"Could not locate kdcube_ai_app under {lib_root}")
    docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    return installer_mod.PathsContext(
        lib_root=lib_root,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=docker_dir / "sample_env",
        workdir=workdir,
        config_dir=workdir / "config",
        data_dir=workdir / "data",
    )


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
        )
        return {line.strip() for line in output.splitlines() if line.strip()}
    except SystemExit:
        return set()


def _strip_env_value(raw: str | None) -> str:
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def _resolve_bundle_reload_source(env_main: installer_mod.EnvFile, env_proc: installer_mod.EnvFile) -> Path:
    raw = _strip_env_value(env_proc.entries.get("AGENTIC_BUNDLES_JSON", (None, None))[1])
    if not raw:
        raise SystemExit(
            "AGENTIC_BUNDLES_JSON is not configured for this runtime. "
            "Bundle reload expects a descriptor-backed bundle registry."
        )

    if raw == "/config/bundles.yaml":
        host = _strip_env_value(env_main.entries.get("HOST_BUNDLES_DESCRIPTOR_PATH", (None, None))[1])
        if not host or host == "/dev/null":
            raise SystemExit(
                "HOST_BUNDLES_DESCRIPTOR_PATH is not configured. "
                "Bundle reload expects bundles.yaml to be mounted into chat-proc."
            )
        return Path(host).expanduser().resolve()

    if raw == "/config/assembly.yaml":
        host = _strip_env_value(env_main.entries.get("HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH", (None, None))[1])
        if not host or host == "/dev/null":
            raise SystemExit(
                "HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH is not configured. "
                "Bundle reload expects assembly.yaml to be mounted into chat-proc."
            )
        return Path(host).expanduser().resolve()

    candidate = Path(raw).expanduser()
    return candidate.resolve() if candidate.exists() else candidate


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
    if not isinstance(raw_bundles, dict):
        raise SystemExit(f"Descriptor {path} does not contain a bundles mapping")
    return {str(key) for key in raw_bundles.keys()}


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
        "'http://127.0.0.1:8020/internal/bundles/reset-env',"
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
        "[dim]The next request will re-import that bundle from the mounted path.[/dim]"
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


def _load_yaml_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text()) if path.suffix == ".json" else None
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return payload
    try:
        return installer_mod.load_release_descriptor(path)
    except Exception:
        return {}


def _descriptor_fast_path_reasons(
    assembly: dict,
    *,
    have_secrets: bool,
    have_gateway: bool,
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

    if not _has_value(_get_nested(assembly, "paths", "host_bundles_path")):
        reasons.append("assembly paths.host_bundles_path is required for non-interactive local bundle installs")

    frontend = assembly.get("frontend")
    if isinstance(frontend, dict) and frontend:
        frontend_image = frontend.get("image")
        if not _has_value(frontend_image):
            build = frontend.get("build") if isinstance(frontend.get("build"), dict) else frontend
            for field in ("repo", "ref", "dockerfile", "src"):
                if not _has_value(build.get(field) if isinstance(build, dict) else None):
                    reasons.append(f"assembly frontend.build.{field} is required when frontend.image is not set")
            if not _has_value(frontend.get("frontend_config")):
                reasons.append("assembly frontend.frontend_config is required when frontend.image is not set")

    return reasons


def _stage_descriptor_set(
    *,
    repo_root: Path,
    workdir: Path,
    descriptors_location: Path,
) -> dict[str, object]:
    ai_app_root = repo_root / "app/ai-app"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    assembly_source = descriptors_location / "assembly.yaml"
    secrets_source = descriptors_location / "secrets.yaml"
    gateway_source = descriptors_location / "gateway.yaml"
    bundles_source = descriptors_location / "bundles.yaml"
    bundles_secrets_source = descriptors_location / "bundles.secrets.yaml"

    assembly_target = config_dir / "assembly.yaml"
    if not assembly_source.exists():
        raise SystemExit(f"assembly.yaml not found under {descriptors_location}")
    if not installer_mod.stage_assembly_descriptor(
        assembly_target,
        source_path=assembly_source,
        ai_app_root=ai_app_root,
    ):
        raise SystemExit(f"assembly.yaml not found under {descriptors_location}")

    secrets_target = config_dir / "secrets.yaml"
    have_secrets = installer_mod.stage_secrets_descriptor(
        secrets_target,
        source_path=secrets_source if secrets_source.exists() else None,
        ai_app_root=ai_app_root,
    ) if secrets_source.exists() else False

    gateway_target = config_dir / "gateway.yaml"
    have_gateway = False
    if gateway_source.exists():
        installer_mod.stage_gateway_descriptor(
            gateway_target,
            source_path=gateway_source,
            ai_app_root=ai_app_root,
        )
        have_gateway = gateway_target.exists()

    bundles_target = config_dir / "bundles.yaml"
    have_bundles = installer_mod.stage_bundles_descriptor(
        bundles_target,
        source_path=bundles_source if bundles_source.exists() else None,
        ai_app_root=ai_app_root,
    ) if bundles_source.exists() else False

    bundles_secrets_target = config_dir / "bundles.secrets.yaml"
    have_bundles_secrets = installer_mod.stage_bundles_secrets_descriptor(
        bundles_secrets_target,
        source_path=bundles_secrets_source if bundles_secrets_source.exists() else None,
        ai_app_root=ai_app_root,
    ) if bundles_secrets_source.exists() else False

    assembly = _load_yaml_mapping(assembly_target)
    return {
        "assembly_path": assembly_target,
        "secrets_path": secrets_target if have_secrets else None,
        "gateway_path": gateway_target if have_gateway else None,
        "bundles_path": bundles_target if have_bundles else None,
        "bundles_secrets_path": bundles_secrets_target if have_bundles_secrets else None,
        "assembly": assembly,
        "have_secrets": have_secrets,
        "have_gateway": have_gateway,
    }


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
    meta_path = workdir / "config" / "install-meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return None


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
        help="Directory containing assembly.yaml, secrets.yaml, gateway.yaml, and optional bundle descriptors",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="With --descriptors-location, use the latest platform release from the platform repo instead of assembly platform.ref",
    )
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="With --descriptors-location and --build, use the latest upstream repo state (origin/main) instead of a released platform ref",
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
        help="Reapply the mounted bundle descriptor and clear proc bundle caches for local development. Validates that the given bundle id exists in the current descriptor.",
    )
    args = parser.parse_args()

    def _arg_provided(name: str) -> bool:
        return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])

    if args.dry_run and (args.secrets_set or args.secrets_prompt):
        console.print("[yellow]Dry run ignores --secrets-set/--secrets-prompt (env generation only).[/yellow]")
        args.secrets_set = []
        args.secrets_prompt = False

    repo_path = Path(os.path.expanduser(args.path)).resolve()
    try:
        if args.clean:
            clean_docker_images(console)
            return
        if args.remove_volumes and not args.stop:
            raise SystemExit("--remove-volumes can only be used together with --stop.")
        selected_version_flags = int(bool(args.latest)) + int(bool(args.upstream)) + int(bool(str(args.release or "").strip()))
        if selected_version_flags > 1:
            raise SystemExit("Choose only one of --latest, --upstream, or --release.")
        if args.upstream and not args.descriptors_location:
            raise SystemExit("--upstream is only supported together with --descriptors-location.")
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
        workdir = Path(os.path.expanduser(args.workdir)).expanduser().resolve()
        def _is_git_repo(path: Path) -> bool:
            return path.exists() and (path / ".git").is_dir()
        if args.stop:
            stop_compose_stack(
                console,
                repo_root=repo_path,
                workdir=workdir,
                remove_volumes=args.remove_volumes,
            )
            return
        if args.bundle_reload:
            reload_bundle_from_descriptor(
                console,
                repo_root=repo_path,
                workdir=workdir,
                bundle_id=str(args.bundle_reload).strip(),
            )
            return
        workdir_arg = _arg_provided("--workdir")
        if (
            not args.secrets_set
            and not args.secrets_prompt
            and not args.descriptors_location
            and not (args.dry_run and workdir_arg)
        ):
            workdir = Path(
                Prompt.ask("Compose workdir (config+data root)", default=str(workdir))
            ).expanduser().resolve()

        descriptor_bootstrap = None
        if args.descriptors_location and not args.secrets_set and not args.secrets_prompt:
            descriptor_bootstrap = _stage_descriptor_set(
                repo_root=repo_path,
                workdir=workdir,
                descriptors_location=Path(os.path.expanduser(args.descriptors_location)).expanduser().resolve(),
            )
            assembly_path = descriptor_bootstrap["assembly_path"]
            secrets_path = descriptor_bootstrap["secrets_path"]
            gateway_path = descriptor_bootstrap["gateway_path"]
            bundles_path = descriptor_bootstrap["bundles_path"]
            bundles_secrets_path = descriptor_bootstrap["bundles_secrets_path"]
            assembly = descriptor_bootstrap["assembly"]

            os.environ["KDCUBE_ASSEMBLY_DESCRIPTOR_PATH"] = str(assembly_path)
            os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "1"
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
                latest=bool(args.latest),
                upstream=bool(args.upstream),
                release=str(args.release or "").strip() or None,
            )
            if not reasons:
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
                run_installer(console, repo_path, workdir, install_mode, release_ref, None, args.dry_run)
                return

            console.print("[yellow]Descriptor set is incomplete for non-interactive install; falling back to guided setup.[/yellow]")
            for reason in reasons:
                console.print(f"  - {reason}")

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
            default_assembly = str((workdir / "config" / "assembly.yaml").resolve())
            raw_path = Prompt.ask("Assembly descriptor path (assembly.yaml)", default=default_assembly).strip()
            source_path = Path(os.path.expanduser(raw_path)).expanduser().resolve()
            target_path = Path(default_assembly)
            staged = installer_mod.stage_assembly_descriptor(
                target_path,
                source_path=source_path,
                ai_app_root=repo_path / "app/ai-app",
            )
            user_supplied = source_path.resolve() != target_path.resolve()
            if staged and target_path.exists():
                os.environ["KDCUBE_ASSEMBLY_DESCRIPTOR_PATH"] = str(target_path)
                os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "1" if user_supplied else "0"
                assembly_descriptor_path = target_path
                descriptor = installer_mod.load_release_descriptor(target_path)
                bundles_default = isinstance(descriptor, dict) and bool(descriptor.get("bundles"))
                frontend_default = isinstance(descriptor, dict) and bool(descriptor.get("frontend"))
                # platform.ref is no longer used for source selection (handled by source menu)
            else:
                console.print("[yellow]Assembly template not found; continuing without assembly descriptor.[/yellow]")
                os.environ["KDCUBE_ASSEMBLY_SKIP"] = "1"
                os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "0"

            raw_secrets = Prompt.ask(
                "Secrets descriptor path (secrets.yaml) (leave blank to skip)",
                default="",
            ).strip()
            if raw_secrets:
                secrets_descriptor_path = Path(os.path.expanduser(raw_secrets)).expanduser().resolve()
                if secrets_descriptor_path.exists():
                    os.environ["KDCUBE_SECRETS_DESCRIPTOR_PATH"] = str(secrets_descriptor_path)
                else:
                    console.print("[yellow]Secrets descriptor not found; continuing without secrets descriptor.[/yellow]")

            default_gateway_path = (workdir / "config" / "gateway.yaml").resolve()
            default_gateway = str(default_gateway_path) if default_gateway_path.exists() else ""
            raw_gateway = Prompt.ask(
                "Gateway config path (gateway.yaml) (leave blank to skip)",
                default=default_gateway,
            ).strip()
            if raw_gateway:
                gateway_source = Path(os.path.expanduser(raw_gateway)).expanduser().resolve()
                target_gateway = Path(default_gateway_path)
                installer_mod.stage_gateway_descriptor(
                    target_gateway,
                    source_path=gateway_source,
                    ai_app_root=repo_path / "app/ai-app",
                )
                os.environ["KDCUBE_GATEWAY_DESCRIPTOR_PATH"] = str(target_gateway)

            default_bundles_path = (workdir / "config" / "bundles.yaml").resolve()
            default_bundles = str(default_bundles_path) if default_bundles_path.exists() else ""
            raw_bundles = Prompt.ask(
                "Bundles descriptor path (bundles.yaml) (leave blank to skip)",
                default=default_bundles,
            ).strip()
            if raw_bundles:
                source_path = Path(os.path.expanduser(raw_bundles)).expanduser().resolve()
                target_path = Path(default_bundles_path)
                staged = installer_mod.stage_bundles_descriptor(
                    target_path,
                    source_path=source_path,
                    ai_app_root=repo_path / "app/ai-app",
                )
                if staged and target_path.exists():
                    bundles_descriptor_path = target_path
                    os.environ["KDCUBE_BUNDLES_DESCRIPTOR_PATH"] = str(target_path)
                else:
                    console.print("[yellow]Bundles descriptor not found; continuing without bundles descriptor.[/yellow]")

            default_bundles_secrets_path = (workdir / "config" / "bundles.secrets.yaml").resolve()
            default_bundles_secrets = ""
            raw_bundles_secrets = Prompt.ask(
                "Bundle secrets descriptor path (bundles.secrets.yaml) (leave blank to skip)",
                default=default_bundles_secrets,
            ).strip()
            if raw_bundles_secrets:
                source_path = Path(os.path.expanduser(raw_bundles_secrets)).expanduser().resolve()
                if source_path.exists():
                    bundles_secrets_path = source_path
                    os.environ["KDCUBE_BUNDLES_SECRETS_PATH"] = str(source_path)
                else:
                    console.print("[yellow]Bundles secrets descriptor not found; continuing without it.[/yellow]")

            if bundles_descriptor_path:
                use_bundles_descriptor = True

            if bundles_secrets_path:
                use_bundles_secrets = True

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
                openrouter = Prompt.ask("OpenRouter API key (leave blank to skip)", default="", password=True)
                brave = Prompt.ask("Brave Search API key (leave blank to skip)", default="", password=True)
                if openai:
                    secrets["OPENAI_API_KEY"] = openai
                if anthropic:
                    secrets["ANTHROPIC_API_KEY"] = anthropic
                if openrouter:
                    secrets["OPENROUTER_API_KEY"] = openrouter
                if brave:
                    secrets["BRAVE_API_KEY"] = brave
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
