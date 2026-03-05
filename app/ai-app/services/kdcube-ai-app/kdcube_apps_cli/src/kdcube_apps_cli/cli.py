# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt

from kdcube_apps_cli.banner import print_cli_banner


DEFAULT_REPO = "https://github.com/kdcube/kdcube-ai-app.git"
DEFAULT_DIR = Path.home() / ".kdcube" / "kdcube-ai-app"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


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
        local_ref = _read_local_ref(target)
        remote_ref = _read_remote_ref(target)
        if remote_ref and remote_ref != local_ref:
            console.print(f"[yellow]New platform release available:[/yellow] {remote_ref}")
            if local_ref:
                console.print(f"[dim]Installed release:[/dim] {local_ref}")
        if Confirm.ask("Pull latest changes?", default=bool(remote_ref and remote_ref != local_ref)):
            run(["git", "pull"], cwd=target)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"Cloning {repo} to {target}")
    run(["git", "clone", repo, str(target)])


def run_installer(console: Console, repo_root: Path) -> None:
    installer = repo_root / "app/ai-app/deployment/docker/all_in_one_kdcube/kdcube-cli.py"
    if not installer.exists():
        raise SystemExit(f"Installer not found at {installer}")
    console.print("Launching setup wizard...")
    run([sys.executable, str(installer)])


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
    args = parser.parse_args()

    repo_path = Path(os.path.expanduser(args.path)).resolve()
    try:
        ensure_repo(console, args.repo, repo_path)
        run_installer(console, repo_path)
    except FileNotFoundError as exc:
        raise SystemExit("Missing dependency. Please install Git and Python.") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}.") from exc


if __name__ == "__main__":
    main()
