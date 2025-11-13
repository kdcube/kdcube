# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import hashlib
import json
import time
import os
from datetime import datetime
from typing import Optional, Dict, Any, List, Literal, Union, Tuple

from kdcube_ai_app.apps.knowledge_base.index.content_index import IContentIndexManager
from kdcube_ai_app.storage.distributed_locks import LockInfo
from kdcube_ai_app.storage.storage import IStorageBackend, logger

# Processing stages supported by the knowledge base
ProcessingStage = Literal[
    "raw",
    "extraction",
    "segmentation",
    "metadata",
    "enrichment",
    "summarization",
    "embedding",
    "tf-idf",
    "search_indexing",
    "usage"
]
VALID_STAGES = ["raw", "extraction", "segmentation", "enrichment", "metadata", "summarization", "embedding", "tf-idf", "search_indexing", "usage"]


class KnowledgeBaseStorage:
    """High-level storage manager for Knowledge Base with multi-stage processing support."""

    def __init__(self, backend: IStorageBackend, content_index:IContentIndexManager):
        self.backend = backend
        self.content_index = content_index

    def _validate_stage(self, stage: ProcessingStage) -> None:
        """Validate that the stage is supported."""
        if stage not in VALID_STAGES:
            raise ValueError(f"Invalid processing stage: {stage}. Must be one of {VALID_STAGES}")

    # ================================================================================
    #                          RESOURCE ID AND PATH GENERATION
    # ================================================================================

    # in kdcube_ai_app/apps/knowledge_base/storage.py (class KnowledgeBaseStorage)

    def generate_resource_id(self, source_type: str, source_name: str, provider: Optional[str] = None) -> str:
        """Generate a provider-namespaced resource ID from source type and name.
           Backwards-compatible: if provider is None, keep old format."""
        def _sanitize(s: str) -> str:
            return (s or "").replace("/", "_").replace("\\", "_").replace(":", "_") \
                            .replace("?", "_").replace("&", "_").replace("=", "_") \
                            .replace("|", "_").strip()

        safe_type = _sanitize(source_type)
        safe_name = _sanitize(source_name)
        if provider:
            safe_provider = _sanitize(provider)
            return f"{safe_provider}|{safe_type}|{safe_name}"
        return f"{safe_type}|{safe_name}"

    def get_resource_base_path(self, resource_id: str) -> str:
        """Get the base path for a resource (always in data/raw)."""
        return f"data/raw/{resource_id}"

    def get_resource_metadata_path(self, resource_id: str) -> str:
        """Get the path to a resource's metadata file (always in data/raw)."""
        return f"data/raw/{resource_id}/metadata.json"

    def get_resource_versions_path(self, resource_id: str) -> str:
        """Get the path to a resource's versions directory (always in data/raw)."""
        return f"data/raw/{resource_id}/versions"

    def get_resource_version_path(self, resource_id: str, version: str) -> str:
        """Get the path to a specific version directory (always in data/raw)."""
        return f"data/raw/{resource_id}/versions/{version}"

    def get_resource_version_metadata_path(self, resource_id: str, version: str) -> str:
        """Get the path to a specific version's metadata file (always in data/raw)."""
        return f"data/raw/{resource_id}/versions/{version}/metadata.json"

    # ================================================================================
    #                          STAGE-AWARE PATH GENERATION
    # ================================================================================

    def get_stage_resource_path(self, stage: ProcessingStage, resource_id: str) -> str:
        """Get the base path for a resource in a specific processing stage."""
        self._validate_stage(stage)
        return f"data/{stage}/{resource_id}"

    def get_stage_version_path(self, stage: ProcessingStage, resource_id: str, version: str) -> str:
        """Get the path to a specific version directory in a processing stage."""
        self._validate_stage(stage)
        return f"data/{stage}/{resource_id}/versions/{version}"

    def get_stage_version_path_with_subfolder(self, stage: ProcessingStage, resource_id: str, version: str, subfolder: Optional[str] = None) -> str:
        """Get the path to a specific version directory in a processing stage, optionally with subfolder."""
        self._validate_stage(stage)
        base_path = f"data/{stage}/{resource_id}/versions/{version}"
        if subfolder:
            return f"{base_path}/{subfolder}"
        return base_path

    def get_stage_file_path(self, stage: ProcessingStage, resource_id: str, version: str, filename: str, subfolder: Optional[str] = None) -> str:
        """Get the path to a specific file in a processing stage, optionally in a subfolder."""
        self._validate_stage(stage)
        base_path = f"data/{stage}/{resource_id}/versions/{version}"
        if subfolder:
            return f"{base_path}/{subfolder}/{filename}"
        return f"{base_path}/{filename}"

    # ================================================================================
    #                          RESOURCE AND VERSION METADATA (always from raw)
    # ================================================================================

    def resource_exists(self, resource_id: str) -> bool:
        """Check if a resource exists (checks data/raw)."""
        return self.backend.exists(self.get_resource_metadata_path(resource_id))

    def get_resource_metadata(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get resource metadata (always from data/raw)."""
        metadata_path = self.get_resource_metadata_path(resource_id)
        if not self.backend.exists(metadata_path):
            return None

        try:
            metadata_json = self.backend.read_text(metadata_path)
            return json.loads(metadata_json)
        except Exception as e:
            logger.error(f"Error reading metadata for {resource_id}: {e}")
            return None

    def save_resource_metadata(self, resource_id: str, metadata: Dict[str, Any]) -> None:
        """Save resource metadata (always to data/raw)."""
        metadata_path = self.get_resource_metadata_path(resource_id)
        metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False)
        self.backend.write_text(metadata_path, metadata_json)

    def get_version_metadata(self, resource_id: str, version: str) -> Optional[Dict[str, Any]]:
        """Get version-specific metadata (always from data/raw)."""
        metadata_path = self.get_resource_version_metadata_path(resource_id, version)
        if not self.backend.exists(metadata_path):
            return None

        try:
            metadata_json = self.backend.read_text(metadata_path)
            return json.loads(metadata_json)
        except Exception as e:
            logger.error(f"Error reading version metadata for {resource_id} v{version}: {e}")
            return None

    def save_version_metadata(self, resource_id: str, version: str, metadata: Dict[str, Any]) -> None:
        """Save version-specific metadata (always to data/raw)."""
        metadata_path = self.get_resource_version_metadata_path(resource_id, version)
        metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False)
        self.backend.write_text(metadata_path, metadata_json)

    def save_version_content(self, resource_id: str, version: str, filename: str, content: bytes) -> str:
        """Save version content and return the path."""
        version_dir = self.get_resource_version_path(resource_id, version)
        content_path = f"{version_dir}/{filename}"
        self.backend.write_bytes(content_path, content)
        return content_path

    def _get_version_content(self, resource_id: str, version: str, filename: str) -> bytes:
        """Get version content."""
        version_dir = self.get_resource_version_path(resource_id, version)
        content_path = f"{version_dir}/{filename}"
        return self.backend.read_bytes(content_path)

    def get_latest_version(self, resource_id: str) -> Optional[str]:
        """Get the latest version number for a resource."""
        metadata = self.get_resource_metadata(resource_id)
        if not metadata:
            return None
        return metadata.get("version")

    def index_resource_content(self,
                               content_hash: str,
                               resource_id: str,
                               max_retries: int = 3) -> bool:
        """
        Safely add content index mapping with race condition protection.

        Prevents duplicate entries when multiple processes add the same content.
        """
        for attempt in range(max_retries):
            try:
                # Check if mapping already exists
                existing_resource = self.content_index.check_content_exists(content_hash)
                if existing_resource:
                    if existing_resource == resource_id:
                        # Same mapping already exists, this is fine
                        logger.debug(f"Content mapping {content_hash[:16]}... -> {resource_id} already exists")
                        return True
                    else:
                        # Different resource has this content hash
                        logger.info(f"Content hash {content_hash[:16]}... already mapped to {existing_resource}")
                        return False

                # Try to add the mapping
                try:
                    self.content_index.add_content_mapping(content_hash, resource_id)
                    return True
                except Exception as e:
                    # Check if someone else added it in the meantime
                    existing_resource = self.content_index.check_content_exists(content_hash)
                    if existing_resource:
                        if existing_resource == resource_id:
                            # Someone else added the same mapping, that's fine
                            return True
                        else:
                            # Someone else added different mapping
                            logger.info(f"Content hash {content_hash[:16]}... was mapped to {existing_resource} by another process")
                            return False

                    # Real error
                    logger.error(f"Error adding content mapping: {e}")
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(0.1 * (attempt + 1))
                    continue

            except Exception as e:
                logger.error(f"Error in safe content index update attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))

        return False

    def list_versions(self, resource_id: str) -> List[str]:
        """List all versions for a resource (from data/raw)."""
        versions_path = self.get_resource_versions_path(resource_id)
        if not self.backend.exists(versions_path):
            return []

        versions = self.backend.list_dir(versions_path)
        # Sort versions numerically if possible
        try:
            return sorted(versions, key=int)
        except ValueError:
            return sorted(versions)

    # ================================================================================
    #                          STAGE CONTENT MANAGEMENT
    # ================================================================================

    def stage_exists(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, filename: Optional[str] = None, subfolder: Optional[str] = None) -> bool:
        """Check if content exists in a specific processing stage, optionally in a subfolder."""
        self._validate_stage(stage)

        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return False

        if filename is None:
            # Check if the version directory (or subfolder) exists
            if subfolder:
                stage_path = self.get_stage_version_path_with_subfolder(stage, resource_id, version, subfolder)
            else:
                stage_path = self.get_stage_version_path(stage, resource_id, version)
            return self.backend.exists(stage_path)
        else:
            # Check if the specific file exists
            stage_file_path = self.get_stage_file_path(stage, resource_id, version, filename, subfolder)
            return self.backend.exists(stage_file_path)

    def get_stage_content(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, filename: Optional[str] = None, as_text: bool = True, subfolder: Optional[str] = None) -> Optional[Union[str, bytes]]:
        """Get content from a specific processing stage, optionally from a subfolder."""
        self._validate_stage(stage)

        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return None

        if filename is None:
            # Get filename from version metadata
            version_metadata = self.get_version_metadata(resource_id, version)
            if not version_metadata:
                return None
            filename = version_metadata.get("filename")
            if not filename:
                return None

        stage_file_path = self.get_stage_file_path(stage, resource_id, version, filename, subfolder)

        if not self.backend.exists(stage_file_path):
            return None

        try:
            if as_text:
                return self.backend.read_text(stage_file_path)
            else:
                return self.backend.read_bytes(stage_file_path)
        except Exception as e:
            logger.error(f"Error reading {stage} content for {resource_id} v{version}/{filename}: {e}")
            return None

    def save_stage_content(self, stage: ProcessingStage, resource_id: str, version: str, filename: str, content: Union[str, bytes], subfolder: Optional[str] = None) -> str:
        """Save content to a specific processing stage, optionally in a subfolder."""
        self._validate_stage(stage)

        stage_file_path = self.get_stage_file_path(stage, resource_id, version, filename, subfolder)

        try:
            if isinstance(content, str):
                self.backend.write_text(stage_file_path, content)
            else:
                self.backend.write_bytes(stage_file_path, content)
            return stage_file_path
        except Exception as e:
            logger.error(f"Error saving {stage} content for {resource_id} v{version}/{filename}: {e}")
            raise

    def list_stage_files(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, subfolder: Optional[str] = None) -> List[str]:
        """List all files in a specific processing stage for a resource version, optionally in a subfolder."""
        self._validate_stage(stage)

        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return []

        if subfolder:
            stage_path = self.get_stage_version_path_with_subfolder(stage, resource_id, version, subfolder)
        else:
            stage_path = self.get_stage_version_path(stage, resource_id, version)

        if not self.backend.exists(stage_path):
            return []

        try:
            return self.backend.list_dir(stage_path)
        except Exception as e:
            logger.error(f"Error listing {stage} files for {resource_id} v{version}: {e}")
            return []

    def delete_stage_content(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, filename: Optional[str] = None, subfolder: Optional[str] = None) -> bool:
        """Delete content from a specific processing stage, optionally from a subfolder."""
        self._validate_stage(stage)

        try:
            if filename is None:
                # Delete entire version directory (or subfolder) for this stage
                if version is None:
                    # Delete entire resource for this stage
                    stage_resource_path = self.get_stage_resource_path(stage, resource_id)
                    self.backend.delete(stage_resource_path)
                else:
                    if subfolder:
                        stage_path = self.get_stage_version_path_with_subfolder(stage, resource_id, version, subfolder)
                    else:
                        stage_path = self.get_stage_version_path(stage, resource_id, version)
                    self.backend.delete(stage_path)
            else:
                # Delete specific file
                if version is None:
                    version = self.get_latest_version(resource_id)
                    if not version:
                        return False

                stage_file_path = self.get_stage_file_path(stage, resource_id, version, filename, subfolder)
                self.backend.delete(stage_file_path)

            return True
        except Exception as e:
            logger.error(f"Error deleting {stage} content for {resource_id}: {e}")
            return False

    # ================================================================================
    #                          CONVENIENCE METHODS
    # ================================================================================

    def get_raw_content(self, resource_id: str, version: Optional[str] = None, as_text: bool = True) -> Optional[Union[str, bytes]]:
        """Convenience method to get raw content."""
        return self.get_stage_content("raw", resource_id, version, as_text=as_text)

    def save_raw_content(self, resource_id: str, version: str, filename: str, content: Union[str, bytes]) -> str:
        """Convenience method to save raw content."""
        return self.save_stage_content("raw", resource_id, version, filename, content)

    def get_extraction_results(self, resource_id: str, version: Optional[str] = None, filename: str = "extraction.json") -> Optional[Dict[str, Any]]:
        """Get extraction results as JSON."""
        content = self.get_stage_content("extraction", resource_id, version, filename, as_text=True)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing extraction results JSON: {e}")
        return None

    def save_extraction_results(self, resource_id: str, version: str, results: List[Dict[str, Any]], filename: str = "extraction.json") -> str:
        """Save extraction results as JSON."""
        content = json.dumps(results, indent=2, ensure_ascii=False)
        return self.save_stage_content("extraction", resource_id, version, filename, content)

    def get_segments(self, resource_id: str, version: Optional[str] = None, filename: str = "segments.json", subfolder: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Get segmentation results as JSON, optionally from a subfolder."""
        content = self.get_stage_content("segmentation",
                                         resource_id,
                                         version,
                                         filename,
                                         as_text=True,
                                         subfolder=subfolder)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing segments JSON: {e}")
        return None

    def save_segments(self, resource_id: str, version: str, segments: List[Dict[str, Any]], filename: str = "segments.json", subfolder: Optional[str] = None) -> str:
        """Save segmentation results as JSON, optionally in a subfolder."""
        content = json.dumps(segments, indent=2, ensure_ascii=False)
        return self.save_stage_content("segmentation", resource_id, version, filename, content, subfolder=subfolder)

    def list_subfolders(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None) -> List[str]:
        """List all subfolders in a stage version directory."""
        self._validate_stage(stage)

        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return []

        stage_path = self.get_stage_version_path(stage, resource_id, version)

        if not self.backend.exists(stage_path):
            return []

        try:
            all_items = self.backend.list_dir(stage_path)
            # Filter to only directories (subfolders)
            subfolders = []
            for item in all_items:
                item_path = f"{stage_path}/{item}"
                if self.backend.exists(item_path) and not item.endswith('.json'):  # Simple heuristic
                    subfolders.append(item)
            return sorted(subfolders)
        except Exception as e:
            logger.error(f"Error listing subfolders for {stage}/{resource_id} v{version}: {e}")
            return []

    def subfolder_exists(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, subfolder: str = None) -> bool:
        """Check if a subfolder exists in a stage version directory."""
        if not subfolder:
            return False
        return self.stage_exists(stage, resource_id, version, subfolder=subfolder)

    # ================================================================================
    #                          FULL URI GENERATION
    # ================================================================================

    def get_stage_full_path(self, stage: ProcessingStage, resource_id: str, version: Optional[str] = None, filename: Optional[str] = None, subfolder: Optional[str] = None) -> Optional[str]:
        """
        Get the full path with protocol for content in a specific processing stage, optionally in a subfolder.

        Args:
            stage: Processing stage
            resource_id: The resource identifier
            version: The version (if None, uses latest version)
            filename: The filename (if None, retrieves from version metadata for raw stage)
            subfolder: Optional subfolder within the stage

        Returns:
            Full path with protocol, or None if resource/version not found
        """
        self._validate_stage(stage)

        # Get version if not specified
        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return None

        # Get filename if not specified and we're dealing with raw stage
        if filename is None and stage == "raw":
            version_metadata = self.get_version_metadata(resource_id, version)
            if not version_metadata:
                return None
            filename = version_metadata.get("filename")
            if not filename:
                return None

        # Construct the relative path to the content file
        if filename:
            content_path = self.get_stage_file_path(stage, resource_id, version, filename, subfolder)
        else:
            if subfolder:
                content_path = self.get_stage_version_path_with_subfolder(stage, resource_id, version, subfolder)
            else:
                content_path = self.get_stage_version_path(stage, resource_id, version)

        # Generate full path based on backend type
        backend_class_name = self.backend.__class__.__name__

        if backend_class_name == "LocalFileSystemBackend":
            # For local filesystem, resolve to absolute path
            absolute_path = self.backend._resolve_path(content_path)
            return f"file://{absolute_path}"

        elif backend_class_name == "S3StorageBackend":
            # For S3, construct s3:// URI
            s3_key = self.backend._get_s3_key(content_path)
            return f"s3://{self.backend.bucket_name}/{s3_key}"

        else:
            # For other backends, return a generic URI
            return f"kb://{content_path}"

    def get_resource_full_path(self, resource_id: str, version: Optional[str] = None, filename: Optional[str] = None) -> Optional[str]:
        """Convenience method to get full path for raw content (backwards compatibility)."""
        return self.get_stage_full_path("raw", resource_id, version, filename)

    def get_resource_base_uri(self, resource_id: str) -> str:
        """Get the base URI for a resource (always points to data/raw)."""
        backend_class_name = self.backend.__class__.__name__

        if backend_class_name == "LocalFileSystemBackend":
            resource_path = self.get_resource_base_path(resource_id)
            absolute_path = self.backend._resolve_path(resource_path)
            return f"file://{absolute_path}"

        elif backend_class_name == "S3StorageBackend":
            resource_path = self.get_resource_base_path(resource_id)
            s3_key = self.backend._get_s3_key(resource_path)
            return f"s3://{self.backend.bucket_name}/{s3_key}"

        else:
            return f"kb://{self.get_resource_base_path(resource_id)}"

    # ================================================================================
    #                          UTILITIES
    # ================================================================================

    def compute_content_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content).hexdigest()

    def log_operation(self, operation: str, resource_id: str, details: Dict[str, Any]) -> None:
        """Log an operation for audit trail."""
        now = datetime.now()
        log_dir = f"log/knowledge_base/{now.year:04d}/{now.month:02d}/{now.day:02d}"
        log_file = f"{log_dir}/operations.jsonl"

        log_entry = {
            "timestamp": now.isoformat(),
            "operation": operation,
            "resource_id": resource_id,
            "details": details
        }

        # Append to log file
        if self.backend.exists(log_file):
            existing_content = self.backend.read_text(log_file)
            new_content = existing_content + "\n" + json.dumps(log_entry, ensure_ascii=False)
        else:
            new_content = json.dumps(log_entry, ensure_ascii=False)

        self.backend.write_text(log_file, new_content)

    def get_processing_status(self, resource_id: str, version: Optional[str] = None) -> Dict[ProcessingStage, bool]:
        """Get the processing status for all stages of a resource."""
        if version is None:
            version = self.get_latest_version(resource_id)
            if not version:
                return {stage: False for stage in VALID_STAGES}

        status = {}
        for stage in VALID_STAGES:
            status[stage] = self.stage_exists(stage, resource_id, version)

        return status

    def cleanup_incomplete_processing(self, resource_id: str, version: str, keep_stages: List[ProcessingStage] = None) -> None:
        """Clean up incomplete processing results, optionally keeping specified stages."""
        if keep_stages is None:
            keep_stages = ["raw"]  # Always keep raw data

        for stage in VALID_STAGES:
            if stage not in keep_stages:
                self.delete_stage_content(stage, resource_id, version)

