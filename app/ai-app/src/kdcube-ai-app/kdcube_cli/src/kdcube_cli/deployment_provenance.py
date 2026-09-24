# SPDX-License-Identifier: MIT
"""Durable source receipts for locally built and running Docker images."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

RECEIPT_SCHEMA = "kdcube.deployment-images.v1"
RECEIPT_RELATIVE_PATH = Path(".kdcube") / "deployment-images.v1.json"
SOURCE_MARKER_SCHEMA = "kdcube.platform-source.v1"
SOURCE_MARKER_RELATIVE_PATH = Path(".kdcube") / "platform-source.v1.json"
COMMAND_TIMEOUT_SECONDS = 30
PLATFORM_SERVICES = {
    "chat-ingress",
    "chat-proc",
    "kdcube-secrets",
    "metrics",
    "postgres-setup",
    "proxylogin",
    "web-proxy",
    "web-ui",
}


class DeploymentProvenanceError(RuntimeError):
    """A source or image receipt could not be observed or persisted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_output(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentProvenanceError(
            f"Could not run {' '.join(command)}: {type(exc).__name__}: {exc}"
        ) from exc
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise DeploymentProvenanceError(
            f"{' '.join(command)} exited {result.returncode}"
            f"{': ' + details if details else ''}"
        )
    return result.stdout or ""


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        return _run_output(["git", "-C", str(repo_root), *args]).strip()
    except DeploymentProvenanceError:
        return None


def _hash_paths(root: Path, relative_paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted({str(value) for value in relative_paths if str(value)}):
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        path = root / relative_path
        if not path.exists() and not path.is_symlink():
            continue
        digest.update(relative.replace(os.sep, "/").encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\n")
    return digest.hexdigest()


def _fallback_tree_paths(root: Path) -> list[str]:
    ignored = {
        ".git",
        ".kdcube",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".vite",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
    paths: list[str] = []
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in ignored for part in relative.parts):
            continue
        if path.is_file() or path.is_symlink():
            paths.append(relative.as_posix())
    return paths


def _dirty_git_content_digest(root: Path, commit: str) -> str:
    diff = _git_output(root, "diff", "--binary", "--no-ext-diff", "HEAD", "--")
    untracked = _git_output(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    if diff is None or untracked is None:
        raise DeploymentProvenanceError(
            f"Could not fingerprint dirty platform source at {root}"
        )
    digest = hashlib.sha256()
    digest.update(commit.encode("ascii"))
    digest.update(b"\0diff\0")
    digest.update(diff.encode("utf-8"))
    digest.update(b"\0untracked\0")
    digest.update(_hash_paths(root, untracked.split("\0")).encode("ascii"))
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def platform_source_identity(repo_root: Path) -> dict[str, Any]:
    """Name the selected platform source by commit and dirty content digest."""

    root = Path(repo_root).expanduser().resolve()
    commit = _git_output(root, "rev-parse", "HEAD")
    if commit:
        status = _git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
        if status is None:
            raise DeploymentProvenanceError(
                f"Could not inspect platform source status at {root}"
            )
        dirty = bool(status)
        repository = _git_output(root, "config", "--get", "remote.origin.url") or ""
        identity: dict[str, Any] = {
            "mode": "git",
            "commit": commit,
            "dirty": dirty,
            "path": str(root),
        }
        if repository:
            identity["repository"] = repository
        if dirty:
            content_digest = _dirty_git_content_digest(root, commit)
            identity["content_sha256"] = content_digest
            identity["version"] = f"git:{commit}+dirty:{content_digest}"
        else:
            identity["version"] = f"git:{commit}"
        return identity

    if _git_output(root, "rev-parse", "--git-dir"):
        listed = _git_output(
            root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        )
        if listed is not None:
            content_digest = _hash_paths(root, listed.split("\0"))
            return {
                "mode": "git-worktree",
                "dirty": True,
                "path": str(root),
                "content_sha256": content_digest,
                "version": f"git-worktree:{content_digest}",
            }

    marker = _read_json(root / SOURCE_MARKER_RELATIVE_PATH)
    marker_source = marker.get("source") if isinstance(marker, dict) else None
    if (
        isinstance(marker, dict)
        and marker.get("schema") == SOURCE_MARKER_SCHEMA
        and isinstance(marker_source, dict)
        and marker_source.get("version")
    ):
        return dict(marker_source)

    if not root.is_dir():
        raise DeploymentProvenanceError(f"Platform source directory does not exist: {root}")
    content_digest = _hash_paths(root, _fallback_tree_paths(root))
    return {
        "mode": "tree",
        "path": str(root),
        "content_sha256": content_digest,
        "version": f"tree:{content_digest}",
    }


def release_source_identity(*, repository: str, ref: str) -> dict[str, Any]:
    clean_repository = str(repository or "").strip()
    clean_ref = str(ref or "").strip()
    return {
        "mode": "release",
        "repository": clean_repository,
        "ref": clean_ref,
        "version": f"release:{clean_repository}@{clean_ref}",
    }


def selected_platform_source_identity(
    repo_root: Path,
    *,
    install_metadata: Mapping[str, Any] | None = None,
    declared_platform_ref: str | None = None,
) -> dict[str, Any]:
    """Name the source selected for this runtime installation."""

    metadata = dict(install_metadata or {})
    if str(metadata.get("install_mode") or "").strip().lower() == "release":
        ref = str(declared_platform_ref or metadata.get("platform_ref") or "").strip()
        if not ref:
            raise DeploymentProvenanceError(
                "Release install metadata does not name platform_ref"
            )
        repository = str(metadata.get("dockerhub_namespace") or "kdcube").strip()
        return release_source_identity(repository=repository, ref=ref)
    return platform_source_identity(repo_root)


def write_platform_source_marker(target_root: Path, source: Mapping[str, Any]) -> Path:
    path = Path(target_root).expanduser().resolve() / SOURCE_MARKER_RELATIVE_PATH
    _atomic_write_json(
        path,
        {
            "schema": SOURCE_MARKER_SCHEMA,
            "source": dict(source),
        },
    )
    return path


def image_receipt_path(workdir: Path) -> Path:
    return Path(workdir).expanduser().resolve() / RECEIPT_RELATIVE_PATH


def load_image_receipts(workdir: Path) -> dict[str, Any]:
    receipt = _read_json(image_receipt_path(workdir))
    if receipt is None or receipt.get("schema") != RECEIPT_SCHEMA:
        return {
            "schema": RECEIPT_SCHEMA,
            "images": {},
            "latest_services": {},
        }
    if not isinstance(receipt.get("images"), dict) or not isinstance(
        receipt.get("latest_services"), dict
    ):
        raise DeploymentProvenanceError(
            f"Deployment image receipt is invalid: {image_receipt_path(workdir)}"
        )
    return receipt


def _compose_environment(env_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["COMPOSE_ENV_FILES"] = str(env_file)
    return env


def _compose_configuration(
    *,
    docker_dir: Path,
    env_file: Path,
    profile_args: Sequence[str] = (),
) -> dict[str, Any]:
    raw = _run_output(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            *profile_args,
            "config",
            "--format",
            "json",
        ],
        cwd=docker_dir,
        env=_compose_environment(env_file),
    )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DeploymentProvenanceError("docker compose config returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        raise DeploymentProvenanceError("docker compose config did not return a services mapping")
    return payload


def _inspect_image_id(reference: str, *, strict: bool = True) -> str | None:
    try:
        value = _run_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", reference]
        ).strip()
    except DeploymentProvenanceError:
        if strict:
            raise
        return None
    return value or None


def record_compose_image_receipts(
    *,
    workdir: Path,
    docker_dir: Path,
    env_file: Path,
    repo_root: Path | None = None,
    source: Mapping[str, Any] | None = None,
    services: Iterable[str] | None = None,
    profile_args: Sequence[str] = (),
    extra_images: Mapping[str, str] | None = None,
    running_only: bool = False,
) -> dict[str, Any]:
    """Record actual image IDs after a successful build or release pull."""

    source_identity = dict(source or {})
    if not source_identity:
        if repo_root is None:
            raise DeploymentProvenanceError("repo_root or source is required for an image receipt")
        source_identity = platform_source_identity(repo_root)

    configuration = _compose_configuration(
        docker_dir=Path(docker_dir),
        env_file=Path(env_file),
        profile_args=profile_args,
    )
    configured = configuration["services"]
    selected = {str(value) for value in services} if services is not None else {
        str(name)
        for name, details in configured.items()
        if isinstance(details, dict) and details.get("build") is not None
    }
    if running_only:
        running_services = {
            str(item.get("Service") or item.get("service") or "").strip()
            for item in _compose_processes(
                docker_dir=Path(docker_dir),
                env_file=Path(env_file),
            )
            if str(item.get("State") or item.get("state") or "").strip().lower()
            == "running"
        }
        selected.intersection_update({value for value in running_services if value})
    references: dict[str, str] = {}
    for service in sorted(selected):
        details = configured.get(service)
        reference = str(details.get("image") or "").strip() if isinstance(details, dict) else ""
        if reference:
            references[service] = reference
    missing_references = sorted(selected.difference(references))
    if missing_references:
        raise DeploymentProvenanceError(
            "Compose config has no image reference for built service(s): "
            + ", ".join(missing_references)
        )
    references.update(
        {
            str(service): str(reference)
            for service, reference in dict(extra_images or {}).items()
            if str(service).strip() and str(reference).strip()
        }
    )
    if not references:
        raise DeploymentProvenanceError("No built image references were found in compose config")

    receipt = load_image_receipts(workdir)
    images = receipt["images"]
    latest = receipt["latest_services"]
    recorded_at = _utc_now()
    for service, reference in sorted(references.items()):
        image_id = _inspect_image_id(reference)
        if not image_id:
            raise DeploymentProvenanceError(f"Docker returned no image ID for {reference}")
        previous = images.get(image_id) if isinstance(images.get(image_id), dict) else {}
        service_names = sorted({*previous.get("services", []), service})
        image_references = sorted({*previous.get("references", []), reference})
        images[image_id] = {
            "image_id": image_id,
            "references": image_references,
            "services": service_names,
            "source": source_identity,
            "recorded_at": recorded_at,
        }
        latest[service] = {
            "service": service,
            "image": reference,
            "image_id": image_id,
            "source": source_identity,
            "recorded_at": recorded_at,
        }

    receipt["updated_at"] = recorded_at
    _atomic_write_json(image_receipt_path(workdir), receipt)
    return receipt


def _json_records(raw: str) -> list[dict[str, Any]]:
    clean = str(raw or "").strip()
    if not clean:
        return []
    try:
        parsed = json.loads(clean)
    except ValueError:
        records: list[dict[str, Any]] = []
        for line in clean.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records
    if isinstance(parsed, list):
        return [value for value in parsed if isinstance(value, dict)]
    return [parsed] if isinstance(parsed, dict) else []


def _compose_processes(*, docker_dir: Path, env_file: Path) -> list[dict[str, Any]]:
    raw = _run_output(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "ps",
            "--format",
            "json",
        ],
        cwd=docker_dir,
        env=_compose_environment(env_file),
    )
    return _json_records(raw)


def _inspect_containers(identifiers: Sequence[str]) -> list[dict[str, Any]]:
    if not identifiers:
        return []
    return _json_records(_run_output(["docker", "inspect", *identifiers]))


def _comparison(
    expected_field: str,
    expected: Any,
    actual_field: str,
    actual: Any,
) -> dict[str, Any]:
    if expected in (None, "") or actual in (None, ""):
        status = "UNKNOWN"
    elif expected == actual:
        status = "MATCH"
    else:
        status = "MISMATCH"
    return {
        "status": status,
        "expected_field": expected_field,
        "expected": expected,
        "actual_field": actual_field,
        "actual": actual,
    }


def _overall_status(comparisons: Iterable[Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status") or "UNKNOWN") for item in comparisons}
    if "MISMATCH" in statuses:
        return "MISMATCH"
    if "UNKNOWN" in statuses or not statuses:
        return "UNKNOWN"
    return "MATCH"


def collect_running_service_attestation(
    *,
    workdir: Path,
    docker_dir: Path,
    env_file: Path,
    repo_root: Path,
    install_metadata: Mapping[str, Any] | None = None,
    declared_platform_ref: str | None = None,
) -> dict[str, Any]:
    """Report each running container's image ID and the source that built it."""

    source_error: str | None = None
    try:
        selected_source = selected_platform_source_identity(
            repo_root,
            install_metadata=install_metadata,
            declared_platform_ref=declared_platform_ref,
        )
    except DeploymentProvenanceError as exc:
        source_error = str(exc)
        selected_source = {
            "mode": "unavailable",
            "path": str(Path(repo_root).expanduser().resolve()),
            "version": None,
            "error": source_error,
        }
    receipt = load_image_receipts(workdir)
    processes = _compose_processes(docker_dir=Path(docker_dir), env_file=Path(env_file))
    running_processes = [
        item
        for item in processes
        if str(item.get("State") or item.get("state") or "").strip().lower() == "running"
    ]
    identifiers = [
        str(item.get("ID") or item.get("Id") or item.get("Name") or "").strip()
        for item in running_processes
    ]
    inspected = _inspect_containers([value for value in identifiers if value])
    by_identifier: dict[str, dict[str, Any]] = {}
    for item in inspected:
        for key in (item.get("Id"), str(item.get("Name") or "").lstrip("/")):
            if key:
                by_identifier[str(key)] = item

    services: list[dict[str, Any]] = []
    for process in sorted(
        running_processes,
        key=lambda item: str(item.get("Service") or item.get("Name") or ""),
    ):
        identifier = str(
            process.get("ID") or process.get("Id") or process.get("Name") or ""
        ).strip()
        container = by_identifier.get(identifier)
        if container is None:
            container = by_identifier.get(str(process.get("Name") or "").lstrip("/"), {})
        config = container.get("Config") if isinstance(container.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        service = str(
            process.get("Service")
            or labels.get("com.docker.compose.service")
            or process.get("Name")
            or "unknown"
        )
        actual_image_id = str(container.get("Image") or "").strip() or None
        image_reference = str(config.get("Image") or process.get("Image") or "").strip() or None
        reference_image_id = (
            _inspect_image_id(image_reference, strict=False) if image_reference else None
        )
        latest = receipt["latest_services"].get(service)
        if not isinstance(latest, dict):
            latest = None
        historical = receipt["images"].get(actual_image_id) if actual_image_id else None
        if not isinstance(historical, dict):
            historical = None
        deployed_receipt = (
            latest
            if latest is not None and latest.get("image_id") == actual_image_id
            else historical
        )
        deployed_source = (
            dict(deployed_receipt.get("source") or {})
            if isinstance(deployed_receipt, dict)
            else {
                "mode": "image-reference",
                "reference": image_reference,
                "image_id": actual_image_id,
                "version": actual_image_id,
            }
        )
        comparisons = [
            _comparison(
                "configured_image.image_id",
                reference_image_id,
                "running_container.image_id",
                actual_image_id,
            )
        ]
        if latest is not None:
            comparisons.append(
                _comparison(
                    "latest_build.image_id",
                    latest.get("image_id"),
                    "running_container.image_id",
                    actual_image_id,
                )
            )
        if isinstance(deployed_receipt, dict):
            comparisons.append(
                _comparison(
                    "selected_source.version",
                    selected_source.get("version"),
                    "deployed_source.version",
                    deployed_source.get("version"),
                )
            )
        elif service in PLATFORM_SERVICES:
            comparisons.append(
                _comparison(
                    "selected_source.version",
                    selected_source.get("version"),
                    "deployed_source.version",
                    None,
                )
            )
        services.append(
            {
                "service": service,
                "container": str(container.get("Name") or process.get("Name") or "").lstrip("/"),
                "image": image_reference,
                "image_id": actual_image_id,
                "source": deployed_source,
                "status": _overall_status(comparisons),
                "comparisons": comparisons,
            }
        )

    result = {
        "schema": "kdcube.running-service-attestation.v1",
        "status": _overall_status(
            {"status": item.get("status")} for item in services
        ),
        "selected_source": selected_source,
        "receipt_path": str(image_receipt_path(workdir)),
        "services": services,
    }
    if source_error:
        result["source_error"] = source_error
    return result
