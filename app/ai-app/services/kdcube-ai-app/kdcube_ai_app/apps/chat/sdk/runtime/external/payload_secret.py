# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional


def _session_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    profile = (os.environ.get("AWS_PROFILE") or "").strip()
    if profile:
        kwargs["profile_name"] = profile
    return kwargs


def _region_name(explicit_region: Optional[str] = None) -> Optional[str]:
    return (
        (explicit_region or "").strip()
        or (os.environ.get("SECRETS_SM_REGION") or "").strip()
        or (os.environ.get("AWS_REGION") or "").strip()
        or (os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        or None
    )


def _payload_secret_name(exec_id: str, *, prefix: Optional[str] = None) -> str:
    base_prefix = (prefix or os.environ.get("SECRETS_SM_PREFIX") or "kdcube").strip("/") or "kdcube"
    safe_exec_id = re.sub(r"[^A-Za-z0-9/_+=.@-]+", "-", exec_id or "run").strip("-") or "run"
    return f"{base_prefix}/runtime/exec-payloads/{safe_exec_id}"


def _client(region_name: Optional[str] = None):
    import boto3  # type: ignore

    session = boto3.Session(**_session_kwargs())
    return session.client("secretsmanager", region_name=_region_name(region_name))


def put_exec_payload_secret(
    *,
    exec_id: str,
    payload: Dict[str, Any],
    prefix: Optional[str] = None,
    region_name: Optional[str] = None,
) -> str:
    secret_name = _payload_secret_name(exec_id, prefix=prefix)
    secret_string = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    client = _client(region_name)
    try:
        client.put_secret_value(SecretId=secret_name, SecretString=secret_string)
    except Exception as exc:
        code = str((((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")) or "")
        if code != "ResourceNotFoundException":
            raise
        client.create_secret(Name=secret_name, SecretString=secret_string)
    return secret_name


def get_exec_payload_secret(
    *,
    secret_id: str,
    region_name: Optional[str] = None,
) -> Dict[str, Any]:
    response = _client(region_name).get_secret_value(SecretId=secret_id)
    raw = response.get("SecretString") or ""
    data = json.loads(raw) if raw else {}
    return data if isinstance(data, dict) else {}


def delete_exec_payload_secret(
    *,
    secret_id: str,
    region_name: Optional[str] = None,
) -> None:
    try:
        _client(region_name).delete_secret(
            SecretId=secret_id,
            ForceDeleteWithoutRecovery=True,
        )
    except Exception as exc:
        code = str((((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")) or "")
        if code in {"ResourceNotFoundException", "InvalidRequestException"}:
            return
        raise
