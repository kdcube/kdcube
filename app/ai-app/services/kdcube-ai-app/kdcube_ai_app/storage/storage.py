# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# storage/storage.py
"""
Knowledge Base storage backends for different storage systems.
"""
import asyncio
import logging
import mimetypes
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

logger = logging.getLogger("KnowledgeBase.Storage")

mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("application/wasm", ".wasm")

class IStorageBackend(ABC):
    """Interface for storage backends."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists in storage."""
        pass

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Read raw bytes from storage."""
        pass

    @abstractmethod
    def write_bytes(self, path: str, data: bytes,  meta: Optional[dict] = None) -> None:
        """Write raw bytes to storage."""
        pass

    @abstractmethod
    def read_text(self, path: str, encoding: str = 'utf-8') -> str:
        """Read text from storage."""
        pass

    @abstractmethod
    def write_text(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        """Write text to storage."""
        pass

    @abstractmethod
    def list_dir(self, path: str) -> List[str]:
        """List items in a directory."""
        pass

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file or directory."""
        pass

    @abstractmethod
    def get_size(self, path: str) -> int:
        """Get file size in bytes."""
        pass

    @abstractmethod
    def get_modified_time(self, path: str) -> datetime:
        """Get last modified time."""
        pass

    # ---------- Async convenience wrappers (default: run sync in a thread) ----------
    async def exists_a(self, path: str) -> bool:
        return await asyncio.to_thread(self.exists, path)

    async def read_bytes_a(self, path: str) -> bytes:
        return await asyncio.to_thread(self.read_bytes, path)

    async def write_bytes_a(self, path: str, data: bytes, meta: Optional[dict] = None) -> None:
        return await asyncio.to_thread(self.write_bytes, path, data, meta)

    async def read_text_a(self, path: str, encoding: str = "utf-8") -> str:
        return await asyncio.to_thread(self.read_text, path, encoding)

    async def write_text_a(self, path: str, content: str, encoding: str = "utf-8") -> None:
        return await asyncio.to_thread(self.write_text, path, content, encoding)

    async def list_dir_a(self, path: str) -> List[str]:
        return await asyncio.to_thread(self.list_dir, path)

    async def delete_a(self, path: str) -> None:
        return await asyncio.to_thread(self.delete, path)

    async def get_size_a(self, path: str) -> int:
        return await asyncio.to_thread(self.get_size, path)

    async def get_modified_time_a(self, path: str) -> datetime:
        return await asyncio.to_thread(self.get_modified_time, path)

    def list_with_prefix(self, path: str, prefix: str) -> List[str]:
        """
        List files in a directory that start with the given prefix.

        Args:
            path: Directory path to search in
            prefix: Filename prefix to filter by (e.g., "cb|user-123|")

        Returns:
            List of filenames (not full paths) that match the prefix
        """
        pass

    async def list_with_prefix_a(self, path: str, prefix: str) -> List[str]:
        """Async version of list_with_prefix"""
        return await asyncio.to_thread(self.list_with_prefix, path, prefix)

