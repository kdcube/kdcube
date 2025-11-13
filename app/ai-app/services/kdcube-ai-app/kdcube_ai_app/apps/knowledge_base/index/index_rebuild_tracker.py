# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
index_rebuild_tracker.py
Persistent rebuild status tracker using storage backend.
Scales across multiple servers and survives restarts.
"""
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
import logging

from kdcube_ai_app.storage.storage import IStorageBackend

logger = logging.getLogger("KnowledgeBase.RebuildTracker")

class RebuildStatus(Enum):
    """Rebuild operation status."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class RebuildOperation:
    """Rebuild operation record."""
    operation_id: str
    project_name: str
    status: RebuildStatus
    started_by: Optional[str]  # Server/worker ID
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    heartbeat: Optional[str] = None  # Last heartbeat timestamp

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['status'] = self.status.value  # Convert enum to string
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RebuildOperation':
        """Create from dictionary."""
        data = data.copy()
        data['status'] = RebuildStatus(data['status'])  # Convert string to enum
        return cls(**data)

class PersistentRebuildTracker:
    """
    Persistent rebuild status tracker using storage backend.
    Thread-safe and scales across multiple servers.
    """

    def __init__(self, backend: IStorageBackend, index_prefix: str = ".rebuild_ops"):
        """
        Initialize the rebuild tracker.

        Args:
            backend: Storage backend for persistence
            index_prefix: Directory prefix for rebuild operation files
        """
        self.backend = backend
        self.index_prefix = index_prefix
        self.heartbeat_timeout = 300  # 5 minutes
        self.cleanup_after_days = 7  # Keep completed operations for 7 days

        logger.info(f"PersistentRebuildTracker initialized with backend {backend.__class__.__name__}")

    def _get_operation_path(self, operation_id: str) -> str:
        """Get storage path for an operation."""
        return f"{self.index_prefix}/{operation_id}.json"

    def _get_project_active_path(self, project_name: str) -> str:
        """Get path for project's active operation pointer."""
        return f"{self.index_prefix}/active_{project_name}.txt"

    def _generate_operation_id(self) -> str:
        """Generate unique operation ID."""
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        return f"rebuild_{timestamp}_{unique_id}"

    def _save_operation(self, operation: RebuildOperation):
        """Save operation to storage atomically."""
        operation_path = self._get_operation_path(operation.operation_id)

        try:
            # Write to temporary file first (atomic write pattern)
            temp_path = f"{operation_path}.tmp.{int(time.time())}"
            content = json.dumps(operation.to_dict(), indent=2, ensure_ascii=False)
            self.backend.write_text(temp_path, content)

            # Atomic rename (if supported) or overwrite
            try:
                if hasattr(self.backend, '_resolve_path'):
                    import os
                    temp_file = self.backend._resolve_path(temp_path)
                    target_file = self.backend._resolve_path(operation_path)
                    os.rename(str(temp_file), str(target_file))
                else:
                    # For S3 and other backends, copy and delete
                    final_content = self.backend.read_text(temp_path)
                    self.backend.write_text(operation_path, final_content)
                    self.backend.delete(temp_path)
            except Exception:
                # Fallback: direct write
                self.backend.write_text(operation_path, content)
                try:
                    self.backend.delete(temp_path)
                except:
                    pass

            logger.debug(f"Saved operation {operation.operation_id} with status {operation.status.value}")
        except Exception as e:
            logger.error(f"Error saving operation {operation.operation_id}: {e}")
            raise

    def _load_operation(self, operation_id: str) -> Optional[RebuildOperation]:
        """Load operation from storage."""
        operation_path = self._get_operation_path(operation_id)

        try:
            if not self.backend.exists(operation_path):
                return None

            content = self.backend.read_text(operation_path)
            data = json.loads(content)
            return RebuildOperation.from_dict(data)
        except Exception as e:
            logger.error(f"Error loading operation {operation_id}: {e}")
            return None

    def _set_active_operation(self, project_name: str, operation_id: str):
        """Set the active operation for a project."""
        active_path = self._get_project_active_path(project_name)
        self.backend.write_text(active_path, operation_id)

    def _get_active_operation_id(self, project_name: str) -> Optional[str]:
        """Get the active operation ID for a project."""
        active_path = self._get_project_active_path(project_name)

        try:
            if not self.backend.exists(active_path):
                return None
            return self.backend.read_text(active_path).strip()
        except Exception as e:
            logger.error(f"Error getting active operation for {project_name}: {e}")
            return None

    def _clear_active_operation(self, project_name: str):
        """Clear the active operation for a project."""
        active_path = self._get_project_active_path(project_name)
        try:
            if self.backend.exists(active_path):
                self.backend.delete(active_path)
        except Exception as e:
            logger.warning(f"Error clearing active operation for {project_name}: {e}")

    def start_rebuild(self, project_name: str, started_by: str = "api") -> RebuildOperation:
        """
        Start a new rebuild operation for a project.

        Args:
            project_name: Name of the project
            started_by: Identifier of who/what started the rebuild

        Returns:
            RebuildOperation object

        Raises:
            ValueError: If a rebuild is already running for this project
        """
        # Check if there's already an active rebuild
        existing_operation = self.get_active_operation(project_name)
        if existing_operation and existing_operation.status in [RebuildStatus.QUEUED, RebuildStatus.RUNNING]:
            # Check if it's stale (no heartbeat)
            if not self._is_operation_stale(existing_operation):
                raise ValueError(f"Rebuild already running for project {project_name}: {existing_operation.operation_id}")
            else:
                # Mark stale operation as failed
                logger.warning(f"Marking stale operation as failed: {existing_operation.operation_id}")
                self.mark_failed(existing_operation.operation_id, "Operation appears to be stale (no heartbeat)")

        # Create new operation
        operation = RebuildOperation(
            operation_id=self._generate_operation_id(),
            project_name=project_name,
            status=RebuildStatus.QUEUED,
            started_by=started_by,
            started_at=datetime.now().isoformat(),
            heartbeat=datetime.now().isoformat()
        )

        # Save operation and set as active
        self._save_operation(operation)
        self._set_active_operation(project_name, operation.operation_id)

        logger.info(f"Started rebuild operation {operation.operation_id} for project {project_name}")
        return operation

    def mark_running(self, operation_id: str, started_by: str = None) -> Optional[RebuildOperation]:
        """Mark operation as running."""
        operation = self._load_operation(operation_id)
        if not operation:
            return None

        operation.status = RebuildStatus.RUNNING
        operation.heartbeat = datetime.now().isoformat()
        if started_by:
            operation.started_by = started_by

        self._save_operation(operation)
        logger.info(f"Marked operation {operation_id} as running")
        return operation

    def update_progress(self, operation_id: str, progress: Dict[str, Any]) -> Optional[RebuildOperation]:
        """Update operation progress and heartbeat."""
        operation = self._load_operation(operation_id)
        if not operation:
            return None

        operation.progress = progress
        operation.heartbeat = datetime.now().isoformat()

        self._save_operation(operation)
        logger.debug(f"Updated progress for operation {operation_id}")
        return operation

    def heartbeat(self, operation_id: str) -> Optional[RebuildOperation]:
        """Update operation heartbeat."""
        operation = self._load_operation(operation_id)
        if not operation:
            return None

        operation.heartbeat = datetime.now().isoformat()
        self._save_operation(operation)
        return operation

    def mark_completed(self, operation_id: str, result: Dict[str, Any]) -> Optional[RebuildOperation]:
        """Mark operation as completed."""
        operation = self._load_operation(operation_id)
        if not operation:
            return None

        now = datetime.now().isoformat()
        start_time = datetime.fromisoformat(operation.started_at)
        current_time = datetime.fromisoformat(now)

        operation.status = RebuildStatus.COMPLETED
        operation.completed_at = now
        operation.duration_seconds = (current_time - start_time).total_seconds()
        operation.result = result
        operation.heartbeat = now

        self._save_operation(operation)
        self._clear_active_operation(operation.project_name)

        logger.info(f"Marked operation {operation_id} as completed in {operation.duration_seconds:.2f}s")
        return operation

    def mark_failed(self, operation_id: str, error: str) -> Optional[RebuildOperation]:
        """Mark operation as failed."""
        operation = self._load_operation(operation_id)
        if not operation:
            return None

        now = datetime.now().isoformat()
        if operation.started_at:
            start_time = datetime.fromisoformat(operation.started_at)
            current_time = datetime.fromisoformat(now)
            operation.duration_seconds = (current_time - start_time).total_seconds()

        operation.status = RebuildStatus.FAILED
        operation.completed_at = now
        operation.error = error
        operation.heartbeat = now

        self._save_operation(operation)
        self._clear_active_operation(operation.project_name)

        logger.error(f"Marked operation {operation_id} as failed: {error}")
        return operation

    def get_operation(self, operation_id: str) -> Optional[RebuildOperation]:
        """Get operation by ID."""
        return self._load_operation(operation_id)

    def get_active_operation(self, project_name: str) -> Optional[RebuildOperation]:
        """Get the active operation for a project."""
        operation_id = self._get_active_operation_id(project_name)
        if not operation_id:
            return None
        return self._load_operation(operation_id)

    def _is_operation_stale(self, operation: RebuildOperation) -> bool:
        """Check if an operation is stale (no recent heartbeat)."""
        if not operation.heartbeat:
            return True

        try:
            heartbeat_time = datetime.fromisoformat(operation.heartbeat)
            now = datetime.now()
            return (now - heartbeat_time).total_seconds() > self.heartbeat_timeout
        except Exception:
            return True

    def list_operations(self, project_name: str = None, limit: int = 50) -> List[RebuildOperation]:
        """List operations, optionally filtered by project."""
        operations = []

        try:
            # List all operation files
            if not self.backend.exists(self.index_prefix):
                return operations

            files = self.backend.list_dir(self.index_prefix)
            operation_files = [f for f in files if f.endswith('.json') and f.startswith('rebuild_')]

            # Sort by filename (which includes timestamp) - newest first
            operation_files.sort(reverse=True)

            for filename in operation_files[:limit]:
                operation_id = filename.replace('.json', '')
                operation = self._load_operation(operation_id)

                if operation and (not project_name or operation.project_name == project_name):
                    operations.append(operation)

            return operations

        except Exception as e:
            logger.error(f"Error listing operations: {e}")
            return operations

    def cleanup_old_operations(self, days: int = None) -> Dict[str, int]:
        """Clean up old completed/failed operations."""
        cleanup_days = days or self.cleanup_after_days
        cutoff_time = datetime.now() - timedelta(days=cleanup_days)

        deleted_count = 0
        error_count = 0

        try:
            operations = self.list_operations(limit=1000)  # Get more for cleanup

            for operation in operations:
                # Only clean up completed/failed operations
                if operation.status in [RebuildStatus.COMPLETED, RebuildStatus.FAILED]:
                    try:
                        completed_time = datetime.fromisoformat(operation.completed_at)
                        if completed_time < cutoff_time:
                            operation_path = self._get_operation_path(operation.operation_id)
                            self.backend.delete(operation_path)
                            deleted_count += 1
                            logger.debug(f"Cleaned up old operation: {operation.operation_id}")
                    except Exception as e:
                        logger.error(f"Error cleaning up operation {operation.operation_id}: {e}")
                        error_count += 1

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            error_count += 1

        logger.info(f"Cleanup completed: deleted {deleted_count} operations, {error_count} errors")
        return {
            "deleted": deleted_count,
            "errors": error_count,
            "cutoff_days": cleanup_days
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics."""
        try:
            operations = self.list_operations(limit=1000)

            stats = {
                "total_operations": len(operations),
                "by_status": {},
                "by_project": {},
                "active_operations": 0,
                "heartbeat_timeout_seconds": self.heartbeat_timeout
            }

            for operation in operations:
                # Count by status
                status = operation.status.value
                stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

                # Count by project
                project = operation.project_name
                stats["by_project"][project] = stats["by_project"].get(project, 0) + 1

                # Count active operations
                if operation.status in [RebuildStatus.QUEUED, RebuildStatus.RUNNING]:
                    if not self._is_operation_stale(operation):
                        stats["active_operations"] += 1

            return stats

        except Exception as e:
            logger.error(f"Error getting tracker stats: {e}")
            return {"error": str(e)}