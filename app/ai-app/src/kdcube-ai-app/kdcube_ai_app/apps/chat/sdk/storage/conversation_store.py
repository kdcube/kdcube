# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/storage/storage.py

import json, time, os, pathlib, tempfile, zipfile
from typing import Optional, Tuple, List, Dict, Any
from urllib.parse import urlparse, unquote

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.service_hub.inventory import _mid
from kdcube_ai_app.apps.chat.sdk.storage.rn import (
    rn_message, rn_attachment, rn_execution_file, rn_file
)

MAX_CONCURRENT_ARTIFACT_FETCHES = 16

try:
    from kdcube_ai_app.storage.storage import create_storage_backend
except ImportError:
    raise ImportError("Please ensure 'kdcube_ai_app.storage.storage' is importable.")

_JSON_META = {"ContentType": "application/json"}

async def attachment_rn_and_rel_name(tenant, project, user_or_fp, conversation_id, turn_id, role, filename: str):
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    safe_name = os.path.basename(filename) or "file.bin"
    rel_name = f"{ts}-{safe_name}"
    rn = rn_attachment(tenant, project, user_or_fp, conversation_id, turn_id, role, rel_name)
    return rn, rel_name

async def file_rn_and_rel_name(tenant, project, user_or_fp, conversation_id, turn_id, role, filename: str):
    safe_name = os.path.basename(filename) or "file.bin"
    rel_name = safe_name
    rn = rn_file(tenant, project, user_or_fp, conversation_id, turn_id, role, rel_name)
    return rn, rel_name