class LocalFileSystemBackend(IStorageBackend):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path).resolve()
        # Ensure base directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path relative to the base path."""
        resolved = (self.base_path / path).resolve()
        # Security check - ensure path is within base directory
        if not str(resolved).startswith(str(self.base_path)):
            raise ValueError(f"Path {path} is outside base directory")
        return resolved

    def exists(self, path: str) -> bool:
        return self._resolve_path(path).exists()

    def read_bytes(self, path: str) -> bytes:
        return self._resolve_path(path).read_bytes()

    def write_bytes(self, path: str, data: bytes,  meta: Optional[dict] = None) -> None:
        resolved = self._resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)

    def read_text(self, path: str, encoding: str = 'utf-8') -> str:
        return self._resolve_path(path).read_text(encoding=encoding)

    def write_text(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        resolved = self._resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding=encoding)

    def list_dir(self, path: str) -> List[str]:
        resolved = self._resolve_path(path)
        if not resolved.is_dir():
            return []
        return [item.name for item in resolved.iterdir()]

    def delete(self, path: str) -> None:
        resolved = self._resolve_path(path)
        if resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            import shutil
            shutil.rmtree(resolved)

    def get_size(self, path: str) -> int:
        return self._resolve_path(path).stat().st_size

    def get_modified_time(self, path: str) -> datetime:
        return datetime.fromtimestamp(self._resolve_path(path).stat().st_mtime)

    def list_with_prefix(self, path: str, prefix: str) -> List[str]:
        """
        List files in a directory that start with the given prefix.
        Much more efficient than list_dir() + filter for large directories.
        """
        resolved = self._resolve_path(path)
        if not resolved.is_dir():
            return []

        # Use glob for efficient prefix matching
        # Convert prefix to a glob pattern
        pattern = f"{prefix}*"

        matching_files = []
        for item in resolved.glob(pattern):
            if item.is_file():
                matching_files.append(item.name)

        return matching_files


