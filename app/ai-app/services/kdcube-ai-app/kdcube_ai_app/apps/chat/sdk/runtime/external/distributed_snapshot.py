# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Distributed execution snapshot helpers.

Purpose:
- Zip workdir/outdir with a stable filter
- Upload to storage (S3 or file backend)
- Build canonical execution prefixes
- Download + extract on remote executor
"""

from __future__ import annotations

import io
import os
import pathlib
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.storage.storage import create_storage_backend


_SKIP_DIRS_DEFAULT = {"logs", "executed_programs", "__pycache__", ".pytest_cache", ".git"}
_SKIP_FILES_DEFAULT = {"sources_pool.json", "sources_used.json", "tool_calls_index.json"}


@dataclass
class ExecSnapshotPaths:
    base_prefix: str
    input_work_key: str
    input_out_key: str
    output_work_key: str
    output_out_key: str


@dataclass
class ExecSnapshotInfo:
    storage_uri: str
    base_prefix: str
    input_work_uri: str
    input_out_uri: str
    output_work_uri: str
    output_out_uri: str
    input_work_files: int
    input_out_files: int


@dataclass
class BundleSnapshotInfo:
    bundle_id: str
    bundle_version: str
    bundle_uri: str
    bundle_sha256: str


def build_exec_storage_prefix(
    *,
    tenant: str,
    project: str,
    user_type: str,
    user_or_fp: str,
    conversation_id: str,
    turn_id: str,
    codegen_run_id: str,
    exec_id: str,
) -> str:
    # Keep consistent with ConversationStore hierarchy + add exec_id and input/output subtrees
    return (
        f"cb/tenants/{tenant}/projects/{project}/executions/"
        f"{user_type}/{user_or_fp}/{conversation_id}/{turn_id}/{codegen_run_id}/{exec_id}"
    )


def build_exec_snapshot_paths(base_prefix: str) -> ExecSnapshotPaths:
    return ExecSnapshotPaths(
        base_prefix=base_prefix,
        input_work_key=f"{base_prefix}/input/work.zip",
        input_out_key=f"{base_prefix}/input/out.zip",
        output_work_key=f"{base_prefix}/output/work.zip",
        output_out_key=f"{base_prefix}/output/out.zip",
    )


def _should_skip(path: pathlib.Path, *, skip_dirs: set[str], skip_files: set[str]) -> bool:
    if path.name in skip_files:
        return True
    for part in path.parts:
        if part in skip_dirs:
            return True
    return False


def _zip_dir(
    src_dir: pathlib.Path,
    *,
    skip_dirs: Iterable[str] = _SKIP_DIRS_DEFAULT,
    skip_files: Iterable[str] = _SKIP_FILES_DEFAULT,
) -> Tuple[bytes, int]:
    skip_dirs_set = set(skip_dirs or [])
    skip_files_set = set(skip_files or [])
    count = 0
    with tempfile.NamedTemporaryFile(prefix="exec_snapshot_", suffix=".zip", delete=False) as tmp:
        zip_path = pathlib.Path(tmp.name)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in src_dir.rglob("*"):
                if not p.is_file():
                    continue
                if _should_skip(p, skip_dirs=skip_dirs_set, skip_files=skip_files_set):
                    continue
                rel = str(p.relative_to(src_dir)).replace("\\", "/")
                zf.write(p, arcname=rel)
                count += 1
        data = zip_path.read_bytes()
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
    return data, count


def build_manifest(
    src_dir: pathlib.Path,
    *,
    skip_dirs: Iterable[str] = _SKIP_DIRS_DEFAULT,
    skip_files: Iterable[str] = _SKIP_FILES_DEFAULT,
) -> Dict[str, Dict[str, Any]]:
    skip_dirs_set = set(skip_dirs or [])
    skip_files_set = set(skip_files or [])
    manifest: Dict[str, Dict[str, Any]] = {}
    for p in src_dir.rglob("*"):
        if not p.is_file():
            continue
        if _should_skip(p, skip_dirs=skip_dirs_set, skip_files=skip_files_set):
            continue
        rel = str(p.relative_to(src_dir)).replace("\\", "/")
        st = p.stat()
        manifest[rel] = {"size": int(st.st_size), "mtime": float(st.st_mtime)}
    return manifest


def _zip_dir_delta(
    src_dir: pathlib.Path,
    *,
    baseline: Dict[str, Dict[str, Any]],
    skip_dirs: Iterable[str] = _SKIP_DIRS_DEFAULT,
    skip_files: Iterable[str] = _SKIP_FILES_DEFAULT,
) -> Tuple[bytes, int]:
    skip_dirs_set = set(skip_dirs or [])
    skip_files_set = set(skip_files or [])
    count = 0
    with tempfile.NamedTemporaryFile(prefix="exec_snapshot_", suffix=".zip", delete=False) as tmp:
        zip_path = pathlib.Path(tmp.name)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in src_dir.rglob("*"):
                if not p.is_file():
                    continue
                if _should_skip(p, skip_dirs=skip_dirs_set, skip_files=skip_files_set):
                    continue
                rel = str(p.relative_to(src_dir)).replace("\\", "/")
                st = p.stat()
                prev = baseline.get(rel)
                if prev and prev.get("size") == int(st.st_size) and prev.get("mtime") == float(st.st_mtime):
                    continue
                zf.write(p, arcname=rel)
                count += 1
        data = zip_path.read_bytes()
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
    return data, count


def compute_dir_sha256(
    src_dir: pathlib.Path,
    *,
    skip_dirs: Iterable[str] = _SKIP_DIRS_DEFAULT,
    skip_files: Iterable[str] = _SKIP_FILES_DEFAULT,
) -> str:
    import hashlib
    h = hashlib.sha256()
    skip_dirs_set = set(skip_dirs or [])
    skip_files_set = set(skip_files or [])
    for p in sorted(src_dir.rglob("*")):
        if not p.is_file():
            continue
        if _should_skip(p, skip_dirs=skip_dirs_set, skip_files=skip_files_set):
            continue
        rel = str(p.relative_to(src_dir)).replace("\\", "/").encode("utf-8")
        h.update(rel)
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def _storage_uri() -> str:
    settings = get_settings()
    storage_uri = settings.STORAGE_PATH or os.environ.get("KDCUBE_STORAGE_PATH") or ""
    if not storage_uri:
        raise ValueError("STORAGE_PATH is required for distributed execution snapshots")
    return storage_uri


def _uri_for_path(storage_uri: str, rel_path: str) -> str:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme or "file"
    if scheme == "s3":
        prefix = parsed.path.lstrip("/")
        if prefix:
            return f"s3://{parsed.netloc}/{prefix}/{rel_path}"
        return f"s3://{parsed.netloc}/{rel_path}"
    if scheme == "file":
        base = parsed.path or ""
        return f"file://{os.path.join(base, rel_path)}"
    # fallback
    base = parsed.path.lstrip("/")
    if base:
        return f"{scheme}://{parsed.netloc}/{base}/{rel_path}"
    return f"{scheme}://{parsed.netloc}/{rel_path}"


def _backend_for_uri(uri: str):
    parsed = urlparse(uri)
    scheme = parsed.scheme or "file"
    if scheme == "file":
        return None, pathlib.Path(parsed.path)
    if scheme == "s3":
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        prefix = os.path.dirname(key)
        backend = create_storage_backend(f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}")
        rel = os.path.basename(key)
        return backend, rel
    raise ValueError(f"Unsupported URI scheme: {scheme}")


def write_dir_zip_to_uri(
    uri: str,
    src_dir: pathlib.Path,
    *,
    skip_dirs: Iterable[str] = _SKIP_DIRS_DEFAULT,
    skip_files: Iterable[str] = _SKIP_FILES_DEFAULT,
    baseline: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[str, int]:
    if baseline:
        data, count = _zip_dir_delta(src_dir, baseline=baseline, skip_dirs=skip_dirs, skip_files=skip_files)
    else:
        data, count = _zip_dir(src_dir, skip_dirs=skip_dirs, skip_files=skip_files)
    backend, rel = _backend_for_uri(uri)
    if backend is None:
        dest = pathlib.Path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    else:
        backend.write_bytes(rel, data, meta={"ContentType": "application/zip"})
    return uri, count


def snapshot_exec_input(
    *,
    exec_ctx: Dict[str, Any],
    exec_id: str,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    codegen_run_id: Optional[str] = None,
) -> ExecSnapshotInfo:
    storage_uri = _storage_uri()

    tenant = exec_ctx.get("tenant") or exec_ctx.get("tenant_id") or "unknown"
    project = exec_ctx.get("project") or exec_ctx.get("project_id") or "unknown"
    user_type = exec_ctx.get("user_type") or "registered"
    user_or_fp = exec_ctx.get("user_id") or exec_ctx.get("user") or "unknown"
    conversation_id = exec_ctx.get("conversation_id") or "unknown"
    turn_id = exec_ctx.get("turn_id") or "unknown"
    run_id = codegen_run_id or exec_ctx.get("codegen_run_id") or exec_ctx.get("session_id") or "run"

    base_prefix = build_exec_storage_prefix(
        tenant=tenant,
        project=project,
        user_type=user_type,
        user_or_fp=user_or_fp,
        conversation_id=conversation_id,
        turn_id=turn_id,
        codegen_run_id=run_id,
        exec_id=exec_id,
    )
    paths = build_exec_snapshot_paths(base_prefix)

    backend = create_storage_backend(storage_uri)

    work_bytes, work_count = _zip_dir(workdir)
    out_bytes, out_count = _zip_dir(outdir)

    backend.write_bytes(paths.input_work_key, work_bytes, meta={"ContentType": "application/zip"})
    backend.write_bytes(paths.input_out_key, out_bytes, meta={"ContentType": "application/zip"})

    return ExecSnapshotInfo(
        storage_uri=storage_uri,
        base_prefix=base_prefix,
        input_work_uri=_uri_for_path(storage_uri, paths.input_work_key),
        input_out_uri=_uri_for_path(storage_uri, paths.input_out_key),
        output_work_uri=_uri_for_path(storage_uri, paths.output_work_key),
        output_out_uri=_uri_for_path(storage_uri, paths.output_out_key),
        input_work_files=work_count,
        input_out_files=out_count,
    )


def ensure_bundle_snapshot(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    bundle_root: pathlib.Path,
    bundle_version: Optional[str] = None,
    storage_uri: Optional[str] = None,
) -> BundleSnapshotInfo:
    storage_uri = storage_uri or _storage_uri()

    backend = create_storage_backend(storage_uri)
    version = (bundle_version or "").strip()

    if version:
        rel_zip = (
            f"cb/tenants/{tenant}/projects/{project}/ai-bundle-snapshots/"
            f"{bundle_id}.{version}.zip"
        )
        rel_sha = (
            f"cb/tenants/{tenant}/projects/{project}/ai-bundle-snapshots/"
            f"{bundle_id}.{version}.sha256"
        )
        if backend.exists(rel_zip):
            uri = _uri_for_path(storage_uri, rel_zip)
            return BundleSnapshotInfo(bundle_id=bundle_id, bundle_version=version, bundle_uri=uri, bundle_sha256="")

    data, _ = _zip_dir(bundle_root, skip_dirs=_SKIP_DIRS_DEFAULT, skip_files=set())
    sha256 = _sha256_bytes(data)
    version = version or sha256[:12]

    rel_zip = (
        f"cb/tenants/{tenant}/projects/{project}/ai-bundle-snapshots/"
        f"{bundle_id}.{version}.zip"
    )
    rel_sha = (
        f"cb/tenants/{tenant}/projects/{project}/ai-bundle-snapshots/"
        f"{bundle_id}.{version}.sha256"
    )
    backend.write_bytes(rel_zip, data, meta={"ContentType": "application/zip"})
    backend.write_bytes(rel_sha, (sha256 + "\n").encode("utf-8"), meta={"ContentType": "text/plain"})
    uri = _uri_for_path(storage_uri, rel_zip)

    return BundleSnapshotInfo(bundle_id=bundle_id, bundle_version=version, bundle_uri=uri, bundle_sha256=sha256)


def restore_zip_to_dir(uri: str, dest_dir: pathlib.Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    backend, rel = _backend_for_uri(uri)
    if backend is None:
        zip_path = pathlib.Path(rel)
        data = zip_path.read_bytes()
    else:
        data = backend.read_bytes(rel)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_dir)


def rewrite_runtime_globals_for_bundle(
    runtime_globals: Dict[str, Any],
    *,
    new_bundle_root: pathlib.Path,
) -> Dict[str, Any]:
    out = dict(runtime_globals)
    old_root = out.get("BUNDLE_ROOT_HOST")
    if not old_root:
        return out

    old_root = str(old_root)
    new_root = str(new_bundle_root)

    def _rewrite_path(p: str) -> str:
        return p.replace(old_root, new_root, 1) if p.startswith(old_root) else p

    def _rewrite_tool_file(p: str) -> Optional[str]:
        if p.startswith(old_root):
            return p.replace(old_root, new_root, 1)
        # Paths outside bundle root are not available on remote; force import-by-name
        return None

    tool_files = dict(out.get("TOOL_MODULE_FILES") or {})
    for k, v in list(tool_files.items()):
        if isinstance(v, str):
            tool_files[k] = _rewrite_tool_file(v)
    out["TOOL_MODULE_FILES"] = tool_files

    raw_specs = list(out.get("RAW_TOOL_SPECS") or [])
    for spec in raw_specs:
        if isinstance(spec, dict) and isinstance(spec.get("ref"), str):
            spec["ref"] = _rewrite_path(spec["ref"])
    out["RAW_TOOL_SPECS"] = raw_specs

    out["BUNDLE_ROOT_HOST"] = new_root
    bundle_spec = out.get("BUNDLE_SPEC")
    if isinstance(bundle_spec, dict):
        bundle_spec = dict(bundle_spec)
        if isinstance(bundle_spec.get("path"), str):
            bundle_spec["path"] = new_root
        out["BUNDLE_SPEC"] = bundle_spec

    skills_desc = out.get("SKILLS_DESCRIPTOR")
    if isinstance(skills_desc, dict):
        csr = skills_desc.get("custom_skills_root")
        if isinstance(csr, str) and csr.startswith(old_root):
            skills_desc = dict(skills_desc)
            skills_desc["custom_skills_root"] = csr.replace(old_root, new_root, 1)
            out["SKILLS_DESCRIPTOR"] = skills_desc

    return out


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()