class ConversationStore:
    """
    Root: ${KDCUBE_STORAGE_PATH}/cb
    Messages:
      cb/tenants/{tenant}/projects/{project}/conversation/{user_or_fp}/{conversation_id}/{turn_id}/{message_id}.json
    Attachments:
      cb/tenants/{tenant}/projects/{project}/attachments/{user_or_fp}/{conversation_id}/{turn_id}/{timestamp-filename}
    Executions:
      cb/tenants/{tenant}/projects/{project}/executions/{user_or_fp}/{conversation_id}/{turn_id}/{ctx_id}/{ctx_id}.zip
    """

    def __init__(self, storage_uri: Optional[str] = None):
        self._settings = get_settings()
        self.storage_uri = storage_uri or self._settings.STORAGE_PATH
        self.backend = create_storage_backend(self.storage_uri)
        parsed = urlparse(self.storage_uri)
        self.scheme = parsed.scheme or "file"
        self.root_prefix = "cb"
        self._file_base = parsed.path if self.scheme == "file" else ""
        self._s3_bucket = parsed.netloc if self.scheme == "s3" else ""
        self._s3_prefix = parsed.path.lstrip("/") if self.scheme == "s3" else ""

    # ---------- helpers ----------

    def _join(self, *parts: str) -> str:
        return "/".join([p.strip("/").replace("//", "/") for p in parts if p])

    def _uri_for_path(self, relpath: str) -> str:
        if self.scheme == "file":
            base = self._file_base.rstrip("/")
            abs_path = self._join(base, relpath)
            return "file://" + abs_path
        if self.scheme == "s3":
            prefix = self._s3_prefix.rstrip("/")
            key = self._join(prefix, relpath)
            return f"s3://{self._s3_bucket}/{key}"
        return f"{self.scheme}://{relpath}"

    def _who_and_id(self, user: Optional[str], fingerprint: Optional[str]) -> Tuple[str, str]:
        who = "registered" if (user and user != "anonymous") else "anonymous"
        user_or_fp = user if who == "registered" else (fingerprint or "unknown")
        return who, user_or_fp

    def _sha256_bytes(self, data: bytes) -> str:
        import hashlib
        h = hashlib.sha256(); h.update(data); return h.hexdigest()

    # ---------- messages ----------

    async def put_message(
            self,
            *,
            tenant: str,
            project: str,
            user: Optional[str],
            fingerprint: Optional[str],
            conversation_id: str,
            turn_id: str,
            role: str,
            text: str,
            id: str|None = None,
            bundle_id: str|None = None,
            payload: Any | None = None,
            meta: Dict | None = None,
            embedding: List[float] | None = None,
            user_type: str = "anonymous",
            ttl_days: int = 365,
            msg_ts: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Persist a message JSON. Returns (uri, message_id, rn).
        RN is generated HERE and written into the record.
        """
        msg_ts = msg_ts or time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
        message_id = f"{_mid(role, msg_ts)}{'-' + id if id else ''}"
        _, user_or_fp = self._who_and_id(user, fingerprint)

        rel = self._join(
            self.root_prefix, "tenants", tenant, "projects", project,
            "conversation", user_or_fp, conversation_id, turn_id,
            f"{message_id}.json"
        )

        rn = rn_message(tenant, project, user_or_fp, conversation_id, turn_id, role, message_id)

        record = {
            "tenant": tenant,
            "project": project,
            "user": user,
            "user_id": user_or_fp,   # stable owner id used in RN
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "bundle_id": bundle_id,
            "role": role,
            "text": text,
            "timestamp": msg_ts + "Z",
            "embedding": embedding,
            "payload": payload,
            "meta": {
                "message_id": message_id,
                "turn_id": turn_id,
                "user_type": user_type,
                "ttl_days": int(ttl_days),
                "rn": rn,
                **(meta or {})
            }
        }
        await self.backend.write_bytes_a(rel, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"), meta=_JSON_META)
        return self._uri_for_path(rel), message_id, rn

    def list_conversation(
        self,
        *,
        tenant: str,
        project: str,
        user_type: str,
        user_or_fp: str,
        conversation_id: str,
        turn_id: Optional[str] = None,
    ) -> List[dict]:
        """
        Traverse directories using backend.list_dir(); loads *.json.
        If turn_id is None, loads messages across all turns.
        """
        base_conv = self._join(
            self.root_prefix, "tenants", tenant, "projects", project,
            "conversation", user_or_fp, conversation_id
        )
        legacy_bases = [
            self._join(
                self.root_prefix, "tenants", tenant, "projects", project,
                "conversation", who, user_or_fp, conversation_id
            )
            for who in ("registered", "anonymous", "privileged", "paid")
        ]

        def _as_child(base: str, name: str) -> str:
            return name if name.startswith(base) else self._join(base, name)

        def _collect_turn(turn_path: str) -> List[dict]:
            out: List[dict] = []
            for item in self.backend.list_dir(turn_path):
                child = _as_child(turn_path, item)
                if child.endswith(".json"):
                    try:
                        raw = self.backend.read_text(child)
                        obj = json.loads(raw)
                        obj.setdefault("meta", {})["hosted_uri"] = self._uri_for_path(child)
                        if "turn_id" not in obj:
                            # .../conversation/<user>/<conv>/<turn>/<message>.json
                            # legacy: .../conversation/<who>/<user>/<conv>/<turn>/<message>.json
                            parts = child.strip("/").split("/")
                            try:
                                i = parts.index("conversation")
                                if parts[i+1] in ("registered", "anonymous", "privileged", "paid"):
                                    obj["turn_id"] = parts[i+4]
                                else:
                                    obj["turn_id"] = parts[i+3]
                                obj.setdefault("meta", {})["turn_id"] = obj["turn_id"]
                            except Exception:
                                pass
                        out.append(obj)
                    except Exception:
                        continue
            out.sort(key=lambda x: x.get("timestamp", ""))
            return out

        if turn_id:
            out_turn: List[dict] = []
            for base in [base_conv] + legacy_bases:
                out_turn.extend(_collect_turn(self._join(base, turn_id)))
            out_turn.sort(key=lambda x: x.get("timestamp", ""))
            return out_turn

        out_all: List[dict] = []
        for base in [base_conv] + legacy_bases:
            try:
                items = self.backend.list_dir(base)
            except Exception:
                continue
            for item in items:
                child = _as_child(base, item)
                if child.endswith(".json"):
                    try:
                        raw = self.backend.read_text(child)
                        obj = json.loads(raw)
                        obj.setdefault("meta", {})["hosted_uri"] = self._uri_for_path(child)
                        out_all.append(obj)
                    except Exception:
                        pass
                else:
                    out_all.extend(_collect_turn(child))
        out_all.sort(key=lambda x: x.get("timestamp", ""))
        return out_all

    async def _delete_tree(self, rel_base: str) -> int:
        """
        Best-effort recursive delete of all files under a conversation-relative base.

        Delegates to storage backend's delete_tree_a / delete_tree, counting deleted blobs.
        """
        if not rel_base:
            return 0

        backend = self.backend
        try:
            # Prefer native async implementation if present
            if hasattr(backend, "delete_tree_a"):
                return int(await backend.delete_tree_a(rel_base))

        except Exception:
            # Best-effort; don't break delete_conversation if storage cleanup fails
            return 0

        return 0


    async def delete_conversation(
            self,
            *,
            tenant: str,
            project: str,
            user_type: str,
            user_or_fp: str,
            conversation_id: str,
    ) -> Dict[str, int]:
        """
        Delete all blobs for a given conversation: messages, attachments, executions.

        Layout:
          cb/tenants/{tenant}/projects/{project}/conversation/{user_or_fp}/{conversation_id}
          cb/tenants/{tenant}/projects/{project}/attachments/{user_or_fp}/{conversation_id}
          cb/tenants/{tenant}/projects/{project}/executions/{user_or_fp}/{conversation_id}
        """
        conv_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "conversation", user_or_fp, conversation_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "conversation", who, user_or_fp, conversation_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]
        att_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "attachments", user_or_fp, conversation_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "attachments", who, user_or_fp, conversation_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]
        exec_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "executions", user_or_fp, conversation_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "executions", who, user_or_fp, conversation_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]

        messages_deleted = sum([await self._delete_tree(b) for b in conv_bases])
        attachments_deleted = sum([await self._delete_tree(b) for b in att_bases])
        executions_deleted = sum([await self._delete_tree(b) for b in exec_bases])

        return {
            "messages": messages_deleted,
            "attachments": attachments_deleted,
            "executions": executions_deleted,
        }

    async def delete_turn(
            self,
            *,
            tenant: str,
            project: str,
            user_type: str,
            user_or_fp: str,
            conversation_id: str,
            turn_id: str,
    ) -> Dict[str, int]:
        """
        Delete all blobs for a given *turn* within a conversation:
          - messages for that turn
          - attachments for that turn
          - executions for that turn

        Layout:
          cb/tenants/{tenant}/projects/{project}/conversation/{user_or_fp}/{conversation_id}/{turn_id}
          cb/tenants/{tenant}/projects/{project}/attachments/{user_or_fp}/{conversation_id}/{turn_id}
          cb/tenants/{tenant}/projects/{project}/executions/{user_or_fp}/{conversation_id}/{turn_id}
        """
        conv_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "conversation", user_or_fp, conversation_id, turn_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "conversation", who, user_or_fp, conversation_id, turn_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]
        att_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "attachments", user_or_fp, conversation_id, turn_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "attachments", who, user_or_fp, conversation_id, turn_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]
        exec_bases = [
            self._join(self.root_prefix, "tenants", tenant, "projects", project,
                       "executions", user_or_fp, conversation_id, turn_id),
            *[
                self._join(self.root_prefix, "tenants", tenant, "projects", project,
                           "executions", who, user_or_fp, conversation_id, turn_id)
                for who in ("registered", "anonymous", "privileged", "paid")
            ],
        ]

        messages_deleted = sum([await self._delete_tree(b) for b in conv_bases])
        attachments_deleted = sum([await self._delete_tree(b) for b in att_bases])
        executions_deleted = sum([await self._delete_tree(b) for b in exec_bases])

        return {
            "messages": messages_deleted,
            "attachments": attachments_deleted,
            "executions": executions_deleted,
        }

    # ---------- attachments (role-aware, turn in path) ----------

    async def put_attachment(
        self,
        *,
        tenant: str,
        project: str,
        user: Optional[str],
        fingerprint: Optional[str],
        conversation_id: str,
        turn_id: str,
        role: str = "artifact",
        filename: str,
        data: bytes,
        mime: Optional[str] = None,
        user_type: Optional[str] = None,
        ttl_days: int = 365,
        request_id: Optional[str] = None,
        origin: str = "chat",
    ) -> Tuple[str, str, str]:
        """
        Save a binary/text file under /attachments/.../{conversation_id}/{turn_id}/.
        Returns (uri, key, rn). RN includes user_id and role.
        """
        if not turn_id:
            raise ValueError("turn_id is required for attachments")

        # ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        _, user_or_fp = self._who_and_id(user, fingerprint)

        base = self._join(
            self.root_prefix, "tenants", tenant, "projects", project,
            "attachments", user_or_fp, conversation_id, turn_id
        )
        # safe_name = os.path.basename(filename) or "file.bin"
        # rel_name = f"{ts}-{safe_name}"
        if origin == "user":
            rn, rel_name = await attachment_rn_and_rel_name(
                tenant, project, user_or_fp, conversation_id, turn_id, role, filename
            )
        else:
            rn, rel_name = await file_rn_and_rel_name(
                tenant, project, user_or_fp, conversation_id, turn_id, role, filename
            )
        rel = self._join(base, rel_name)

        meta = {"ContentType": mime} if mime else None
        await self.backend.write_bytes_a(rel, data, meta=meta)

        # RN is the logical filename (without timestamp) OR the actual stored name?
        # To keep dereferencing simple, we use the stored name.
        # rn = rn_attachment(tenant, project, user_or_fp, conversation_id, turn_id, role, rel_name)
        return self._uri_for_path(rel), rel, rn

    async def get_blob_bytes(self, uri_or_path: str) -> bytes:
        rel = self._rel_from_uri_or_path(uri_or_path)
        return await self.backend.read_bytes_a(rel)

    # ---------- execution snapshot (role-aware RNs in manifest) ----------

    async def put_execution_snapshot(
        self,
        *,
        tenant: str,
        project: str,
        user: Optional[str],
        user_type: str,
        fingerprint: Optional[str],
        conversation_id: str,
        turn_id: str,
        codegen_run_id: str,
        role: str = "artifact",
        out_dir: Optional[str] = None,
        pkg_dir: Optional[str] = None,
    ) -> dict:
        """
        Persist one compact execution archive under /executions/.../{turn_id}/{ctx_id}/{ctx_id}.zip.

        The archive contains top-level out/ and pkg/ trees. Keeping one blob avoids
        slow object-store uploads and expensive browsable-tree writes for every file.
        """
        _, user_or_fp = self._who_and_id(user, fingerprint)
        base = self._join(
            self.root_prefix, "tenants", tenant, "projects", project,
            "executions", user_or_fp, conversation_id, turn_id, codegen_run_id
        )

        EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".venv", "debug"}
        counts = {"out": 0, "pkg": 0}

        def _add_tree(zf: zipfile.ZipFile, src: Optional[str], archive_root: str) -> None:
            if not src:
                return
            srcp = pathlib.Path(src)
            if not srcp.exists():
                return
            for p in srcp.rglob("*"):
                if not p.is_file():
                    continue
                rel_parts = p.relative_to(srcp).parts
                if any(part in EXCLUDE_DIRS for part in rel_parts):
                    continue
                rel_under = str(pathlib.PurePosixPath(archive_root, *rel_parts))
                zf.write(p, arcname=rel_under)
                counts[archive_root] = counts.get(archive_root, 0) + 1

        archive_name = f"{codegen_run_id}.zip"
        archive_rel = self._join(base, archive_name)
        with tempfile.NamedTemporaryFile(prefix=f"exec_{codegen_run_id}_", suffix=".zip", delete=False) as tmp:
            zip_path = pathlib.Path(tmp.name)
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                _add_tree(zf, out_dir, "out")
                _add_tree(zf, pkg_dir, "pkg")
            data = zip_path.read_bytes()
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass

        ctype = "application/zip"
        await self.backend.write_bytes_a(archive_rel, data, meta={"ContentType": ctype})
        archive_url = self._uri_for_path(archive_rel)
        archive_file = {
            "key": archive_rel,
            "url": archive_url,
            "size": len(data),
            "sha256": self._sha256_bytes(data),
            "mime": ctype,
            "kind": "execution_archive",
            "rn": rn_execution_file(
                tenant, project, user_or_fp, conversation_id, turn_id, role, "execution_archive", archive_name
            ),
        }
        out_info = {"dir": archive_url, "files": [], "file_count": counts["out"], "archive_prefix": "out/"}
        pkg_info = {"dir": archive_url, "files": [], "file_count": counts["pkg"], "archive_prefix": "pkg/"}
        return {
            "archive": archive_file,
            "out": out_info,
            "pkg": pkg_info,
            "roots": {"archive": archive_url, "out": archive_url, "pkg": archive_url},
            "files": [archive_file],
        }

    async def close(self):
        return None

    def _rel_from_uri_or_path(self, uri_or_path: str) -> str:
        """
        Convert a full URI or filesystem path into the backend-relative key used by storage.
        Accepts:
          - file://... absolute URIs
          - s3://bucket/prefix/... URIs
          - absolute filesystem paths (when using file backend)
          - backend-relative keys starting with 'cb/...'
        Returns a normalized relative key like:
          'cb/tenants/{tenant}/projects/{project}/conversation/.../{message_id}.json'
        """
        if not uri_or_path:
            raise ValueError("uri_or_path is required")

        text = uri_or_path.strip()
        parsed = urlparse(text)

        # --- URI forms ---
        if parsed.scheme in ("file", "s3"):
            if parsed.scheme == "file":
                abs_path = os.path.normpath(unquote(parsed.path))
                base = os.path.normpath(self._file_base or "/")
                # primary: strip configured base
                base_with_sep = base.rstrip(os.sep) + os.sep
                if abs_path.startswith(base_with_sep):
                    rel = abs_path[len(base_with_sep):].replace("\\", "/")
                    return rel.lstrip("/")
                # fallback: try to cut from '/cb/...'
                as_posix = abs_path.replace("\\", "/")
                idx = as_posix.find("/" + self.root_prefix + "/")
                if idx >= 0:
                    return as_posix[idx + 1 :].lstrip("/")
                raise ValueError(f"Path {abs_path} is not under storage base {base}")

            if parsed.scheme == "s3":
                bucket = parsed.netloc
                key = unquote(parsed.path.lstrip("/"))
                prefix = self._s3_prefix.rstrip("/")
                # prefer configured prefix removal
                if prefix and key.startswith(prefix + "/"):
                    return key[len(prefix) + 1 :].lstrip("/")
                if not prefix:
                    return key.lstrip("/")
                # fallback: detect 'cb/...'
                cb_idx = key.find(self.root_prefix + "/")
                if cb_idx >= 0:
                    return key[cb_idx:].lstrip("/")
                raise ValueError(f"S3 key {key} does not start with expected prefix '{prefix}/'")

        # --- Non-URI forms ---
        # Absolute filesystem path (file backend only)
        if text.startswith("/"):
            if self.scheme != "file":
                raise ValueError("Absolute paths are only supported for file:// storage")
            abs_path = os.path.normpath(unquote(text))
            base = os.path.normpath(self._file_base or "/")
            base_with_sep = base.rstrip(os.sep) + os.sep
            if abs_path.startswith(base_with_sep):
                rel = abs_path[len(base_with_sep):].replace("\\", "/")
                return rel.lstrip("/")
            as_posix = abs_path.replace("\\", "/")
            idx = as_posix.find("/" + self.root_prefix + "/")
            if idx >= 0:
                return as_posix[idx + 1 :].lstrip("/")
            raise ValueError(f"Absolute path {text} is not under storage base {base}")

        # Already looks like a backend-relative key (e.g., 'cb/tenants/...')
        return text.lstrip("/")

    async def get_message(self, uri_or_path: str) -> dict:
        """
        Load a single message JSON by its URI or path and return the record (dict).
        - Supports 'file://', 's3://', absolute file paths, or backend-relative keys.
        - Ensures meta.hosted_uri is set to a dereferenceable URI for this storage,
          and fills in 'turn_id' if missing by parsing the path.
        """
        rel = self._rel_from_uri_or_path(uri_or_path)
        if not rel.endswith(".json"):
            raise ValueError(f"Message path must point to a .json file: got '{rel}'")

        try:
            raw = await self.backend.read_text_a(rel)
        except Exception as e:
            raise FileNotFoundError(f"Cannot read message at {uri_or_path}: {e}")

        return json.loads(raw)
