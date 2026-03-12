# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from kdcube_cli.banner import print_cli_banner
from kdcube_cli import installer as installer_mod


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
    lib_root = ai_app_root / "services/kdcube-ai-app"
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
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return Prompt.ask(title, choices=options, default=options[default_index])
    try:
        from readchar import readkey, key
    except Exception:
        return Prompt.ask(title, choices=options, default=options[default_index])

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


def ensure_repo(console: Console, repo: str, target: Path) -> None:
    if target.exists() and (target / ".git").is_dir():
        console.print(f"Repo already exists at {target}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"Cloning {repo} to {target}")
    run(["git", "clone", repo, str(target)])


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
        if args.proxy_ssl and args.no_proxy_ssl:
            raise SystemExit("Choose only one of --proxy-ssl or --no-proxy-ssl.")
        if args.proxy_ssl:
            os.environ["KDCUBE_PROXY_SSL"] = "1"
        elif args.no_proxy_ssl:
            os.environ["KDCUBE_PROXY_SSL"] = "0"
        if args.dry_run_print_env:
            os.environ["KDCUBE_DRY_RUN_PRINT_ENV"] = "1"
        workdir = Path(os.path.expanduser(args.workdir)).expanduser().resolve()
        workdir_arg = _arg_provided("--workdir")
        if not args.secrets_set and not args.secrets_prompt and not (args.dry_run and workdir_arg):
            workdir = Path(
                Prompt.ask("Compose workdir (config+data root)", default=str(workdir))
            ).expanduser().resolve()

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
        if not args.secrets_set and not args.secrets_prompt:
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

        def _is_git_repo(path: Path) -> bool:
            return path.exists() and (path / ".git").is_dir()

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
