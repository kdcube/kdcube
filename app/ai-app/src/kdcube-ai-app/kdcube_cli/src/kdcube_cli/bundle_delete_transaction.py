# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BundleDeleteTransaction:
    bundle_id: str
    descriptor_fingerprint: str
    operation_id: str
    purge_data: bool
    phase: str
    created_at: float
    updated_at: float


def descriptor_fingerprint(item: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(item), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _transaction_path(workdir: Path, bundle_id: str) -> Path:
    digest = hashlib.sha256(bundle_id.encode("utf-8")).hexdigest()[:20]
    return Path(workdir) / "data" / "cli" / "bundle-delete" / f"{digest}.json"


def _write(path: Path, transaction: BundleDeleteTransaction) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **asdict(transaction)}
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_bundle_delete_transaction(
    *,
    workdir: Path,
    bundle_id: str,
) -> BundleDeleteTransaction | None:
    path = _transaction_path(workdir, bundle_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.pop("schema_version")) != SCHEMA_VERSION:
            return None
        transaction = BundleDeleteTransaction(**payload)
    except (OSError, TypeError, ValueError, KeyError):
        return None
    return transaction if transaction.bundle_id == bundle_id else None


def begin_bundle_delete_transaction(
    *,
    workdir: Path,
    bundle_id: str,
    descriptor_item: Mapping[str, Any],
    purge_data: bool,
) -> BundleDeleteTransaction:
    fingerprint = descriptor_fingerprint(descriptor_item)
    current = load_bundle_delete_transaction(workdir=workdir, bundle_id=bundle_id)
    if (
        current is not None
        and current.descriptor_fingerprint == fingerprint
        and current.purge_data == bool(purge_data)
        and current.phase in {"requested", "cleanup_incomplete", "deprovisioned"}
    ):
        return current

    now = time.time()
    transaction = BundleDeleteTransaction(
        bundle_id=bundle_id,
        descriptor_fingerprint=fingerprint,
        operation_id=str(uuid.uuid4()),
        purge_data=bool(purge_data),
        phase="requested",
        created_at=now,
        updated_at=now,
    )
    _write(_transaction_path(workdir, bundle_id), transaction)
    return transaction


def advance_bundle_delete_transaction(
    *,
    workdir: Path,
    transaction: BundleDeleteTransaction,
    phase: str,
) -> BundleDeleteTransaction:
    updated = replace(transaction, phase=str(phase), updated_at=time.time())
    _write(_transaction_path(workdir, transaction.bundle_id), updated)
    return updated


def clear_bundle_delete_transaction(*, workdir: Path, bundle_id: str) -> None:
    try:
        _transaction_path(workdir, bundle_id).unlink(missing_ok=True)
    except OSError:
        return
