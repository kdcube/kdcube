# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt


DEFAULT_REPO = "https://github.com/elenaviter/kdcube-ai-app.git"
DEFAULT_DIR = Path.home() / ".kdcube" / "kdcube-ai-app"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_repo(console: Console, repo: str, target: Path) -> None:
    if target.exists() and (target / ".git").is_dir():
        console.print(f"Repo already exists at {target}")
        if Confirm.ask("Pull latest changes?", default=True):
            run(["git", "pull"], cwd=target)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"Cloning {repo} to {target}")
    run(["git", "clone", repo, str(target)])


def run_installer(console: Console, repo_root: Path) -> None:
    installer = repo_root / "app/ai-app/deployment/docker/all_in_one/kdcube-cli.py"
    if not installer.exists():
        raise SystemExit(f"Installer not found at {installer}")
    console.print("Launching setup wizard...")
    run([sys.executable, str(installer)])


def main() -> None:
    console = Console()
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