class S3StorageBackend(IStorageBackend):
    """Amazon S3 storage backend."""

    def __init__(self, bucket_name: str, prefix: str = "",
                 aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None,
                 region_name: Optional[str] = None,
                 profile_name: Optional[str] = None,
                 skip_bucket_check: bool = False):
        # try:
        #    import boto3
        #    from botocore.exceptions import NoCredentialsError, ClientError
        # except ImportError:
        #    raise ImportError("boto3 is required for S3 storage backend. Install with: pip install boto3")

        self.bucket_name = bucket_name
        self.prefix = prefix.rstrip('/') + '/' if prefix else ''

        # Initialize S3 client
        session_kwargs = {}
        # Keep for async session reuse
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._profile_name = profile_name
        self._region_name = region_name

        if aws_access_key_id:
            session_kwargs['aws_access_key_id'] = aws_access_key_id
        if aws_secret_access_key:
            session_kwargs['aws_secret_access_key'] = aws_secret_access_key
        if region_name:
            session_kwargs['region_name'] = region_name
        if profile_name:
            session_kwargs['profile_name'] = profile_name

        self.s3_client = self._create_s3_client(session_kwargs)

        # Test connection (can be skipped for testing)
        if not skip_bucket_check:
            try:
                self.s3_client.head_bucket(Bucket=bucket_name)
            except Exception as e:
                raise ConnectionError(f"Cannot connect to S3 bucket {bucket_name}: {e}")

    def _create_s3_client(self, session_kwargs):
        """Create S3 client - separated for easier testing."""
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 is required for S3 storage backend. Install with: pip install boto3")

        session = boto3.Session(**session_kwargs)
        return session.client('s3')

    # ---------- Async (native) client helpers ----------
    def _build_async_session_kwargs(self) -> dict:
        # mirror sync creds/region config
        return {
            k: v for k, v in dict(
                aws_access_key_id=getattr(self, "_aws_access_key_id", None),
                aws_secret_access_key=getattr(self, "_aws_secret_access_key", None),
                region_name=getattr(self, "_region_name", None),
                profile_name=getattr(self, "_profile_name", None),
            ).items() if v
        }

    async def _get_async_client(self):
        try:
            import aioboto3
        except ImportError as e:
            raise ImportError("aioboto3 is required for async S3 methods (pip install aioboto3)") from e

        # We build a short-lived client per call via async context managers below.
        # If you want connection reuse, you can lift this into a pool and reuse it.
        session = aioboto3.Session(**self._build_async_session_kwargs())
        return session.client("s3")

    def _get_s3_key(self, path: str) -> str:
        """Convert local path to S3 key."""
        # Normalize path separators
        path = path.replace('\\', '/')
        return self.prefix + path.lstrip('/')

    def _prefix_exists(self, path: str) -> bool:
        resp = self.s3_client.list_objects(Bucket=self.bucket_name, Prefix=path, Delimiter='/', MaxKeys=1)
        return 'Contents' in resp or ('CommonPrefixes' in resp and len(resp['CommonPrefixes']) > 0)

    def exists(self, path: str) -> bool:
        s3_key = self._get_s3_key(path)
        if s3_key.endswith('/'):
            return self._prefix_exists(s3_key)
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=self._get_s3_key(path))
            return True
        except:
            # try using path as prefix
            return self._prefix_exists(s3_key + '/')

    def read_bytes(self, path: str) -> bytes:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=self._get_s3_key(path))
            return response['Body'].read()
        except Exception as e:
            raise FileNotFoundError(f"Cannot read {path} from S3: {e}")

    def write_bytes(self, path: str, data: bytes, meta: Optional[dict] = None) -> None:

        try:
            key = self._get_s3_key(path)
            put_kwargs: Dict[str, Any] = {
                "Bucket": self.bucket_name,
                "Key": key,
                "Body": data,
            }
            content_type = None
            if meta:
                content_type = (
                        meta.get("ContentType")
                        or meta.get("content_type")
                        or meta.get("mime")
                        or meta.get("mime_type")
                )
            if not content_type:
                guessed_type, guessed_encoding = mimetypes.guess_type(path)
                if guessed_type:
                    content_type = guessed_type
                if guessed_encoding:
                    put_kwargs["ContentEncoding"] = guessed_encoding  # e.g., "gzip"

            if content_type:
                put_kwargs["ContentType"] = content_type

            header_names = [
                "ContentEncoding",
                "CacheControl",
                "ContentDisposition",
                "ContentLanguage",
                "Expires",  # can be datetime or RFC 1123 string
                "ACL",          # optional if you want to allow this
                "StorageClass", # e.g., "STANDARD_IA"
            ]
            if meta:
                for name in header_names:
                    if name in meta:
                        put_kwargs[name] = meta[name]
                    elif name.lower() in meta:
                        put_kwargs[name] = meta[name.lower()]

            # Merge/derive custom metadata
            metadata: Dict[str, str] = {}
            if isinstance(meta.get("Metadata"), dict):
                metadata.update({str(k): str(v) for k, v in meta["Metadata"].items()})

            reserved = set(
                ["ContentType", "content_type", "mime", "mime_type"]
                + header_names
                + ["Metadata"]
            )
            leftovers = {k: v for k, v in meta.items() if k not in reserved}
            if leftovers:
                # S3 requires str values for Metadata
                metadata.update({str(k): str(v) for k, v in leftovers.items()})

            if metadata:
                put_kwargs["Metadata"] = metadata

            self.s3_client.put_object(**put_kwargs)
        except Exception as e:
            raise IOError(f"Cannot write {path} to S3: {e}")

    def read_text(self, path: str, encoding: str = 'utf-8') -> str:
        return self.read_bytes(path).decode(encoding)

    def write_text(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        self.write_bytes(path, content.encode(encoding))

    def list_dir(self, path: str) -> List[str]:
        try:
            prefix = self._get_s3_key(path).rstrip('/') + '/'
            items = []
            continuation_token = None

            # Handle pagination
            while True:
                list_kwargs = {
                    'Bucket': self.bucket_name,
                    'Prefix': prefix,
                    'Delimiter': '/'
                }

                if continuation_token:
                    list_kwargs['ContinuationToken'] = continuation_token

                response = self.s3_client.list_objects_v2(**list_kwargs)

                # Add subdirectories
                for prefix_info in response.get('CommonPrefixes', []):
                    dir_name = prefix_info['Prefix'][len(prefix):].rstrip('/')
                    if dir_name:
                        items.append(dir_name)

                # Add files
                for obj in response.get('Contents', []):
                    file_name = obj['Key'][len(prefix):]
                    if file_name and '/' not in file_name:
                        items.append(file_name)

                # Check if there are more pages
                if response.get('IsTruncated'):
                    continuation_token = response.get('NextContinuationToken')
                else:
                    break

            return items
        except Exception as e:
            logger.error(f"Cannot list directory {path} in S3: {e}")
            return []

    def delete(self, path: str) -> None:
        try:
            s3_key = self._get_s3_key(path)

            # Check if it's a "directory" (prefix with objects)
            objects_to_delete = []
            continuation_token = None

            # Handle pagination for listing objects to delete
            while True:
                list_kwargs = {
                    'Bucket': self.bucket_name,
                    'Prefix': s3_key.rstrip('/') + '/'
                }

                if continuation_token:
                    list_kwargs['ContinuationToken'] = continuation_token

                response = self.s3_client.list_objects_v2(**list_kwargs)

                # Collect objects to delete
                for obj in response.get('Contents', []):
                    objects_to_delete.append({'Key': obj['Key']})

                # Check if there are more pages
                if response.get('IsTruncated'):
                    continuation_token = response.get('NextContinuationToken')
                else:
                    break

            if objects_to_delete:
                # Delete objects in batches (S3 allows max 1000 per batch)
                batch_size = 1000
                for i in range(0, len(objects_to_delete), batch_size):
                    batch = objects_to_delete[i:i + batch_size]
                    self.s3_client.delete_objects(
                        Bucket=self.bucket_name,
                        Delete={'Objects': batch}
                    )
            else:
                # Try to delete single object
                try:
                    self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
                except self.s3_client.exceptions.NoSuchKey:
                    # Object doesn't exist, that's fine
                    pass
        except Exception as e:
            logger.error(f"Cannot delete {path} from S3: {e}")
            raise

    def get_size(self, path: str) -> int:
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=self._get_s3_key(path))
            return response['ContentLength']
        except Exception as e:
            raise FileNotFoundError(f"Cannot get size of {path} from S3: {e}")

    def get_modified_time(self, path: str) -> datetime:
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=self._get_s3_key(path))
            return response['LastModified'].replace(tzinfo=None)  # Remove timezone for consistency
        except Exception as e:
            raise FileNotFoundError(f"Cannot get modified time of {path} from S3: {e}")

    # ---------- Native async overrides (no threads) ----------
    async def exists_a(self, path: str) -> bool:
        key = self._get_s3_key(path)
        async with (await self._get_async_client()) as s3:
            if key.endswith("/"):
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=key, Delimiter="/", PaginationConfig={"MaxItems": 1}):
                    if page.get("KeyCount", 0) > 0:
                        return True
                return False
            # try head_object, fallback to prefix
            try:
                await s3.head_object(Bucket=self.bucket_name, Key=key)
                return True
            except Exception:
                prefix = key if key.endswith("/") else key + "/"
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix, Delimiter="/", PaginationConfig={"MaxItems": 1}):
                    if page.get("KeyCount", 0) > 0:
                        return True
                return False

    async def read_bytes_a(self, path: str) -> bytes:
        key = self._get_s3_key(path)
        async with (await self._get_async_client()) as s3:
            try:
                resp = await s3.get_object(Bucket=self.bucket_name, Key=key)
                body = resp["Body"]
                return await body.read()
            except Exception as e:
                raise FileNotFoundError(f"Cannot read {path} from S3: {e}")

    async def write_bytes_a(self, path: str, data: bytes, meta: Optional[dict] = None) -> None:
        key = self._get_s3_key(path)
        put_kwargs: Dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": key,
            "Body": data,
        }
        # Copy of your sync metadata logic
        content_type = None
        if meta:
            content_type = (
                    meta.get("ContentType")
                    or meta.get("content_type")
                    or meta.get("mime")
                    or meta.get("mime_type")
            )
        if not content_type:
            guessed_type, guessed_encoding = mimetypes.guess_type(path)
            if guessed_type:
                content_type = guessed_type
            if guessed_encoding:
                put_kwargs["ContentEncoding"] = guessed_encoding

        if content_type:
            put_kwargs["ContentType"] = content_type

        header_names = [
            "ContentEncoding",
            "CacheControl",
            "ContentDisposition",
            "ContentLanguage",
            "Expires",
            "ACL",
            "StorageClass",
        ]

        if meta:
            for name in header_names:
                if name in meta:
                    put_kwargs[name] = meta[name]
                elif name.lower() in meta:
                    put_kwargs[name] = meta[name.lower()]

            metadata: Dict[str, str] = {}
            if isinstance(meta.get("Metadata"), dict):
                metadata.update({str(k): str(v) for k, v in meta["Metadata"].items()})

            reserved = set(["ContentType", "content_type", "mime", "mime_type"] + header_names + ["Metadata"])
            leftovers = {k: v for k, v in meta.items() if k not in reserved}
            if leftovers:
                metadata.update({str(k): str(v) for k, v in leftovers.items()})

            if metadata:
                put_kwargs["Metadata"] = metadata

        async with (await self._get_async_client()) as s3:
            try:
                await s3.put_object(**put_kwargs)
            except Exception as e:
                raise IOError(f"Cannot write {path} to S3: {e}")

    async def read_text_a(self, path: str, encoding: str = "utf-8") -> str:
        data = await self.read_bytes_a(path)
        return data.decode(encoding)

    async def write_text_a(self, path: str, content: str, encoding: str = "utf-8") -> None:
        await self.write_bytes_a(path, content.encode(encoding))

    async def list_dir_a(self, path: str) -> List[str]:
        prefix = self._get_s3_key(path).rstrip("/") + "/"
        items: List[str] = []
        async with (await self._get_async_client()) as s3:
            try:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix, Delimiter="/"):
                    for p in page.get("CommonPrefixes", []):
                        dir_name = p["Prefix"][len(prefix):].rstrip("/")
                        if dir_name:
                            items.append(dir_name)
                    for obj in page.get("Contents", []):
                        file_name = obj["Key"][len(prefix):]
                        if file_name and "/" not in file_name:
                            items.append(file_name)
                return items
            except Exception as e:
                logger.error(f"Cannot list directory {path} in S3: {e}")
                return []

    async def delete_a(self, path: str) -> None:
        key = self._get_s3_key(path)
        async with (await self._get_async_client()) as s3:
            try:
                # Collect children (if prefix)
                prefix = key.rstrip("/") + "/"
                paginator = s3.get_paginator("list_objects_v2")
                to_delete: List[Dict[str, str]] = []
                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        to_delete.append({"Key": obj["Key"]})

                if to_delete:
                    # Delete in batches of 1000
                    for i in range(0, len(to_delete), 1000):
                        batch = {"Objects": to_delete[i:i + 1000]}
                        await s3.delete_objects(Bucket=self.bucket_name, Delete=batch)
                else:
                    # Try single key
                    try:
                        await s3.delete_object(Bucket=self.bucket_name, Key=key)
                    except Exception:
                        # Best-effort; ignore missing
                        pass
            except Exception as e:
                logger.error(f"Cannot delete {path} from S3: {e}")
                raise

    async def get_size_a(self, path: str) -> int:
        key = self._get_s3_key(path)
        async with (await self._get_async_client()) as s3:
            try:
                head = await s3.head_object(Bucket=self.bucket_name, Key=key)
                return int(head["ContentLength"])
            except Exception as e:
                raise FileNotFoundError(f"Cannot get size of {path} from S3: {e}")

    async def get_modified_time_a(self, path: str) -> datetime:
        key = self._get_s3_key(path)
        async with (await self._get_async_client()) as s3:
            try:
                head = await s3.head_object(Bucket=self.bucket_name, Key=key)
                # aiobotocore returns tz-aware datetime; match your sync behavior (naive)
                lm = head["LastModified"]
                return lm.replace(tzinfo=None) if getattr(lm, "tzinfo", None) else lm
            except Exception as e:
                raise FileNotFoundError(f"Cannot get modified time of {path} from S3: {e}")

    def list_with_prefix(self, path: str, prefix: str) -> List[str]:
        """
        List files in S3 directory that start with the given prefix.
        Uses S3's native prefix filtering for efficiency.
        """
        # Build the full S3 prefix
        dir_prefix = self._get_s3_key(path).rstrip("/") + "/"
        full_prefix = dir_prefix + prefix

        items: List[str] = []

        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=full_prefix):
                for obj in page.get('Contents', []):
                    # Extract just the filename (not the full key)
                    full_key = obj['Key']
                    if full_key.startswith(dir_prefix):
                        filename = full_key[len(dir_prefix):]
                        # Only include files directly in this directory (no subdirs)
                        if '/' not in filename:
                            items.append(filename)
            return items
        except Exception as e:
            import logging
            logger = logging.getLogger("S3Storage")
            logger.error(f"Cannot list files with prefix {prefix} in {path}: {e}")
            return []


    # =============================================================================
    # S3StorageBackend async implementation
    # =============================================================================

    async def list_with_prefix_a(self, path: str, prefix: str) -> List[str]:
        """
        Async version - list files in S3 directory that start with the given prefix.
        Uses S3's native prefix filtering for efficiency.
        """
        dir_prefix = self._get_s3_key(path).rstrip("/") + "/"
        full_prefix = dir_prefix + prefix

        items: List[str] = []

        async with (await self._get_async_client()) as s3:
            try:
                paginator = s3.get_paginator('list_objects_v2')
                async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=full_prefix):
                    for obj in page.get('Contents', []):
                        full_key = obj['Key']
                        if full_key.startswith(dir_prefix):
                            filename = full_key[len(dir_prefix):]
                            # Only include files directly in this directory (no subdirs)
                            if '/' not in filename:
                                items.append(filename)
                return items
            except Exception as e:
                import logging
                logger = logging.getLogger("S3Storage")
                logger.error(f"Cannot list files with prefix {prefix} in {path}: {e}")
                return []

