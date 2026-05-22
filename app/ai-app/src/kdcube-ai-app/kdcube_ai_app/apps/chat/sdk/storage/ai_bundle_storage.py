# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/storage/ai_bundle_storage.py

import mimetypes
from typing import Optional, List, Dict, Any, Union
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.sdk.config import get_settings

try:
    from kdcube_ai_app.storage.storage import create_storage_backend, IStorageBackend
except ImportError:
    raise ImportError("Please ensure 'kdcube_ai_app.storage.storage' is importable.")


class BundleArtifactStorage:
    """
    Lightweight storage wrapper for bundle artifacts.

    Root (per bundle):
      cb/tenants/{tenant}/projects/{project}/ai-bundle-storage/{bundle_id}/

    Interface:
      - write(key, data, *, mime=None, encoding='utf-8', meta=None) -> str (absolute URI)
      - read(key, *, as_text=False, encoding='utf-8') -> bytes | str
      - list(prefix: str = '') -> List[str]
      - exists(key: str) -> bool
      - delete(key: str) -> int   # file or subtree if key ends with '/'
    """

    root_prefix = "cb"

    def __init__(
            self,
            *,
            tenant: str,
            project: str,
            bundle_id: str | None = None,
            ai_bundle_id: str | None = None,
            storage_uri: Optional[str] = None
    ) -> None:
        resolved_bundle_id = bundle_id or ai_bundle_id
        if not resolved_bundle_id:
            raise ValueError("bundle_id is required")
        settings = get_settings()
        self.storage_uri = storage_uri or settings.STORAGE_PATH
        self.tenant = tenant
        self.project = project
        self.bundle_id = resolved_bundle_id
        # Backward-compatible attribute name for existing callers.
        self.ai_bundle_id = resolved_bundle_id

        self.backend: IStorageBackend = create_storage_backend(self.storage_uri)

        parsed = urlparse(self.storage_uri)
        self.scheme = parsed.scheme or "file"
        self._file_base = (parsed.path or "") if self.scheme == "file" else ""
        self._s3_bucket = parsed.netloc if self.scheme == "s3" else ""
        self._s3_prefix = parsed.path.lstrip("/") if self.scheme == "s3" else ""

        # Precompute root path for this bundle (relative key used by backend).
        # The physical prefix is kept for backward compatibility with existing
        # deployments and stored artifacts.
        self._bundle_root = self._join(
            self.root_prefix, "tenants", self.tenant, "projects", self.project,
            "ai-bundle-storage", self.bundle_id
        )

    # ------------------ public API ------------------

    def write(
            self,
            key: str,
            data: Union[bytes, str],
            *,
            mime: Optional[str] = None,
            encoding: str = "utf-8",
            meta: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Write bytes or text at the bundle-relative key. Returns an absolute URI to the stored object.
        """
        rel = self._join(self._bundle_root, self._normalize_key(key))
        payload: bytes = data.encode(encoding) if isinstance(data, str) else data

        meta = dict(meta) if meta else {}
        if mime:
            meta.setdefault("ContentType", mime)
        else:
            guessed, enc = mimetypes.guess_type(key)
            if guessed:
                meta.setdefault("ContentType", guessed)
            if enc:
                meta.setdefault("ContentEncoding", enc)

        self.backend.write_bytes(rel, payload, meta=meta or None)
        return self._uri_for_path(rel)

    def read(
            self,
            key: str,
            *,
            as_text: bool = False,
            encoding: str = "utf-8"
    ) -> Union[bytes, str]:
        """
        Read the object at the bundle-relative key. Returns bytes by default, or text if as_text=True.
        """
        rel = self._join(self._bundle_root, self._normalize_key(key))
        if as_text:
            return self.backend.read_text(rel, encoding=encoding)
        return self.backend.read_bytes(rel)

    def list(self, prefix: str = "") -> List[str]:
        """
        List immediate children (files and subdirectories) under the given bundle-relative prefix.
        Returns names relative to that prefix (not full paths).
        """
        rel_dir = self._join(self._bundle_root, self._normalize_prefix(prefix))
        return self.backend.list_dir(rel_dir)

    def exists(self, key: str) -> bool:
        """
        Return True if a file exists at 'key' or if a directory/prefix exists when 'key' ends with '/'.
        """
        if key.endswith("/"):
            rel = self._join(self._bundle_root, self._normalize_prefix(key))
            return self.backend.exists(rel)
        rel_file = self._join(self._bundle_root, self._normalize_key(key))
        # Try both file and "as-prefix" (to cover directory-like matches)
        return self.backend.exists(rel_file) or self.backend.exists(rel_file + "/")

    def delete(self, key: str) -> int:
        """
        Delete a single file (e.g. 'foo/bar.txt') or an entire subtree if 'key' ends with '/'.
        Returns a best-effort count of deleted items (files/objects).
        - For backends that support recursive prefix deletion (S3, local), this will be efficient.
        - For backends that don't, we fall back to recursive listing + deletion.
        """
        # Detect intent: file vs prefix
        is_prefix = key.endswith("/")
        if is_prefix:
            rel_prefix = self._join(self._bundle_root, self._normalize_prefix(key))
            return self._delete_tree(rel_prefix)

        # File path
        rel_file = self._join(self._bundle_root, self._normalize_key(key))

        # If it behaves like a directory (has children), treat it as a prefix
        children = self.backend.list_dir(rel_file)
        if children:
            return self._delete_tree(rel_file if rel_file.endswith("/") else rel_file + "/")

        # Otherwise, delete the single object
        self.backend.delete(rel_file)
        return 1

    # ------------------ helpers ------------------

    def _delete_tree(self, rel_prefix: str) -> int:
        """
        Robust recursive deletion that works across all backends.
        Attempts a single-shot backend delete first; if the prefix still exists,
        it falls back to manual traversal + deletes.
        """
        deleted = 0

        # First try native backend recursive delete (works for S3/local)
        try:
            self.backend.delete(rel_prefix)
        except Exception:
            # Ignore and fall back
            pass

        # If anything remains (e.g., in-memory backend), purge manually
        while True:
            names = self.backend.list_dir(rel_prefix)
            if not names:
                break
            for name in names:
                child = self._join(rel_prefix, name)
                # Try deleting child as subtree first (if it's a directory-like)
                sub_names = self.backend.list_dir(child)
                if sub_names:
                    deleted += self._delete_tree(child if child.endswith("/") else child + "/")
                    continue
                # Otherwise treat as file/object
                try:
                    self.backend.delete(child)
                    deleted += 1
                except Exception:
                    # As a fallback, try deleting as a subtree
                    try:
                        self.backend.delete(child if child.endswith("/") else child + "/")
                    except Exception:
                        pass

        # Finally, attempt to remove the (now-empty) root directory (no-op for S3/in-memory)
        try:
            self.backend.delete(rel_prefix)
        except Exception:
            pass

        return deleted

    def _join(self, *parts: str) -> str:
        return "/".join([p.strip("/").replace("//", "/") for p in parts if p])

    def _normalize_key(self, key: str) -> str:
        if not key:
            raise ValueError("key must be a non-empty string")
        k = key.replace("\\", "/").lstrip("/")
        if ".." in k.split("/"):
            raise ValueError("path traversal ('..') is not allowed in keys")
        return k

    def _normalize_prefix(self, prefix: str) -> str:
        p = self._normalize_key(prefix) if prefix else ""
        return p if not p or p.endswith("/") else (p + "/")

    def _uri_for_path(self, relpath: str) -> str:
        if self.scheme == "file":
            base = (self._file_base or "").rstrip("/")
            abs_path = self._join(base, relpath)
            return "file://" + abs_path
        if self.scheme == "s3":
            prefix = self._s3_prefix.rstrip("/")
            key = self._join(prefix, relpath)
            return f"s3://{self._s3_bucket}/{key}"
        return f"{self.scheme}://{relpath}"

    @property
    def root_uri(self) -> str:
        return self._uri_for_path(self._bundle_root + "/")


class AIBundleStorage(BundleArtifactStorage):
    """
    Backward-compatible alias for BundleArtifactStorage.

    New bundle code should import BundleArtifactStorage. This class is kept so
    existing bundles and tests continue to run during the naming migration.
    """