class KnowledgeBaseCollaborativeStorage(KnowledgeBaseStorage):

    def __init__(self, backend: IStorageBackend, content_index:IContentIndexManager):
        super().__init__(backend, content_index)

        from kdcube_ai_app.storage.distributed_locks import DistributedResourceLocks
        self.locks = DistributedResourceLocks(backend)


    def get_next_version_and_lock(self,
                                       resource_id: str,
                                       versions_path: str,
                                       max_attempts: int = 10) -> Tuple[str, Optional[str]]:
        """
        Safely get next version and acquire lock.

        Returns:
            (version, lock_id) if successful
            (None, None) if failed
        """

        for attempt in range(max_attempts):
            try:
                # Clean up stale entries periodically
                if attempt % 3 == 0:
                    self.locks.cleanup_stale_locks(resource_id)

                # Get existing versions from filesystem
                existing_versions = self._scan_existing_versions(versions_path)

                # Get in-flight versions from queue
                queue_entries = self.locks.get_version_queue(resource_id)
                in_flight_versions = []
                for entry in queue_entries:
                    try:
                        in_flight_versions.append(int(entry.version))
                    except ValueError:
                        pass  # Skip non-numeric versions

                # Calculate next version
                all_versions = existing_versions + in_flight_versions
                next_version = str(max(all_versions, default=0) + 1)

                # Add to queue first (claims our version)
                if not self.locks.add_to_queue(resource_id, next_version, "add_version"):
                    logger.debug(f"Failed to add version {next_version} to queue")
                    time.sleep(0.1 * (attempt + 1))
                    continue

                # Try to acquire lock
                lock_id = self.locks.acquire_lock(resource_id, next_version, "add_version")
                if lock_id:
                    # Successfully got version and lock
                    logger.info(f"Acquired version {next_version} and lock {lock_id} for {resource_id}")
                    return next_version, lock_id
                else:
                    # Failed to get lock, remove from queue
                    self.locks.remove_from_queue(resource_id, next_version)
                    logger.debug(f"Failed to acquire lock for version {next_version}")

                # Wait before retry
                wait_time = min(0.2 * (2 ** attempt), 3.0)
                time.sleep(wait_time)

            except Exception as e:
                logger.error(f"Error in version allocation attempt {attempt + 1}: {e}")
                if attempt == max_attempts - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))

        logger.error(f"Failed to get version and lock for {resource_id} after {max_attempts} attempts")
        return None, None

    def _scan_existing_versions(self, versions_path: str) -> list[int]:
        """Scan filesystem for existing version numbers."""
        try:
            if not self.backend.exists(versions_path):
                return []

            version_dirs = self.backend.list_dir(versions_path)
            versions = []

            for dirname in version_dirs:
                try:
                    if dirname.isdigit():
                        versions.append(int(dirname))
                except ValueError:
                    pass  # Skip non-numeric directory names

            return versions

        except Exception as e:
            logger.warning(f"Error scanning versions: {e}")
            return []


    def _verify_lock_ownership(self, resource_id: str, lock_id: str) -> bool:
        """Verify we still own the lock."""
        try:
            lock_path = self.locks._get_lock_path(resource_id)

            if not self.backend.exists(lock_path):
                return False

            content = self.backend.read_text(lock_path)
            lock_info = LockInfo.from_dict(json.loads(content))

            return (lock_info.lock_id == lock_id and
                    lock_info.server_id == self.locks.server_id and
                    not lock_info.is_expired())

        except Exception as e:
            logger.warning(f"Error verifying lock ownership: {e}")
            return False

    def _should_overwrite_metadata(self, metadata_path: str, our_version: str) -> bool:
        """Check if we should overwrite existing metadata."""
        try:
            if not self.backend.exists(metadata_path):
                return True  # No existing metadata

            # Read existing metadata
            content = self.backend.read_text(metadata_path)
            existing_metadata = json.loads(content)
            existing_version = existing_metadata.get("version", "0")

            # Compare versions
            try:
                our_ver = int(our_version)
                existing_ver = int(existing_version)
                should_overwrite = our_ver > existing_ver
            except ValueError:
                # String comparison fallback
                should_overwrite = our_version > existing_version

            if not should_overwrite:
                logger.debug(f"Not overwriting: our v{our_version} <= existing v{existing_version}")

            return should_overwrite

        except Exception as e:
            logger.warning(f"Error checking existing metadata: {e}")
            return True  # Default to allowing update

    def _atomic_write_metadata(self, metadata_path: str, metadata: Dict[str, Any], version: str) -> bool:
        """Write metadata atomically."""
        try:
            # Add timestamp and version info
            metadata_with_info = metadata.copy()
            metadata_with_info["last_updated"] = datetime.now().isoformat()
            metadata_with_info["updated_by_version"] = version
            metadata_with_info["updated_by_server"] = self.locks.server_id

            # Create content
            content = json.dumps(metadata_with_info, indent=2, ensure_ascii=False)

            # Atomic write strategy
            temp_path = f"{metadata_path}.tmp.{self.locks.process_id}.{int(time.time())}"

            try:
                # Write to temp file
                self.backend.write_text(temp_path, content)

                # Atomic move to final location
                if hasattr(self.backend, '_resolve_path'):
                    # Local filesystem - atomic rename
                    import os
                    temp_file = self.backend._resolve_path(temp_path)
                    target_file = self.backend._resolve_path(metadata_path)
                    os.rename(str(temp_file), str(target_file))
                else:
                    # S3 - copy and delete (not fully atomic but best we can do)
                    final_content = self.backend.read_text(temp_path)
                    self.backend.write_text(metadata_path, final_content)
                    self.backend.delete(temp_path)

                logger.debug(f"Atomically wrote metadata for version {version}")
                return True

            except Exception as e:
                # Clean up temp file
                try:
                    self.backend.delete(temp_path)
                except:
                    pass
                logger.error(f"Error in atomic write: {e}")
                return False

        except Exception as e:
            logger.error(f"Error writing metadata: {e}")
            return False

    # Alloc version method
    def allocate_resource_version(self,
                                  resource_id: str,
                                  max_attempts: int = 10) -> Union[tuple[str, str], str]:
        """
        Safely allocate next version with distributed locking.

        Returns:
            (version, lock_id) - MUST call release_version_lock() with these
        """
        versions_path = self.get_resource_versions_path(resource_id)
        version, lock_id = self.get_next_version_and_lock(resource_id, versions_path, max_attempts)

        if not version or not lock_id:
            raise Exception(f"Failed to allocate version for {resource_id}")

        return version, lock_id

    # Release method (ALWAYS call this)
    def release_version_lock(self, resource_id: str, version: str, lock_id: str) -> bool:
        """
        Release version lock and clean up queue.

        ALWAYS call this after safe_get_next_version_and_lock, even on failure.
        """
        try:
            success = True

            # Release lock
            if lock_id:
                if not self.locks.release_lock(resource_id, lock_id):
                    logger.warning(f"Failed to release lock {lock_id}")
                    success = False

            # Remove from queue
            if version:
                if not self.locks.remove_from_queue(resource_id, version):
                    logger.warning(f"Failed to remove version {version} from queue")
                    success = False

            return success

        except Exception as e:
            logger.error(f"Error releasing version lock: {e}")
            return False

    def cleanup_stale_resources(self, resource_id: str = None) -> Dict[str, Any]:
        """Clean up stale locks and queue entries."""
        try:
            result = self.locks.cleanup_stale_locks(resource_id)
            logger.info(f"Cleanup completed: {result}")
            return result
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return {"error": str(e)}

    # Monitoring
    def get_resource_lock_status(self, resource_id: str) -> Dict[str, Any]:
        """Get current lock and queue status for a resource."""
        try:
            # Check lock status
            lock_path = self.locks._get_lock_path(resource_id)
            is_locked = False
            lock_info = None

            if self.backend.exists(lock_path):
                try:
                    content = self.backend.read_text(lock_path)
                    lock_data = json.loads(content)
                    lock_info = lock_data

                    # Check if still valid
                    from kdcube_ai_app.storage.distributed_locks import LockInfo
                    lock_obj = LockInfo.from_dict(lock_data)
                    is_locked = not lock_obj.is_expired()
                except Exception as e:
                    logger.warning(f"Error reading lock: {e}")

            # Get queue status
            queue_entries = self.locks.get_version_queue(resource_id)

            return {
                "resource_id": resource_id,
                "is_locked": is_locked,
                "lock_info": lock_info,
                "queue_entries": [entry.to_dict() for entry in queue_entries],
                "in_flight_count": len(queue_entries),
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error getting resource status: {e}")
            return {
                "error": str(e),
                "resource_id": resource_id,
                "timestamp": datetime.now().isoformat()
            }

    def update_resource_metadata(self,
                                 resource_id: str,
                                 version: str,
                                 lock_id: str,
                                 metadata: Dict[str, Any]) -> bool:
        """
        Safely update resource metadata with distributed coordination.

        Args:
            resource_id: Resource identifier
            version: Version attempting to update
            lock_id: Lock ID from safe_get_next_version_and_lock
            metadata: Metadata to write
            metadata_path: Path to resource metadata file

        Returns:
            True if updated, False if skipped
        """
        metadata_path = self.get_resource_metadata_path(resource_id)
        try:
            # Verify we still own the lock
            if not self._verify_lock_ownership(resource_id, lock_id):
                logger.warning(f"Lock verification failed for {resource_id}")
                return False

            # Check if we should update (are we the highest version?)
            if not self.locks.is_highest_version(resource_id, version):
                logger.info(f"Skipping metadata update - not highest version for {resource_id} v{version}")
                return False

            # Check existing metadata version
            if not self._should_overwrite_metadata(metadata_path, version):
                logger.info(f"Skipping metadata update - existing is newer for {resource_id} v{version}")
                return False

            # Safe to update
            success = self._atomic_write_metadata(metadata_path, metadata, version)

            if success:
                logger.info(f"Updated resource metadata for {resource_id} v{version}")

            return success

        except Exception as e:
            logger.error(f"Error in safe metadata update: {e}")
            return False