def create_storage_backend(storage_uri: str, **kwargs) -> IStorageBackend:
    """Factory function to create storage backends from URI."""
    parsed = urlparse(storage_uri)

    if parsed.scheme == 'file' or not parsed.scheme:
        # Local filesystem
        path = parsed.path if parsed.path else storage_uri
        return LocalFileSystemBackend(path)

    elif parsed.scheme == 's3':
        # S3 storage
        bucket_name = parsed.netloc
        prefix = parsed.path.lstrip('/') if parsed.path else ''

        return S3StorageBackend(
            bucket_name=bucket_name,
            prefix=prefix,
            **kwargs
        )

    else:
        raise ValueError(f"Unsupported storage scheme: {parsed.scheme}")


class InMemoryObject:
    def __init__(self, key: str, data: bytes):
        self.__key = key
        self.__data = data
        self.__modified = datetime.now(UTC)

    @property
    def key(self):
        return self.__key

    @property
    def data(self) -> bytes:
        return self.__data

    @property
    def data_size(self):
        return len(self.data)

    @property
    def modified(self):
        return self.__modified

import threading
class InMemoryStorageBackend(IStorageBackend):
    """In-memory storage backend."""

    def __init__(self, max_total_size: Optional[int] = None, max_file_size: Optional[int] = None) -> None:
        self.max_total_size = max_total_size
        self.max_file_size = max_file_size
        self.__fs_objects: Dict[str, InMemoryObject] = {}
        self.__lock = threading.RLock()
        self.__total_size = 0

    # @contextmanager
    # def __with_lock(self):
    #     try:
    #         yield self.__lock.acquire()
    #     finally:
    #         if self.__lock is not None and self.__lock.locked():
    #             self.__lock.release()
    @contextmanager
    def __with_lock(self):
        self.__lock.acquire()
        try:
            yield
        finally:
            self.__lock.release()

    def __prefix_exists(self, path: str) -> bool:
        return any(k.startswith(path) for k in self.__fs_objects.keys())

    def exists(self, path: str) -> bool:
        with self.__with_lock():
            if path.endswith('/'):
                return self.__prefix_exists(path)
            if path in self.__fs_objects:
                return True
        return self.__prefix_exists(path + '/')


    def read_bytes(self, path: str) -> bytes:
        with self.__with_lock():
            if path in self.__fs_objects:
                return self.__fs_objects[path].data
        raise FileNotFoundError(f"{path} not found in storage")

    def write_bytes(self, path: str, data: bytes,  meta: Optional[dict] = None) -> None:
        if path.endswith('/'):
            raise ValueError(f"{path} is a directory")
        if self.max_file_size and len(data) > self.max_file_size:
            raise ValueError(f"Max size of a file exceeded: {self.max_file_size}")
        if self.max_total_size and self.__total_size + len(data) > self.max_total_size:
            raise ValueError(f"Max size of file system exceeded: {self.max_total_size} ")
        with self.__with_lock():
            self.__fs_objects[path] = InMemoryObject(path, data)
            self.__total_size += len(data)

    def read_text(self, path: str, encoding: str = 'utf-8') -> str:
        return self.read_bytes(path).decode(encoding)

    def write_text(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        self.write_bytes(path, content.encode(encoding))

    def list_dir(self, path: str) -> List[str]:
        prefix = path if path.endswith('/') else path + '/'
        with self.__with_lock():
            keys = list(filter(lambda k: k.startswith(prefix), self.__fs_objects.keys()))
            other_objects = set()
            def p(k:str):
                no_prefix = k.removeprefix(prefix)
                parts = no_prefix.split('/', maxsplit=1)
                if len(parts) > 1:
                    other_objects.add(prefix + parts[0])
                    return False
                return True
            files = list(filter(p, keys))
            return files + list(other_objects)

    def delete(self, path: str) -> None:
        """
        Delete a file or a whole subtree (prefix) from the in-memory storage.

        Behaves like LocalFileSystemBackend.delete and S3StorageBackend.delete:
        - If `path` is an exact file key, remove that file.
        - Additionally, remove all keys that start with `path + '/'`
          (i.e., treat `path` as a directory/prefix).
        """
        with self.__with_lock():
            # 1) delete exact file, if present
            obj = self.__fs_objects.pop(path, None)
            if obj is not None:
                self.__total_size -= obj.data_size

            # 2) delete everything under this "directory" prefix
            prefix = path if path.endswith('/') else path + '/'
            keys_to_delete = [k for k in self.__fs_objects.keys() if k.startswith(prefix)]

            for k in keys_to_delete:
                obj = self.__fs_objects.pop(k, None)
                if obj is not None:
                    self.__total_size -= obj.data_size

    def get_size(self, path: str) -> int:
        with self.__with_lock():
            if path in self.__fs_objects:
                return self.__fs_objects[path].data_size
        raise FileNotFoundError(f"{path} not found in storage")

    def get_modified_time(self, path: str) -> datetime:
        with self.__with_lock():
            if path in self.__fs_objects:
                return self.__fs_objects[path].modified
        raise FileNotFoundError(f"{path} not found in storage")

    def list_with_prefix(self, path: str, prefix: str) -> List[str]:
        """
        List files in memory storage that start with the given prefix.
        """
        dir_prefix = path if path.endswith('/') else path + '/'

        with self.__with_lock():
            matching_files = []
            for key in self.__fs_objects.keys():
                if key.startswith(dir_prefix):
                    filename = key[len(dir_prefix):]
                    # Only files directly in this directory (no subdirs)
                    if '/' not in filename and filename.startswith(prefix):
                        matching_files.append(filename)
            return matching_files

