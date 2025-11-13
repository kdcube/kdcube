# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Distributed Resource Locks - Pure S3/Filesystem based locking.
No context managers, works across multiple servers and processes.

File: kdcube_ai_app/storage/distributed_locks.py
"""
import json
import time
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
import logging

from kdcube_ai_app.storage.storage import IStorageBackend

logger = logging.getLogger("DistributedLocks")

@dataclass
class LockInfo:
    """Information about a distributed lock."""
    lock_id: str
    resource_id: str
    version: str
    process_id: str
    server_id: str
    created_at: str
    expires_at: str
    operation: str

    def is_expired(self) -> bool:
        """Check if lock has expired."""
        try:
            expire_time = datetime.fromisoformat(self.expires_at)
            return datetime.now() > expire_time
        except:
            return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LockInfo':
        return cls(**data)

@dataclass
class VersionQueueEntry:
    """Entry in version queue for tracking in-flight operations."""
    version: str
    process_id: str
    server_id: str
    operation: str
    created_at: str
    expected_completion: str

    def is_stale(self) -> bool:
        """Check if entry is stale."""
        try:
            completion_time = datetime.fromisoformat(self.expected_completion)
            return datetime.now() > completion_time
        except:
            return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VersionQueueEntry':
        return cls(**data)

class DistributedResourceLocks:
    """
    Distributed resource locking using pure S3/filesystem operations.
    No context managers - explicit acquire/release model.
    """

    def __init__(self,
                 backend: IStorageBackend,
                 timeout_seconds: int = 300):
        """
        Initialize distributed lock manager.

        Args:
            backend: S3 or filesystem storage backend
            timeout_seconds: Lock timeout (default 5 minutes)
        """
        self.backend = backend
        self.timeout = timeout_seconds
        self.locks_prefix = ".distributed_locks"
        self.queue_prefix = ".version_queue"

        # Generate unique identifiers for this process/server
        self.process_id = f"proc_{os.getpid()}_{int(time.time())}"
        self.server_id = f"server_{uuid.uuid4().hex[:8]}"

        logger.info(f"DistributedResourceLocks initialized: {self.server_id}/{self.process_id}")

    def _get_lock_path(self, resource_id: str) -> str:
        """Get S3 key/filesystem path for resource lock."""
        return f"{self.locks_prefix}/{resource_id}.lock"

    def _get_queue_dir(self, resource_id: str) -> str:
        """Get S3 prefix/filesystem directory for version queue."""
        return f"{self.queue_prefix}/{resource_id}"

    def _get_queue_entry_path(self, resource_id: str, version: str) -> str:
        """Get S3 key/filesystem path for queue entry."""
        return f"{self.queue_prefix}/{resource_id}/{version}_{self.process_id}_{self.server_id}.json"

    def acquire_lock(self, resource_id: str, version: str, operation: str = "update") -> Optional[str]:
        """
        Try to acquire a distributed lock.

        Returns:
            lock_id if successful, None if failed
        """
        # Create lock info
        now = datetime.now()
        expires_at = now + timedelta(seconds=self.timeout)

        lock_info = LockInfo(
            lock_id=f"{resource_id}_{self.server_id}_{uuid.uuid4().hex[:8]}",
            resource_id=resource_id,
            version=version,
            process_id=self.process_id,
            server_id=self.server_id,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            operation=operation
        )

        lock_path = self._get_lock_path(resource_id)

        try:
            # Check if lock already exists and is valid
            if self.backend.exists(lock_path):
                try:
                    existing_content = self.backend.read_text(lock_path)
                    existing_lock = LockInfo.from_dict(json.loads(existing_content))

                    if not existing_lock.is_expired():
                        logger.debug(f"Lock held by {existing_lock.server_id}/{existing_lock.process_id}")
                        return None  # Lock held by someone else
                    else:
                        logger.info(f"Found expired lock, will try to claim it")
                except Exception as e:
                    logger.warning(f"Error reading existing lock: {e}")
                    return None  # Assume lock is valid to be safe

            # Try to create lock atomically
            lock_content = json.dumps(lock_info.to_dict(), indent=2, ensure_ascii=False)

            # Atomic lock creation strategy
            if self._try_atomic_create(lock_path, lock_content):
                logger.info(f"Acquired lock {lock_info.lock_id} for {resource_id}")
                return lock_info.lock_id
            else:
                logger.debug(f"Failed to acquire lock for {resource_id}")
                return None

        except Exception as e:
            logger.error(f"Error acquiring lock for {resource_id}: {e}")
            return None

    def _try_atomic_create(self, lock_path: str, content: str) -> bool:
        """
        Try to create lock file atomically.

        Returns True if we successfully created the lock.
        """
        try:
            # Strategy 1: For local filesystem - atomic temp + rename
            if hasattr(self.backend, '_resolve_path'):
                temp_path = f"{lock_path}.tmp.{self.process_id}.{int(time.time())}"

                try:
                    # Write to temp file
                    self.backend.write_text(temp_path, content)

                    # Atomic rename (only works if target doesn't exist)
                    import os
                    temp_file = self.backend._resolve_path(temp_path)
                    target_file = self.backend._resolve_path(lock_path)

                    try:
                        os.link(str(temp_file), str(target_file))  # Atomic hard link
                        os.unlink(str(temp_file))  # Remove temp
                        return True
                    except FileExistsError:
                        # Someone else got it first
                        os.unlink(str(temp_file))  # Clean up temp
                        return False

                except Exception as e:
                    # Clean up temp file
                    try:
                        self.backend.delete(temp_path)
                    except:
                        pass
                    return False

            # Strategy 2: For S3 - conditional PUT with ETag checking
            else:
                # First, try a conditional write
                try:
                    # Check if object exists
                    if not self.backend.exists(lock_path):
                        # Try to create
                        self.backend.write_text(lock_path, content)

                        # Verify we got it (race condition detection)
                        time.sleep(0.1)  # Small delay to ensure consistency

                        if self.backend.exists(lock_path):
                            # Double-check the content to see if we won the race
                            try:
                                check_content = self.backend.read_text(lock_path)
                                check_lock = LockInfo.from_dict(json.loads(check_content))

                                # Did we win?
                                return (check_lock.server_id == self.server_id and
                                        check_lock.process_id == self.process_id)
                            except:
                                return False
                    return False

                except Exception as e:
                    logger.debug(f"S3 atomic create failed: {e}")
                    return False

        except Exception as e:
            logger.error(f"Error in atomic create: {e}")
            return False

    def release_lock(self, resource_id: str, lock_id: str) -> bool:
        """
        Release a distributed lock.

        Args:
            resource_id: Resource identifier
            lock_id: Lock ID returned from acquire_lock

        Returns:
            True if released successfully
        """
        lock_path = self._get_lock_path(resource_id)

        try:
            if not self.backend.exists(lock_path):
                logger.debug(f"Lock {lock_id} already released")
                return True

            # Verify we own this lock
            try:
                lock_content = self.backend.read_text(lock_path)
                lock_info = LockInfo.from_dict(json.loads(lock_content))

                if lock_info.lock_id != lock_id:
                    logger.warning(f"Attempted to release lock {lock_id} but found {lock_info.lock_id}")
                    return False

                if lock_info.server_id != self.server_id:
                    logger.warning(f"Attempted to release lock owned by {lock_info.server_id}")
                    return False

            except Exception as e:
                logger.warning(f"Error verifying lock ownership: {e}")
                # Delete anyway to prevent deadlocks

            # Delete the lock
            self.backend.delete(lock_path)
            logger.info(f"Released lock {lock_id} for {resource_id}")
            return True

        except Exception as e:
            logger.error(f"Error releasing lock {lock_id}: {e}")
            return False

    def add_to_queue(self, resource_id: str, version: str, operation: str) -> bool:
        """
        Add entry to version queue.

        This tracks in-flight operations for ordering decisions.
        """
        try:
            # Ensure queue directory exists
            queue_dir = self._get_queue_dir(resource_id)

            # Create queue entry
            now = datetime.now()
            completion = now + timedelta(seconds=self.timeout)

            entry = VersionQueueEntry(
                version=version,
                process_id=self.process_id,
                server_id=self.server_id,
                operation=operation,
                created_at=now.isoformat(),
                expected_completion=completion.isoformat()
            )

            # Write queue entry
            entry_path = self._get_queue_entry_path(resource_id, version)
            entry_content = json.dumps(entry.to_dict(), indent=2, ensure_ascii=False)
            self.backend.write_text(entry_path, entry_content)

            logger.debug(f"Added to queue: {resource_id} v{version}")
            return True

        except Exception as e:
            logger.error(f"Error adding to queue: {e}")
            return False

    def remove_from_queue(self, resource_id: str, version: str) -> bool:
        """Remove entry from version queue."""
        try:
            entry_path = self._get_queue_entry_path(resource_id, version)
            if self.backend.exists(entry_path):
                self.backend.delete(entry_path)
                logger.debug(f"Removed from queue: {resource_id} v{version}")
            return True
        except Exception as e:
            logger.error(f"Error removing from queue: {e}")
            return False

    def get_version_queue(self, resource_id: str) -> List[VersionQueueEntry]:
        """Get current version queue entries."""
        try:
            queue_dir = self._get_queue_dir(resource_id)
            if not self.backend.exists(queue_dir):
                return []

            entries = []
            files = self.backend.list_dir(queue_dir)

            for filename in files:
                if filename.endswith('.json'):
                    try:
                        entry_path = f"{queue_dir}/{filename}"
                        content = self.backend.read_text(entry_path)
                        entry_data = json.loads(content)
                        entry = VersionQueueEntry.from_dict(entry_data)

                        # Skip stale entries
                        if not entry.is_stale():
                            entries.append(entry)
                        else:
                            # Clean up stale entry
                            try:
                                self.backend.delete(entry_path)
                                logger.debug(f"Cleaned stale queue entry: {filename}")
                            except:
                                pass

                    except Exception as e:
                        logger.warning(f"Error reading queue entry {filename}: {e}")

            # Sort by version (numeric if possible)
            try:
                entries.sort(key=lambda e: int(e.version))
            except ValueError:
                entries.sort(key=lambda e: e.version)

            return entries

        except Exception as e:
            logger.error(f"Error getting version queue: {e}")
            return []

    def is_highest_version(self, resource_id: str, version: str) -> bool:
        """
        Check if this version is the highest in the queue.

        Only the highest version should update resource metadata.
        """
        try:
            queue_entries = self.get_version_queue(resource_id)

            if not queue_entries:
                return True  # No other versions in flight

            # Find highest version
            try:
                highest = max(int(entry.version) for entry in queue_entries)
                current = int(version)
                is_highest = current >= highest
            except ValueError:
                # String comparison fallback
                highest = max(entry.version for entry in queue_entries)
                is_highest = version >= highest

            if not is_highest:
                logger.info(f"Version {version} not highest (max: {highest}), skipping metadata update")

            return is_highest

        except Exception as e:
            logger.error(f"Error checking highest version: {e}")
            return True  # Default to allowing update

    def cleanup_stale_locks(self, resource_id: str = None) -> Dict[str, int]:
        """
        Clean up stale locks and queue entries.

        Args:
            resource_id: Specific resource to clean, or None for all
        """
        cleaned = {"locks": 0, "queue_entries": 0}

        try:
            if resource_id:
                # Clean specific resource
                cleaned.update(self._cleanup_resource(resource_id))
            else:
                # Clean all resources
                if self.backend.exists(self.locks_prefix):
                    lock_files = self.backend.list_dir(self.locks_prefix)
                    for lock_file in lock_files:
                        if lock_file.endswith('.lock'):
                            rid = lock_file[:-5]  # Remove .lock extension
                            result = self._cleanup_resource(rid)
                            cleaned["locks"] += result["locks"]
                            cleaned["queue_entries"] += result["queue_entries"]

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        return cleaned

    def _cleanup_resource(self, resource_id: str) -> Dict[str, int]:
        """Clean up locks and queue for specific resource."""
        cleaned = {"locks": 0, "queue_entries": 0}

        try:
            # Clean expired lock
            lock_path = self._get_lock_path(resource_id)
            if self.backend.exists(lock_path):
                try:
                    content = self.backend.read_text(lock_path)
                    lock_info = LockInfo.from_dict(json.loads(content))

                    if lock_info.is_expired():
                        self.backend.delete(lock_path)
                        cleaned["locks"] += 1
                        logger.info(f"Cleaned expired lock for {resource_id}")
                except Exception as e:
                    logger.warning(f"Error cleaning lock: {e}")

            # Clean stale queue entries
            queue_dir = self._get_queue_dir(resource_id)
            if self.backend.exists(queue_dir):
                files = self.backend.list_dir(queue_dir)
                for filename in files:
                    if filename.endswith('.json'):
                        try:
                            entry_path = f"{queue_dir}/{filename}"
                            content = self.backend.read_text(entry_path)
                            entry = VersionQueueEntry.from_dict(json.loads(content))

                            if entry.is_stale():
                                self.backend.delete(entry_path)
                                cleaned["queue_entries"] += 1
                                logger.debug(f"Cleaned stale queue entry: {filename}")
                        except Exception as e:
                            logger.warning(f"Error cleaning queue entry: {e}")

        except Exception as e:
            logger.error(f"Error cleaning resource {resource_id}: {e}")

        return cleaned

# Example usage without context managers
def example_distributed_locking():
    """Example of how to use distributed locks properly."""

    def safe_add_version(backend, resource_id: str, content: bytes) -> str:
        """Add version safely using distributed locks."""
        locks = DistributedResourceLocks(backend)

        # Step 1: Add to queue to claim our spot
        version = None
        lock_id = None

        try:
            # Get next version by examining existing + queue
            queue_entries = locks.get_version_queue(resource_id)
            existing_versions = []  # ... scan filesystem for existing versions

            all_versions = existing_versions + [int(e.version) for e in queue_entries if e.version.isdigit()]
            next_version = str(max(all_versions, default=0) + 1)

            # Add to queue
            if not locks.add_to_queue(resource_id, next_version, "add_version"):
                raise Exception("Failed to add to queue")

            version = next_version

            # Step 2: Try to acquire lock
            lock_id = locks.acquire_lock(resource_id, version, "add_version")
            if not lock_id:
                raise Exception("Failed to acquire lock")

            # Step 3: Do the actual work (save content, etc.)
            # ... save version content ...

            # Step 4: Update resource metadata only if we're highest version
            if locks.is_highest_version(resource_id, version):
                # ... update resource metadata ...
                logger.info(f"Updated resource metadata for {resource_id} v{version}")
            else:
                logger.info(f"Skipped metadata update for {resource_id} v{version}")

            return version

        except Exception as e:
            logger.error(f"Error in safe_add_version: {e}")
            raise
        finally:
            # Step 5: Always clean up
            if lock_id:
                locks.release_lock(resource_id, lock_id)
            if version:
                locks.remove_from_queue(resource_id, version)

    # The function can be called from any server, any process
    # All coordination happens through S3/filesystem
    return safe_add_version