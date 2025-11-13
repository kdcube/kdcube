# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Content-based deduplication index manager for Knowledge Base.
Provides content hash -> resource_id mapping with bloom filter optimization.
"""
import json
import time
from abc import ABCMeta, abstractmethod
from typing import Dict, Optional
import logging

from kdcube_ai_app.apps.knowledge_base.db.kb_db_connector import KnowledgeBaseConnector
from kdcube_ai_app.storage.storage import IStorageBackend

logger = logging.getLogger("KnowledgeBase.ContentIndex")

try:
    from pybloom_live import BloomFilter
    BLOOM_AVAILABLE = True
except ImportError:
    logger.warning("pybloom_live not available. Install with: pip install pybloom_live")
    BLOOM_AVAILABLE = False
    BloomFilter = None


class IContentIndexManager(metaclass=ABCMeta):
    @abstractmethod
    def check_content_exists(self, content_hash: str) -> Optional[str]:
        """
        Check if content hash exists and return the resource_id if found.

        Args:
            content_hash: SHA-256 hash of content

        Returns:
            resource_id if content exists, None otherwise
        """
        pass

    @abstractmethod
    def add_content_mapping(self, content_hash:str, resource_id:str) -> None:
        """
        Add a content hash -> resource_id mapping.

        Args:
            content_hash: SHA-256 hash of content
            resource_id: Resource identifier
        """
        pass

    @abstractmethod
    def remove_content_mapping(self, content_hash:str) -> bool:
        """
        Remove a content hash mapping.

        Args:
            content_hash: SHA-256 hash to remove

        Returns:
            True if mapping was removed, False if not found
        """
        pass

    @abstractmethod
    def get_stats(self):
        """Get index statistics."""
        pass

    def rebuild_index(self, content_mappings: Dict[str, str]) -> None:
        """
        Rebuild the entire index from scratch.

        Args:
            content_mappings: Dict of content_hash -> resource_id mappings
        """
        pass

    @abstractmethod
    def validate_index_consistency(self, resource_callback=None)-> Dict:
        """
        Validate index consistency by checking if all indexed resources still exist.

        Args:
            resource_callback: Function that takes resource_id and returns True if resource exists

        Returns:
            Dict with validation results
        """
        pass


class DBContentIndexManager(IContentIndexManager):

    def __init__(self, storage_backend: IStorageBackend, db_connector:KnowledgeBaseConnector):
        super().__init__()
        self.storage_backend = storage_backend
        self.db_connector = db_connector
        self.logger = logging.getLogger(__name__)

    def check_content_exists(self, content_hash: str) -> Optional[str]:
        self.logger.debug("Checking content hash %s", content_hash)
        return self.db_connector.content_hash_exists(content_hash)

    def add_content_mapping(self, content_hash: str, resource_id: str) -> None:
        self.logger.debug("Adding content hash %s", content_hash)
        self.db_connector.add_content_hash(resource_id, content_hash)

    def remove_content_mapping(self, content_hash: str) -> bool:
        self.logger.debug("Removing content hash %s", content_hash)
        return self.db_connector.remove_content_hash(content_hash)

    def get_stats(self):
        self.logger.debug("Getting content statistics")
        return {
            "total_entries": self.db_connector.get_content_hash_count(),
        }

    def validate_index_consistency(self, resource_callback=None) -> Dict:
        self.logger.debug("Validating index consistency")
        if not resource_callback:
            return {"status": "skipped", "reason": "no_callback_provided"}

        orphaned_hashes = []
        valid_entries = 0

        total = 0
        for content_hash, i, total in self.db_connector.list_content_hashes_generator():
            if resource_callback(content_hash.value):
                valid_entries += 1
            else:
                orphaned_hashes.append(content_hash)

        return {
            "status": "completed",
            "total_entries": total,
            "valid_entries": valid_entries,
            "orphaned_entries": len(orphaned_hashes),
            "orphaned_hashes": orphaned_hashes[:10]  # Limit for readability
        }

    def rebuild_index(self, content_mappings: Dict[str, str]):
        """
        Rebuild the entire index from scratch.

        Args:
            content_mappings: Dict of content_hash -> resource_id mappings
        """
        logger.info(f"Rebuilding content index with {len(content_mappings)} entries")

        # Clear out hashes
        self.db_connector.clear_all_content_hashes()

        content_hashes = []
        for content_hash, resource_id in content_mappings.items():
            content_hashes.append(
                {
                    "name": resource_id,
                    "value": content_hash,
                }
            )

        self.db_connector.batch_add_content_hashes(content_hashes)

        logger.info("Content index rebuild completed")


class FSContentIndexManager(IContentIndexManager):
    """
    Manages content-based deduplication using hash indexes and bloom filters.
    Works with any IStorageBackend implementation.
    """

    def __init__(self, backend: IStorageBackend, index_prefix: str = ".index"):
        """
        Initialize the content index manager.

        Args:
            backend: Storage backend to use for index persistence
            index_prefix: Directory prefix for index files
        """
        super().__init__()
        self.backend = backend
        self.index_prefix = index_prefix

        # Index paths
        self.hash_index_path = f"{index_prefix}/content_hash_index.json"
        self.bloom_index_path = f"{index_prefix}/bloom_filter.json"
        self.meta_index_path = f"{index_prefix}/index_metadata.json"

        # In-memory caches
        self._hash_index: Optional[Dict[str, str]] = None
        self._bloom_filter: Optional[BloomFilter] = None
        self._index_loaded = False

        # Bloom filter settings
        self.bloom_capacity = 100000
        self.bloom_error_rate = 0.001

        logger.info(f"ContentIndexManager initialized with backend {backend.__class__.__name__}")

    def _ensure_index_loaded(self):
        """Load index from storage if not already loaded."""
        if self._index_loaded:
            return

        try:
            self._load_hash_index()
            self._load_bloom_filter()
            self._index_loaded = True
            logger.debug("Content index loaded successfully")
        except Exception as e:
            logger.error(f"Error loading content index: {e}")
            self._initialize_empty_index()

    def _load_hash_index(self):
        """Load hash index from storage."""
        try:
            if self.backend.exists(self.hash_index_path):
                content = self.backend.read_text(self.hash_index_path)
                self._hash_index = json.loads(content)
                logger.debug(f"Loaded hash index with {len(self._hash_index)} entries")
            else:
                self._hash_index = {}
                logger.debug("Hash index file not found, starting with empty index")
        except Exception as e:
            logger.error(f"Error loading hash index: {e}")
            self._hash_index = {}

    def _load_bloom_filter(self):
        """Load bloom filter from storage."""
        if not BLOOM_AVAILABLE:
            self._bloom_filter = None
            return

        try:
            if self.backend.exists(self.bloom_index_path):
                # Load bloom filter data
                content = self.backend.read_text(self.bloom_index_path)
                bloom_data = json.loads(content)

                # Recreate bloom filter and add existing hashes
                self._bloom_filter = BloomFilter(
                    capacity=bloom_data.get("capacity", self.bloom_capacity),
                    error_rate=bloom_data.get("error_rate", self.bloom_error_rate)
                )

                # Add existing hashes to bloom filter
                for content_hash in self._hash_index.keys():
                    self._bloom_filter.add(content_hash)

                logger.debug(f"Loaded bloom filter with {len(self._hash_index)} entries")
            else:
                self._bloom_filter = BloomFilter(
                    capacity=self.bloom_capacity,
                    error_rate=self.bloom_error_rate
                )
                logger.debug("Bloom filter file not found, created new bloom filter")
        except Exception as e:
            logger.error(f"Error loading bloom filter: {e}")
            self._bloom_filter = BloomFilter(
                capacity=self.bloom_capacity,
                error_rate=self.bloom_error_rate
            ) if BLOOM_AVAILABLE else None

    def _initialize_empty_index(self):
        """Initialize empty index structures."""
        self._hash_index = {}
        self._bloom_filter = BloomFilter(
            capacity=self.bloom_capacity,
            error_rate=self.bloom_error_rate
        ) if BLOOM_AVAILABLE else None
        self._index_loaded = True
        logger.info("Initialized empty content index")

    def _save_hash_index(self):
        """Save hash index to storage atomically."""
        try:
            # Write to temporary file first
            temp_path = f"{self.hash_index_path}.tmp.{int(time.time())}"
            content = json.dumps(self._hash_index, indent=2, ensure_ascii=False)
            self.backend.write_text(temp_path, content)

            # Atomic rename (if backend supports it, otherwise just overwrite)
            try:
                # For local filesystem, this will be atomic
                if hasattr(self.backend, '_resolve_path'):
                    import os
                    temp_file = self.backend._resolve_path(temp_path)
                    target_file = self.backend._resolve_path(self.hash_index_path)
                    os.rename(str(temp_file), str(target_file))
                else:
                    # For S3 and other backends, copy and delete
                    final_content = self.backend.read_text(temp_path)
                    self.backend.write_text(self.hash_index_path, final_content)
                    self.backend.delete(temp_path)
            except Exception:
                # Fallback: direct write
                self.backend.write_text(self.hash_index_path, content)
                try:
                    self.backend.delete(temp_path)
                except:
                    pass

            logger.debug(f"Saved hash index with {len(self._hash_index)} entries")
        except Exception as e:
            logger.error(f"Error saving hash index: {e}")
            raise

    def _save_bloom_filter(self):
        """Save bloom filter metadata to storage."""
        if not BLOOM_AVAILABLE or not self._bloom_filter:
            return

        try:
            bloom_metadata = {
                "capacity": self.bloom_capacity,
                "error_rate": self.bloom_error_rate,
                "entry_count": len(self._hash_index),
                "last_updated": time.time()
            }

            content = json.dumps(bloom_metadata, indent=2, ensure_ascii=False)
            self.backend.write_text(self.bloom_index_path, content)
            logger.debug("Saved bloom filter metadata")
        except Exception as e:
            logger.error(f"Error saving bloom filter: {e}")

    def _save_metadata(self):
        """Save index metadata."""
        try:
            metadata = {
                "version": "1.0",
                "last_updated": time.time(),
                "total_entries": len(self._hash_index) if self._hash_index else 0,
                "bloom_filter_enabled": BLOOM_AVAILABLE and self._bloom_filter is not None
            }

            content = json.dumps(metadata, indent=2, ensure_ascii=False)
            self.backend.write_text(self.meta_index_path, content)
        except Exception as e:
            logger.error(f"Error saving index metadata: {e}")

    def check_content_exists(self, content_hash: str) -> Optional[str]:
        """
        Check if content hash exists and return the resource_id if found.

        Args:
            content_hash: SHA-256 hash of content

        Returns:
            resource_id if content exists, None otherwise
        """
        self._ensure_index_loaded()

        # Fast negative lookup with bloom filter
        if BLOOM_AVAILABLE and self._bloom_filter:
            if content_hash not in self._bloom_filter:
                # Definitely not present
                return None

        # Check hash index
        return self._hash_index.get(content_hash)

    def add_content_mapping(self, content_hash: str, resource_id: str):
        """
        Add a content hash -> resource_id mapping.

        Args:
            content_hash: SHA-256 hash of content
            resource_id: Resource identifier
        """
        self._ensure_index_loaded()

        # Add to hash index
        self._hash_index[content_hash] = resource_id

        # Add to bloom filter
        if BLOOM_AVAILABLE and self._bloom_filter:
            self._bloom_filter.add(content_hash)

        # Save to persistent storage
        try:
            self._save_hash_index()
            self._save_bloom_filter()
            self._save_metadata()
            logger.debug(f"Added content mapping: {content_hash[:16]}... -> {resource_id}")
        except Exception as e:
            logger.error(f"Error saving content mapping: {e}")
            # Remove from memory if save failed
            self._hash_index.pop(content_hash, None)
            raise

    def remove_content_mapping(self, content_hash: str) -> bool:
        """
        Remove a content hash mapping.

        Args:
            content_hash: SHA-256 hash to remove

        Returns:
            True if mapping was removed, False if not found
        """
        self._ensure_index_loaded()

        if content_hash not in self._hash_index:
            return False

        resource_id = self._hash_index.pop(content_hash)

        # Note: Cannot remove from bloom filter efficiently
        # This is a known limitation of bloom filters

        try:
            self._save_hash_index()
            self._save_metadata()
            logger.debug(f"Removed content mapping: {content_hash[:16]}... -> {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Error removing content mapping: {e}")
            # Restore mapping if save failed
            self._hash_index[content_hash] = resource_id
            raise

    def rebuild_index(self, content_mappings: Dict[str, str]):
        """
        Rebuild the entire index from scratch.

        Args:
            content_mappings: Dict of content_hash -> resource_id mappings
        """
        logger.info(f"Rebuilding content index with {len(content_mappings)} entries")

        # Rebuild in-memory structures
        self._hash_index = content_mappings.copy()

        if BLOOM_AVAILABLE:
            self._bloom_filter = BloomFilter(
                capacity=max(len(content_mappings) * 2, self.bloom_capacity),
                error_rate=self.bloom_error_rate
            )
            for content_hash in content_mappings.keys():
                self._bloom_filter.add(content_hash)

        self._index_loaded = True

        # Save to storage
        self._save_hash_index()
        self._save_bloom_filter()
        self._save_metadata()

        logger.info("Content index rebuild completed")

    def get_stats(self) -> Dict:
        """Get index statistics."""
        self._ensure_index_loaded()

        return {
            "total_entries": len(self._hash_index) if self._hash_index else 0,
            "bloom_filter_enabled": BLOOM_AVAILABLE and self._bloom_filter is not None,
            "index_loaded": self._index_loaded,
            "bloom_capacity": self.bloom_capacity if BLOOM_AVAILABLE else None,
            "bloom_error_rate": self.bloom_error_rate if BLOOM_AVAILABLE else None
        }

    def validate_index_consistency(self, resource_callback=None) -> Dict:
        """
        Validate index consistency by checking if all indexed resources still exist.

        Args:
            resource_callback: Function that takes resource_id and returns True if resource exists

        Returns:
            Dict with validation results
        """
        self._ensure_index_loaded()

        if not resource_callback:
            return {"status": "skipped", "reason": "no_callback_provided"}

        orphaned_hashes = []
        valid_entries = 0

        for content_hash, resource_id in self._hash_index.items():
            if resource_callback(resource_id):
                valid_entries += 1
            else:
                orphaned_hashes.append(content_hash)

        return {
            "status": "completed",
            "total_entries": len(self._hash_index),
            "valid_entries": valid_entries,
            "orphaned_entries": len(orphaned_hashes),
            "orphaned_hashes": orphaned_hashes[:10]  # Limit for readability
        }