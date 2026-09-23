"""Host-side command adapter for the durable-authority cutover."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


AddQuietArgument = Callable[[argparse.ArgumentParser], None]
EnvironmentLoader = Callable[..., Mapping[str, str]]
_WRITER_SERVICES = frozenset({"chat-ingress", "chat-proc"})
_CUTOVER_SECRET_ENV = ("SECRETS_TOKEN", "SECRETS_ADMIN_TOKEN")


@dataclass(frozen=True)
class AuthorityCutoverRuntime:
    docker_dir: Path
    config_dir: Path
    env_file: Path


def configure_authority_parser(
    parser: argparse.ArgumentParser,
    *,
    add_quiet: AddQuietArgument,
    default_path: Path,
) -> None:
    """Configure the public ``kdcube authority`` command."""

    def add_runtime_options(command: argparse.ArgumentParser) -> None:
        add_quiet(command)
        command.add_argument("--tenant", default="", help="Local runtime tenant.")
        command.add_argument("--project", default="", help="Local runtime project.")
        command.add_argument(
            "--workdir",
            default=None,
            help="Fully-qualified local runtime workdir.",
        )
        command.add_argument(
            "--path",
            default=str(default_path),
            help="Platform repository path.",
        )
        command.add_argument(
            "--verbose",
            action="store_true",
            help="Show the Docker Compose command and complete operation output.",
        )

    commands = parser.add_subparsers(dest="authority_action", required=True)
    preview = commands.add_parser(
        "preview",
        help="Inventory the reset and write its secret-safe review artifact.",
    )
    add_runtime_options(preview)
    preview.add_argument("--generation-id", required=True)
    preview.add_argument("--preview-file", required=True)

    preflight = commands.add_parser(
        "preflight",
        help="Open the reviewed target without stopping source writers.",
    )
    add_runtime_options(preflight)
    preflight.add_argument("--preview-file", required=True)
    preflight.add_argument("--confirm-preview-sha256", required=True)

    apply = commands.add_parser(
        "apply",
        help="Apply exactly one reviewed preview to PostgreSQL.",
    )
    add_runtime_options(apply)
    apply.add_argument("--preview-file", required=True)
    apply.add_argument("--confirm-preview-sha256", required=True)
    apply.add_argument(
        "--source-quiesced",
        action="store_true",
        help="Confirm source writes are stopped; the command also verifies writer services.",
    )
    apply.add_argument(
        "--stop-writers",
        action="store_true",
        help=(
            "Stop chat-ingress and chat-proc, verify quiescence, and leave them "
            "stopped after the operation."
        ),
    )


def _compose_base(runtime: AuthorityCutoverRuntime) -> list[str]:
    if not runtime.env_file.is_file():
        raise SystemExit(
            f"Compose environment is missing: {runtime.env_file}\n"
            "Initialize the selected runtime before preparing the authority cutover."
        )
    return ["docker", "compose", "--env-file", str(runtime.env_file)]


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _checked_output(
    command: list[str],
    *,
    runtime: AuthorityCutoverRuntime,
    runner: Callable[..., Any],
    label: str,
    verbose: bool,
    process_env: Mapping[str, str] | None = None,
) -> str:
    if verbose:
        print("$ " + " ".join(command))
    if process_env is None:
        result = runner(command, cwd=runtime.docker_dir)
    else:
        result = runner(command, cwd=runtime.docker_dir, env=process_env)
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    if verbose:
        if stdout:
            print(stdout.rstrip())
        if stderr:
            print(stderr.rstrip())
    if int(getattr(result, "returncode", 1)) != 0:
        detail = "\n".join(
            value.strip() for value in (stdout, stderr) if value.strip()
        )
        suffix = f"\n{detail}" if detail else ""
        raise SystemExit(
            f"{label} failed with exit code {result.returncode}.{suffix}"
        )
    return stdout


def _running_services(
    runtime: AuthorityCutoverRuntime,
    *,
    runner: Callable[..., Any],
    verbose: bool,
) -> set[str]:
    output = _checked_output(
        [
            *_compose_base(runtime),
            "ps",
            "--services",
            "--filter",
            "status=running",
        ],
        runtime=runtime,
        runner=runner,
        label="Authority writer-state check",
        verbose=verbose,
    )
    return {line.strip() for line in output.splitlines() if line.strip()}


def _container_command(
    runtime: AuthorityCutoverRuntime,
    *operation: str,
    forward_secret_environment: bool = False,
) -> list[str]:
    secret_environment = (
        [
            "--env",
            "SECRETS_TOKEN",
            "--env",
            "SECRETS_ADMIN_TOKEN",
        ]
        if forward_secret_environment
        else []
    )
    return [
        *_compose_base(runtime),
        "run",
        "--rm",
        "--no-deps",
        *secret_environment,
        "chat-proc",
        "python",
        "-m",
        "kdcube_ai_app.ops.authority_cutover.cli",
        *operation,
    ]


def _inspect_container_environment(
    runtime: AuthorityCutoverRuntime,
    *,
    service: str,
    runner: Callable[..., Any],
) -> dict[str, str]:
    container = runner(
        [*_compose_base(runtime), "ps", "--all", "--quiet", service],
        cwd=runtime.docker_dir,
    )
    if int(getattr(container, "returncode", 1)) != 0:
        raise SystemExit(
            f"Authority cutover could not locate the {service} container."
        )
    container_ids = [
        line.strip()
        for line in str(getattr(container, "stdout", "") or "").splitlines()
        if line.strip()
    ]
    if len(container_ids) != 1:
        raise SystemExit(
            f"Authority cutover requires exactly one {service} container."
        )
    inspected = runner(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
            container_ids[0],
        ],
        cwd=runtime.docker_dir,
    )
    if int(getattr(inspected, "returncode", 1)) != 0:
        raise SystemExit(
            f"Authority cutover could not inspect the {service} service binding."
        )
    values: dict[str, str] = {}
    for line in str(getattr(inspected, "stdout", "") or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _CUTOVER_SECRET_ENV:
            values[key] = value
    return values


def _cutover_process_environment(
    runtime: AuthorityCutoverRuntime,
    *,
    runner: Callable[..., Any],
) -> Mapping[str, str]:
    """Forward existing service gates without persisting or printing values."""

    broker = _inspect_container_environment(
        runtime,
        service="kdcube-secrets",
        runner=runner,
    )
    processor = _inspect_container_environment(
        runtime,
        service="chat-proc",
        runner=runner,
    )
    process_env = dict(os.environ)
    process_env["SECRETS_ADMIN_TOKEN"] = broker.get("SECRETS_ADMIN_TOKEN", "")
    process_env["SECRETS_TOKEN"] = processor.get("SECRETS_TOKEN", "")
    return process_env


def _staging_paths(runtime: AuthorityCutoverRuntime) -> tuple[Path, str]:
    name = f".authority-cutover-{uuid.uuid4().hex}.json"
    return runtime.config_dir / name, f"/config/{name}"


def _copy_new_atomic(source: Path, destination: Path) -> None:
    if destination.exists():
        raise SystemExit(
            f"Preview file already exists: {destination}\n"
            "Choose a new path so reviewed evidence is never overwritten."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _preview(
    args: argparse.Namespace,
    *,
    runtime: AuthorityCutoverRuntime,
    runner: Callable[..., Any],
) -> str:
    destination = Path(args.preview_file).expanduser().resolve()
    staging, container_path = _staging_paths(runtime)
    try:
        output = _checked_output(
            _container_command(
                runtime,
                "preview",
                "--generation-id",
                str(args.generation_id),
                "--preview-file",
                container_path,
            ),
            runtime=runtime,
            runner=runner,
            label="Authority cutover preview",
            verbose=bool(args.verbose),
        )
        if not staging.is_file():
            raise SystemExit(
                "Authority cutover preview succeeded without producing its artifact."
            )
        _copy_new_atomic(staging, destination)
        return output
    finally:
        staging.unlink(missing_ok=True)


def _preflight(
    args: argparse.Namespace,
    *,
    runtime: AuthorityCutoverRuntime,
    runner: Callable[..., Any],
    process_env: Mapping[str, str],
) -> str:
    source = Path(args.preview_file).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Authority preview file is missing: {source}")
    staging, container_path = _staging_paths(runtime)
    try:
        shutil.copyfile(source, staging)
        os.chmod(staging, 0o644)
        return _checked_output(
            _container_command(
                runtime,
                "preflight-target",
                "--preview-file",
                container_path,
                "--confirm-preview-sha256",
                str(args.confirm_preview_sha256),
                forward_secret_environment=True,
            ),
            runtime=runtime,
            runner=runner,
            label="Authority cutover target preflight",
            verbose=bool(args.verbose),
            process_env=process_env,
        )
    finally:
        staging.unlink(missing_ok=True)


def _apply(
    args: argparse.Namespace,
    *,
    runtime: AuthorityCutoverRuntime,
    runner: Callable[..., Any],
    process_env: Mapping[str, str],
) -> str:
    source = Path(args.preview_file).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Authority preview file is missing: {source}")
    stop_writers = bool(getattr(args, "stop_writers", False))
    if not bool(args.source_quiesced) and not stop_writers:
        raise SystemExit(
            "Authority cutover apply requires --stop-writers, or "
            "--source-quiesced after the writer services were stopped explicitly."
        )
    staging, container_path = _staging_paths(runtime)
    try:
        shutil.copyfile(source, staging)
        os.chmod(staging, 0o644)
        _checked_output(
            _container_command(
                runtime,
                "preflight-target",
                "--preview-file",
                container_path,
                "--confirm-preview-sha256",
                str(args.confirm_preview_sha256),
                forward_secret_environment=True,
            ),
            runtime=runtime,
            runner=runner,
            label="Authority cutover target preflight",
            verbose=bool(args.verbose),
            process_env=process_env,
        )
        if stop_writers:
            _checked_output(
                [
                    *_compose_base(runtime),
                    "stop",
                    *sorted(_WRITER_SERVICES),
                ],
                runtime=runtime,
                runner=runner,
                label="Authority writer quiescence",
                verbose=bool(args.verbose),
            )
        running_writers = sorted(
            _WRITER_SERVICES.intersection(
                _running_services(
                    runtime,
                    runner=runner,
                    verbose=bool(args.verbose),
                )
            )
        )
        if running_writers:
            raise SystemExit(
                "Authority source is not quiesced; stop these services first: "
                + ", ".join(running_writers)
            )
        return _checked_output(
            _container_command(
                runtime,
                "apply",
                "--preview-file",
                container_path,
                "--confirm-preview-sha256",
                str(args.confirm_preview_sha256),
                "--source-quiesced",
                forward_secret_environment=True,
            ),
            runtime=runtime,
            runner=runner,
            label="Authority cutover apply",
            verbose=bool(args.verbose),
            process_env=process_env,
        )
    finally:
        staging.unlink(missing_ok=True)


def run_authority_command(
    args: argparse.Namespace,
    *,
    runtime: AuthorityCutoverRuntime,
    runner: Callable[..., Any] = _run,
    environment_loader: EnvironmentLoader = _cutover_process_environment,
) -> str:
    """Run one cutover command through a runtime-scoped one-off container."""

    runtime.config_dir.mkdir(parents=True, exist_ok=True)
    if args.authority_action == "preview":
        return _preview(args, runtime=runtime, runner=runner)
    if args.authority_action == "preflight":
        return _preflight(
            args,
            runtime=runtime,
            runner=runner,
            process_env=environment_loader(runtime, runner=runner),
        )
    if args.authority_action == "apply":
        return _apply(
            args,
            runtime=runtime,
            runner=runner,
            process_env=environment_loader(runtime, runner=runner),
        )
    raise SystemExit("Usage: kdcube authority <preview|preflight|apply> ...")


__all__ = [
    "AuthorityCutoverRuntime",
    "configure_authority_parser",
    "run_authority_command",
]
