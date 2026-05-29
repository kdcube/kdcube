# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import posixpath
import subprocess
from pathlib import Path

import yaml
from rich.console import Console


def aws_cli_env(*, region: str | None, profile: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if region:
        env["AWS_REGION"] = region
        env["AWS_DEFAULT_REGION"] = region
    if profile:
        env["AWS_PROFILE"] = profile
    return env


def aws_secret_json(
    *,
    secret_id: str,
    region: str | None,
    profile: str | None,
    required: bool,
) -> dict[str, object] | None:
    cmd = [
        "aws",
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        secret_id,
        "--query",
        "SecretString",
        "--output",
        "text",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=aws_cli_env(region=region, profile=profile),
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if not required and "ResourceNotFoundException" in stderr:
            return None
        raise SystemExit(f"Failed to read AWS secret {secret_id}: {stderr or proc.stdout.strip()}")
    raw = (proc.stdout or "").strip()
    if not raw or raw == "None":
        return {} if required else None
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise SystemExit(f"AWS secret {secret_id} does not contain valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"AWS secret {secret_id} must contain a JSON object.")
    return payload


def resolve_aws_sm_prefix(*, tenant: str, project: str, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip().strip("/")
    tenant_text = str(tenant or "").strip()
    project_text = str(project or "").strip()
    if not tenant_text or not project_text:
        raise SystemExit("--tenant and --project are required unless --aws-sm-prefix is provided.")
    return f"kdcube/{tenant_text}/{project_text}"


def _empty_bundles_secrets_payload() -> dict[str, object]:
    return {
        "bundles": {
            "version": "1",
            "items": [],
        }
    }


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(max(size, 0))
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def _get_nested(data: object, *parts: str) -> object:
    cur = data
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _strip_env_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.strip()


def _load_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _strip_env_value(value)
    return values


def _descriptor_item_refs(data: object) -> list[tuple[str, dict[str, object]]]:
    if not isinstance(data, dict):
        return []
    raw_bundles = data.get("bundles")
    if isinstance(raw_bundles, dict):
        raw_items = raw_bundles.get("items")
        if isinstance(raw_items, list):
            return [
                (str(item.get("id") or "").strip(), item)
                for item in raw_items
                if isinstance(item, dict)
            ]
        items: list[tuple[str, dict[str, object]]] = []
        for key, value in raw_bundles.items():
            if key in {"items", "version", "default_bundle_id"} or not isinstance(value, dict):
                continue
            items.append((str(value.get("id") or key).strip(), value))
        return items
    if isinstance(raw_bundles, list):
        return [
            (str(item.get("id") or "").strip(), item)
            for item in raw_bundles
            if isinstance(item, dict)
        ]
    return []


def _norm_container_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    normalized = posixpath.normpath(text)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _container_path_to_host_path(
    raw_path: object,
    *,
    container_root: str,
    host_root: str,
) -> str | None:
    path = _norm_container_path(raw_path)
    root = _norm_container_path(container_root)
    host = str(host_root or "").strip()
    if not path or not root or not host:
        return None
    if path == root:
        return str(Path(host).expanduser())
    if not path.startswith(root.rstrip("/") + "/"):
        return None
    rel = path[len(root.rstrip("/")) :].lstrip("/")
    return str(Path(host).expanduser().joinpath(*rel.split("/")))


def _normalize_exported_bundle_paths(
    data: object,
    *,
    config_dir: Path | None,
) -> list[dict[str, str]]:
    """Convert runtime-local descriptor paths back to seed-descriptor paths.

    Runtime descriptors consumed by Docker use container-visible paths such as
    /bundles/... or /managed-bundles/.... Exported seed descriptors should use
    host paths for unmanaged local-path bundles. Managed cache paths remain
    container-visible because they are runtime materialization locations, not
    portable source roots. Git-backed descriptors should keep their repo/ref/subdir
    fields and must not export an incidental materialized path.
    """
    if not isinstance(data, dict):
        return []

    assembly_path = config_dir / "assembly.yaml" if config_dir is not None else None
    assembly = yaml.safe_load(assembly_path.read_text()) if assembly_path is not None and assembly_path.exists() else {}
    env = _load_env_values(config_dir / ".env") if config_dir is not None else {}

    host_bundles = str(_get_nested(assembly, "paths", "host_bundles_path") or env.get("HOST_BUNDLES_PATH") or "").strip()
    container_bundles = str(
        _get_nested(assembly, "platform", "services", "proc", "bundles", "bundles_root")
        or env.get("BUNDLES_ROOT")
        or "/bundles"
    ).strip()
    host_managed = str(
        _get_nested(assembly, "paths", "host_managed_bundles_path")
        or env.get("HOST_MANAGED_BUNDLES_PATH")
        or ""
    ).strip()
    container_managed = str(
        _get_nested(assembly, "platform", "services", "proc", "bundles", "managed_bundles_root")
        or env.get("MANAGED_BUNDLES_ROOT")
        or "/managed-bundles"
    ).strip()

    translations: list[dict[str, str]] = []
    for bundle_id, item in _descriptor_item_refs(data):
        if item.get("repo"):
            if "path" in item:
                old = str(item.pop("path") or "")
                translations.append(
                    {
                        "bundle_id": bundle_id,
                        "action": "removed_git_path",
                        "runtime_path": old,
                    }
                )
            continue

        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        if _container_path_to_host_path(
            raw_path,
            container_root=container_managed,
            host_root=host_managed,
        ) is not None:
            translations.append(
                {
                    "bundle_id": bundle_id,
                    "action": "kept_managed_path",
                    "runtime_path": raw_path,
                }
            )
            continue
        host_path = _container_path_to_host_path(
            raw_path,
            container_root=container_bundles,
            host_root=host_bundles,
        )
        source = "bundles"
        if host_path is None:
            continue
        item["path"] = host_path
        translations.append(
            {
                "bundle_id": bundle_id,
                "action": "translated_path",
                "source": source,
                "runtime_path": raw_path,
                "host_path": host_path,
            }
        )
    return translations


def _export_live_bundle_descriptors_from_files(
    console: Console,
    *,
    bundles_path: Path,
    bundles_secrets_path: Path | None,
    out_dir: Path,
) -> None:
    if not bundles_path.exists():
        raise SystemExit(f"Local bundles descriptor not found: {bundles_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_bundles_path = out_dir / "bundles.yaml"
    bundles_data = yaml.safe_load(bundles_path.read_text()) or {}
    translations = _normalize_exported_bundle_paths(
        bundles_data,
        config_dir=bundles_path.parent,
    )
    out_bundles_path.write_text(
        yaml.safe_dump(bundles_data, sort_keys=False, allow_unicode=True)
    )

    out_bundles_secrets_path = out_dir / "bundles.secrets.yaml"
    if bundles_secrets_path is not None and bundles_secrets_path.exists():
        out_bundles_secrets_path.write_text(bundles_secrets_path.read_text())
    else:
        out_bundles_secrets_path.write_text(
            yaml.safe_dump(_empty_bundles_secrets_payload(), sort_keys=False, allow_unicode=True)
        )

    console.print("[green]Exported effective live bundle descriptors.[/green]")
    console.print(f"[dim]Authority:[/dim] mounted local descriptors")
    console.print(f"[dim]source bundles.yaml:[/dim] {bundles_path}")
    if bundles_secrets_path is not None and bundles_secrets_path.exists():
        console.print(f"[dim]source bundles.secrets.yaml:[/dim] {bundles_secrets_path}")
    else:
        console.print("[dim]source bundles.secrets.yaml:[/dim] <not configured>")
    if translations:
        console.print("[dim]path normalization:[/dim]")
        for item in translations:
            action = item.get("action")
            bundle_id = item.get("bundle_id") or "<unknown>"
            if action == "removed_git_path":
                console.print(f"[dim]  {bundle_id}: removed git materialized path[/dim]")
            elif action == "kept_managed_path":
                console.print(f"[dim]  {bundle_id}: kept managed cache path {item.get('runtime_path')}[/dim]")
            elif action == "translated_path":
                console.print(
                    f"[dim]  {bundle_id}: {item.get('runtime_path')} -> {item.get('host_path')}[/dim]"
                )
    console.print(
        f"[green]created bundles.yaml:[/green] {out_bundles_path} "
        f"({_format_bytes(out_bundles_path.stat().st_size)})"
    )
    console.print(
        f"[green]created bundles.secrets.yaml:[/green] {out_bundles_secrets_path} "
        f"({_format_bytes(out_bundles_secrets_path.stat().st_size)})"
    )


def export_live_bundle_descriptors(
    console: Console,
    *,
    tenant: str,
    project: str,
    out_dir: Path,
    aws_region: str | None,
    aws_profile: str | None,
    aws_sm_prefix: str | None,
    bundles_path: Path | None = None,
    bundles_secrets_path: Path | None = None,
) -> None:
    if bundles_path is not None:
        _export_live_bundle_descriptors_from_files(
            console,
            bundles_path=bundles_path,
            bundles_secrets_path=bundles_secrets_path,
            out_dir=out_dir,
        )
        return

    prefix = resolve_aws_sm_prefix(tenant=tenant, project=project, explicit=aws_sm_prefix)
    meta_secret_id = f"{prefix}/bundles-meta"
    meta = aws_secret_json(
        secret_id=meta_secret_id,
        region=aws_region,
        profile=aws_profile,
        required=True,
    )
    bundle_ids = [
        str(item).strip()
        for item in (meta.get("bundle_ids") or [])
        if str(item).strip()
    ]
    if not bundle_ids:
        raise SystemExit(
            f"{meta_secret_id} does not contain bundle_ids. "
            "Bootstrap the authoritative bundle descriptor store first by applying a live bundle update or reload-authority."
        )

    default_bundle_id = str(meta.get("default_bundle_id") or "").strip() or None
    bundles_items: list[dict[str, object]] = []
    bundles_secrets_items: list[dict[str, object]] = []

    for bundle_id in bundle_ids:
        descriptor = aws_secret_json(
            secret_id=f"{prefix}/bundles/{bundle_id}/descriptor",
            region=aws_region,
            profile=aws_profile,
            required=True,
        ) or {}
        secrets = aws_secret_json(
            secret_id=f"{prefix}/bundles/{bundle_id}/secrets",
            region=aws_region,
            profile=aws_profile,
            required=False,
        ) or {}
        descriptor_payload = {"id": bundle_id, **descriptor}
        props = descriptor_payload.pop("props", None)
        if isinstance(props, dict) and props:
            descriptor_payload["config"] = props
        bundles_items.append(descriptor_payload)
        bundles_secrets_items.append({"id": bundle_id, "secrets": secrets})

    bundles_payload: dict[str, object] = {"bundles": {"version": "1", "items": bundles_items}}
    if default_bundle_id:
        bundles_payload["bundles"]["default_bundle_id"] = default_bundle_id
    translations = _normalize_exported_bundle_paths(
        bundles_payload,
        config_dir=None,
    )

    bundles_secrets_payload: dict[str, object] = {
        "bundles": {"version": "1", "items": bundles_secrets_items}
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_path = out_dir / "bundles.yaml"
    bundles_secrets_path = out_dir / "bundles.secrets.yaml"
    bundles_path.write_text(yaml.safe_dump(bundles_payload, sort_keys=False, allow_unicode=True))
    bundles_secrets_path.write_text(
        yaml.safe_dump(bundles_secrets_payload, sort_keys=False, allow_unicode=True)
    )

    console.print("[green]Exported effective live bundle descriptors.[/green]")
    console.print(f"[dim]AWS SM prefix:[/dim] {prefix}")
    if translations:
        console.print("[dim]path normalization:[/dim]")
        for item in translations:
            action = item.get("action")
            bundle_id = item.get("bundle_id") or "<unknown>"
            if action == "removed_git_path":
                console.print(f"[dim]  {bundle_id}: removed git materialized path[/dim]")
    console.print(
        f"[green]created bundles.yaml:[/green] {bundles_path} "
        f"({_format_bytes(bundles_path.stat().st_size)})"
    )
    console.print(
        f"[green]created bundles.secrets.yaml:[/green] {bundles_secrets_path} "
        f"({_format_bytes(bundles_secrets_path.stat().st_size)})"
    )
