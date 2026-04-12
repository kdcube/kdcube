# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
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


def export_live_bundle_descriptors(
    console: Console,
    *,
    tenant: str,
    project: str,
    out_dir: Path,
    aws_region: str | None,
    aws_profile: str | None,
    aws_sm_prefix: str | None,
) -> None:
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
            "Bootstrap the authoritative bundle descriptor store first by applying a live bundle update or reset-env."
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
        bundles_items.append({"id": bundle_id, **descriptor})
        bundles_secrets_items.append({"id": bundle_id, "secrets": secrets})

    bundles_payload: dict[str, object] = {"bundles": {"version": "1", "items": bundles_items}}
    if default_bundle_id:
        bundles_payload["bundles"]["default_bundle_id"] = default_bundle_id

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
    console.print(f"[dim]bundles.yaml:[/dim] {bundles_path}")
    console.print(f"[dim]bundles.secrets.yaml:[/dim] {bundles_secrets_path}")
