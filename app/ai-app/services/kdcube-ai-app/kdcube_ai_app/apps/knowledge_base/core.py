# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
core.py
Enhanced Knowledge Base core functionality with search_and_navigate support.
"""
import logging
import mimetypes
import os
import traceback
from datetime import datetime
from typing import Dict, Any, Optional, List, Union
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType
from kdcube_ai_app.apps.knowledge_base.db.kb_db_connector import create_kb_connector
from kdcube_ai_app.apps.knowledge_base.search import SimpleKnowledgeBaseSearch
from kdcube_ai_app.apps.knowledge_base.db.data_models import NavigationSearchResult
from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord
from kdcube_ai_app.storage.storage import IStorageBackend, create_storage_backend
from kdcube_ai_app.apps.knowledge_base.storage import KnowledgeBaseCollaborativeStorage

from kdcube_ai_app.tools.datasource import (URLDataElement, FileDataElement,
    RawTextDataElement, DataElement, create_data_element, IngestModifiers,
                                            canonicalize_url, source_name_from_url)
from kdcube_ai_app.tools.content_type import is_text_mime_type
from kdcube_ai_app.tools.parser import MarkdownParser
from kdcube_ai_app.apps.knowledge_base.modules.base import ModuleFactory
from kdcube_ai_app.apps.knowledge_base.index.content_index import FSContentIndexManager, DBContentIndexManager

logger = logging.getLogger("KnowledgeBase.Core")

rm_excluded_fields = {'content_hash', 'ef_uri', 'extraction_info', 'status'}
class ResourceMetadata(BaseModel):
    """Metadata for a knowledge base resource."""
    id: str = Field(..., description="Resource ID")
    source_id: str = Field(..., description="Original source identifier")
    source_type: str = Field(..., description="Type of source (file, url, raw_text)")
    uri: str = Field(..., description="Original URI of the source")
    filename: str = Field(..., description="Original filename")
    name: str = Field(..., description="Display name")
    mime: Optional[str] = Field(None, description="MIME type")
    encoding: Optional[str] = Field("utf-8", description="Text encoding")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    ef_uri: Optional[str] = Field(None, description="Internal storage URI")
    rn: Optional[str] = Field(None, description="Resource Name")
    version: str = Field(..., description="Current version")
    size_bytes: Optional[int] = Field(None, description="Size of the resource in bytes")
    content_hash: Optional[str] = Field(None, description="SHA-256 hash of content")
    extraction_info: Optional[Dict[str, Any]] = Field(
        None, description="Extraction metadata if available"
    )
    description: Optional[str] = Field(None, description="Description")
    title: Optional[str] = Field(None, description="Title")
    summary: Optional[str] = Field(None, description="Summary")
    status: Optional[str] = Field(None, description="resource status. Usually for transmission needs.")
    provider: Optional[str] = Field(None, description="Optional provider info")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Extra info")
    expiration: Optional[str] = Field(None, description="Expiration timestamp")
    def light(self):
        return self.model_dump(exclude=rm_excluded_fields)


def _extract_filesystem_path(uri: str) -> str:
    """
    Extract filesystem path from various URI formats.

    Args:
        uri: URI like 'file:///path/to/file', 's3://bucket/key', or '/path/to/file'

    Returns:
        Appropriate path string for the data element
    """
    if not uri:
        return ""

    parsed = urlparse(uri)

    if parsed.scheme == 'file':
        # file:///path/to/file -> /path/to/file
        return parsed.path

    elif parsed.scheme == 's3':
        # s3://bucket/path/to/file -> keep as-is for S3 operations
        return uri

    elif parsed.scheme == 'kb':
        # kb://data/raw/resource/versions/1/file.txt -> keep as-is for internal operations
        return uri

    elif parsed.scheme == '':
        # No scheme, assume it's already a filesystem path
        return uri

    else:
        # Unknown scheme, return as-is and let the caller handle it
        logger.warning(f"Unknown URI scheme in {uri}, returning as-is")
        return uri

class KnowledgeBase:
    """Main Knowledge Base class for managing resources and processing."""

    def __init__(self,
                 tenant: str,
                 project: str,
                 storage_backend: Union[str, IStorageBackend],
                 embedding_model: ModelRecord,
                 processing_mode: str="retrieval_only",
                 **storage_kwargs):
        """
        Initialize Knowledge Base.

        Args:
            storage_backend: Either a storage URI (like 'file:///path' or 's3://bucket/prefix')
                           or an IStorageBackend instance
            **storage_kwargs: Additional arguments passed to storage backend creation
        """
        self.project = project
        self.tenant = tenant
        if isinstance(storage_backend, str):
            self.backend = create_storage_backend(storage_backend, **storage_kwargs)
        else:
            self.backend = storage_backend

        self.db_connector = create_kb_connector(
            tenant=tenant,
            schema_name=project.replace("-", "_"),
            project_name=project,
            embedding_model=embedding_model
        )
        # self.content_index = InMemoryContentIndexManager(self.backend)
        self.content_index = DBContentIndexManager(self.backend, self.db_connector)
        logger.info("Content deduplication index initialized")

        self.storage = KnowledgeBaseCollaborativeStorage(self.backend, self.content_index)
        self.markdown_parser = MarkdownParser()

        logger.info("Knowledge Base initialized")

        # Initialize processing pipeline with all modules
        self.pipeline = ModuleFactory.create_default_pipeline(self.storage,
                                                              self.project,
                                                              self.tenant,
                                                              processing_mode=processing_mode,
                                                              db_connector=self.db_connector)

        logger.info(f"Knowledge Base '{project}' initialized with modular architecture")
        self._search_component = None



    @property
    def search_component(self) -> SimpleKnowledgeBaseSearch:
        """Get the multi-representation search component."""
        if self._search_component is None:
            self._search_component = SimpleKnowledgeBaseSearch(
                self
            )
        return self._search_component

    def _find_ready_version(self, resource_id: str,
                            stages: Optional[list[str]],
                            require_all: bool) -> Optional[str]:
        """Return latest version that satisfies readiness criteria, or None."""
        if not self.storage.resource_exists(resource_id):
            return None

        versions = self.storage.list_versions(resource_id) or []
        # latest first, numeric-safe
        versions_sorted = sorted(versions, key=lambda v: int(v) if str(v).isdigit() else v, reverse=True)

        for v in versions_sorted:
            status = self.pipeline.get_processing_status(resource_id, v) or {}
            if not stages:  # no stages specified: any existing version counts
                return v
            flags = [bool(status.get(s)) for s in stages]
            if (all(flags) if require_all else any(flags)):
                return v
        return None

    def add_resource(self, data_element: Union[Dict[str, Any], DataElement]) -> ResourceMetadata:
        if isinstance(data_element, dict):
            data_element = create_data_element(**data_element)

        ingest = data_element.ingest or IngestModifiers()  # defaults = legacy behavior

        # --- Build source_type and source_id (with optional URL canonicalization) ---
        source_type = data_element.type
        provider = getattr(data_element, "provider", None)

        if source_type == "url":

            url = data_element.url
            url_for_id = canonicalize_url(url) if ingest.canonicalize_url else url
            source_id = source_name_from_url(url_for_id) if ingest.canonicalize_url else \
                        (urlparse(url).netloc + urlparse(url).path)
        elif source_type == "file":
            source_id = data_element.filename
        elif source_type == "raw_text":
            source_id = data_element.name
        else:
            source_id = str(data_element)

        # Prefer provider-aware ID when available
        resource_id = self.storage.generate_resource_id(source_type, source_id, provider=provider)

        policy = ingest.version_policy
        stages = ingest.ready_stages or []
        require_all = ingest.require_all_ready

        if policy in ("probe_only", "reuse_ready"):
            ready_version = self._find_ready_version(resource_id, stages, require_all)
            if ready_version:
                md = self.storage.get_version_metadata(resource_id, ready_version)
                if md:
                    md["status"] = "probe_ready" if policy == "probe_only" else "reused_ready"
                    return ResourceMetadata(**md)
            if policy == "probe_only":
                # Explicitly return None to signal "no ready version"
                return None

        if policy == "overwrite_latest" and self.storage.resource_exists(resource_id):
            latest_v = self.storage.get_latest_version(resource_id)
            if latest_v:
                self.delete_resource_version(resource_id, latest_v)

        # --- Resolve primary content bytes (element vs fetch) ---
        content_type_detected = None
        actual_filename = None

        def _fetch_if_needed():
            from kdcube_ai_app.tools.content_type import fetch_url_with_content_type
            return fetch_url_with_content_type(data_element.url)

        content_bytes = None

        if ingest.content_source == "element":
            if data_element.content is None:
                raise ValueError("ingest.content_source='element' but element.content is None")
            content_bytes = data_element.content.encode("utf-8") if isinstance(data_element.content, str) else data_element.content

        elif ingest.content_source == "fetch":
            if source_type != "url":
                raise ValueError("ingest.content_source='fetch' is only valid for URL elements")
            content_bytes, content_type_detected, actual_filename = _fetch_if_needed()

        else:  # "auto"
            if data_element.content is not None:
                content_bytes = data_element.content.encode("utf-8") if isinstance(data_element.content, str) else data_element.content
            elif source_type == "url":
                content_bytes, content_type_detected, actual_filename = _fetch_if_needed()

        if not content_bytes:
            raise ValueError(f"No content loaded from source: {data_element}")

        # --- MIME selection (ingest override > element.mime > detected > guess/fallback) ---
        mime = ingest.primary_mime_override or data_element.mime or content_type_detected or \
               ( "text/html" if source_type == "url" else "application/octet-stream" )

        # --- Dedupe & hash policy ---
        # Defaults: dedupe='content', compute_hash=True (legacy)
        compute_hash = (ingest.dedupe == "content") and ingest.compute_hash
        content_hash = self.storage.compute_content_hash(content_bytes) if compute_hash else None

        if ingest.dedupe == "content" and content_hash:
            existing = self.content_index.check_content_exists(content_hash)
            if existing:
                logger.info(f"Content already exists in resource {existing}, returning existing metadata")
                existing_md = self.storage.get_resource_metadata(existing)
                if existing_md:
                    existing_md["status"] = "duplicate"
                    return ResourceMetadata(**existing_md)

        if ingest.dedupe == "resource":
            existing_metadata = self.storage.get_resource_metadata(resource_id)
            if existing_metadata:
                logger.info(f"Resource {resource_id} exists and dedupe=resource -> returning existing metadata")
                existing_metadata["status"] = "unchanged"
                if data_element.title:
                    existing_metadata["title"] = data_element.title
                if data_element.metadata:
                    existing_metadata["metadata"] = data_element.metadata
                return ResourceMetadata(**existing_metadata)

        # --- Version/lock ---
        version, lock_id = self.storage.allocate_resource_version(resource_id)
        if not version or not lock_id:
            raise Exception("Failed to assign version safely")

        try:
            # --- Filename policy ---
            from kdcube_ai_app.tools.datasource import deterministic_url_filename, ext_for_mime
            if ingest.filename_strategy == "provided" and ingest.provided_filename:
                filename = ingest.provided_filename
            elif source_type == "url":
                # deterministic per-URL (host/path + short hash + proper ext)
                filename = deterministic_url_filename(
                    data_element.url if not ingest.canonicalize_url else canonicalize_url(data_element.url),
                    mime,
                    default_ext=".html"
                )
            elif source_type == "file":
                filename = getattr(data_element, "filename", None) or f"{resource_id}{ext_for_mime(mime)}"
            else:  # raw_text or other
                filename = f"{resource_id}{ext_for_mime(mime, default='.txt')}"

            # --- Save content ---
            content_path = self.storage.save_version_content(resource_id, version, filename, content_bytes)

            # --- Build version metadata (note: may omit content_hash) ---
            uri = (
                data_element.url if source_type == "url"
                else (f"file://{data_element.path}" if source_type == "file"
                      else f"raw_text://{getattr(data_element,'name', 'raw_text')}")
            )
            name = (
                (actual_filename or urlparse(data_element.url).netloc) if source_type == "url"
                else (data_element.filename if source_type == "file"
                      else getattr(data_element, "name", resource_id))
            )

            resource_metadata = ResourceMetadata(
                id=resource_id,
                source_id=source_id,
                source_type=source_type,
                uri=uri,
                name=name,
                mime=mime,
                version=version,
                content_hash=content_hash,             # may be None under your policy
                filename=filename,
                rn=f"ef:{self.tenant}:{self.project}:knowledge_base:raw:{resource_id}:{version}",
                ef_uri=self.storage.get_resource_full_path(resource_id=resource_id, version=version, filename=filename),
                size_bytes=len(content_bytes),
                provider=provider,
                title=data_element.title,
                metadata=data_element.metadata
            )

            try:
                self.storage.save_version_metadata(resource_id, version, resource_metadata.model_dump())
            except Exception as e:
                logger.error(f"Failed to save version metadata: {e}")
                # Clean up the content we saved
                try:
                    self.backend.delete(content_path)
                except:
                    pass
                raise

            # --- Update latest resource metadata ---
            metadata_updated = self.storage.update_resource_metadata(
                resource_id=resource_id, version=version, lock_id=lock_id, metadata=resource_metadata.dict()
            )
            if metadata_updated:
                logger.info(f"Updated resource metadata for {resource_id} v{version}")
            else:
                logger.info(f"Skipped metadata update for {resource_id} v{version} - higher version in flight")

            # --- Map content hash if we actually computed one ---
            if content_hash:
                try:
                    success = self.storage.index_resource_content(content_hash, resource_id)
                    if not success:
                        logger.warning(f"Failed to add content index mapping for {resource_id}")
                except Exception as e:
                    logger.error(f"Error adding content index mapping: {e}")
                    # Don't fail the entire operation for this

            # --- Log + return ---
            self.storage.log_operation("add_resource", resource_id, {
                "source_type": source_type,
                "version": version,
                "content_hash": content_hash,
                "size_bytes": len(content_bytes),
                "mime_type": mime,
                "dedupe_policy": ingest.dedupe,
            })
            return resource_metadata
        except Exception as e:
            logger.error(f"Failed to get safe version for {resource_id}: {e}")
            raise Exception("Failed to assign version safely")

        finally:
            # CRITICAL: Always release lock, even on failure
            if version and lock_id:
                self.storage.release_version_lock(resource_id, version, lock_id)

    def get_resource(self, resource_id: str) -> Optional[ResourceMetadata]:
        """Get resource metadata by ID."""
        metadata_dict = self.storage.get_resource_metadata(resource_id)
        if not metadata_dict:
            return None
        return ResourceMetadata(**metadata_dict)

    def get_resource_content(self,
                             resource_id: str, version: Optional[str] = None,
                             as_text: bool = True) -> Optional[Union[str, bytes]]:
        """Get resource content."""
        return self.storage.get_stage_content("raw", resource_id, version, as_text=as_text)

    def get_resource_text(self, resource_id: str, version: Optional[str] = None,
                          encoding: str = 'utf-8') -> Optional[str]:
        """Get resource content as text."""
        content = self.storage.get_stage_content("raw", resource_id, version)
        if content is None:
            return None
        return content.decode(encoding)

    def list_resources(self) -> List[ResourceMetadata]:
        """List all resources in the knowledge base."""
        resources = []
        raw_data_path = "data/raw"

        if not self.backend.exists(raw_data_path):
            return resources

        for item in self.backend.list_dir(raw_data_path):
            metadata_dict = self.storage.get_resource_metadata(item)
            if metadata_dict:
                resources.append(ResourceMetadata(**metadata_dict))

        return resources

    def list_resource_versions(self, resource_id: str) -> List[ResourceMetadata]:
        """List all versions of a resource."""
        versions = []
        version_ids = self.storage.list_versions(resource_id)

        for version_id in version_ids:
            version_metadata = self.storage.get_version_metadata(resource_id, version_id)
            if version_metadata:
                versions.append(ResourceMetadata(**version_metadata))

        return versions

    def delete_resource(self, resource_id: str) -> bool:
        """Delete a resource and all its versions."""
        try:
            # Get all versions to clean up content index
            versions = self.list_resource_versions(resource_id)
            content_hashes_to_remove = []

            for version_metadata in versions:
                content_hash = version_metadata.content_hash
                if content_hash:
                    # Check if this content hash is used by other resources
                    existing_resource = self.content_index.check_content_exists(content_hash)
                    if existing_resource == resource_id:
                        content_hashes_to_remove.append(content_hash)

            # Delete the resource files
            resource_path = f"data/raw/{resource_id}"
            self.backend.delete(resource_path)

            # Clean up content index
            for content_hash in content_hashes_to_remove:
                try:
                    self.content_index.remove_content_mapping(content_hash)
                except Exception as e:
                    logger.warning(f"Failed to remove content hash {content_hash} from index: {e}")

            # Log operation
            self.storage.log_operation("delete_resource", resource_id, {})

            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting resource {resource_id}: {e}")
            return False

    def rebuild_content_index(self) -> Dict:
        """Rebuild the content index by scanning all resources."""
        logger.info("Starting content index rebuild")

        content_mappings = {}
        resources_scanned = 0
        errors = 0

        try:
            # Scan all resources
            for resource in self.list_resources():
                try:
                    # For each resource, get the latest version's content hash
                    latest_metadata = self.storage.get_resource_metadata(resource.id)
                    if latest_metadata and latest_metadata.get("content_hash"):
                        content_hash = latest_metadata["content_hash"]
                        content_mappings[content_hash] = resource.id
                        resources_scanned += 1
                except Exception as e:
                    logger.error(f"Error processing resource {resource.id} during index rebuild: {e}")
                    errors += 1

            # Rebuild the index
            self.content_index.rebuild_index(content_mappings)

            result = {
                "status": "success",
                "resources_scanned": resources_scanned,
                "entries_created": len(content_mappings),
                "errors": errors
            }

            logger.info(f"Content index rebuild completed: {result}")
            return result

        except Exception as e:
            logger.error(f"Content index rebuild failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "resources_scanned": resources_scanned,
                "errors": errors
            }

    def get_content_index_stats(self) -> Dict:
        """Get content index statistics."""
        index_stats = self.content_index.get_stats()

        # Add some additional stats
        try:
            total_resources = len(self.list_resources())
            index_stats["total_resources"] = total_resources
            index_stats["deduplication_ratio"] = (
                (total_resources - index_stats["total_entries"]) / total_resources
                if total_resources > 0 else 0
            )
        except Exception as e:
            logger.warning(f"Error calculating additional index stats: {e}")

        return index_stats

    def validate_content_index(self) -> Dict:
        """Validate content index consistency."""
        def resource_exists(resource_id: str) -> bool:
            return self.storage.resource_exists(resource_id)

        return self.content_index.validate_index_consistency(resource_exists)

    def find_duplicate_content(self) -> Dict[str, List[str]]:
        """Find resources with duplicate content."""
        # This would require scanning all content hashes
        # Implementation depends on whether you want this feature
        logger.warning("find_duplicate_content not implemented - would require full content scan")
        return {}

    def delete_resource_version(self, resource_id: str, version: str) -> bool:
        """Delete a specific version of a resource."""
        try:
            version_path = self.storage.get_resource_version_path(resource_id, version)
            self.backend.delete(version_path)

            # Update resource metadata if this was the latest version
            versions = self.storage.list_versions(resource_id)
            if version in versions:
                versions.remove(version)

            if versions:
                # Update to latest remaining version
                latest_version = max(versions, key=lambda v: int(v) if v.isdigit() else v)
                metadata = self.storage.get_resource_metadata(resource_id)
                if metadata:
                    metadata["version"] = latest_version
                    self.storage.save_resource_metadata(resource_id, metadata)
            else:
                # No versions left, delete entire resource
                return self.delete_resource(resource_id)

            # Log operation
            self.storage.log_operation("delete_version", resource_id, {"version": version})

            logger.info(f"Deleted resource {resource_id} version {version}")
            return True
        except Exception as e:
            logger.error(f"Error deleting resource {resource_id} version {version}: {e}")
            return False

    def to_data_element(self, resource_id: str, version: Optional[str] = None) -> Optional[DataElement]:
        """
        Reconstruct the original data element from stored resource data.

        Args:
            resource_id: The resource identifier
            version: The version (if None, uses latest version)

        Returns:
            DataElement instance or None if resource not found
        """

        # Get last version if not specified
        if version is None:
            # Get resource metadata
            resource_metadata = self.get_resource(resource_id)
            if not resource_metadata:
                return None
            version = resource_metadata.version
        else:
            # Get version metadata and content
            resource_metadata = self.storage.get_version_metadata(resource_id, version)
            if not resource_metadata:
                return None
        resource_metadata = ResourceMetadata(**resource_metadata)

        # Reconstruct based on source type
        if_text_mime = is_text_mime_type(resource_metadata.mime)
        file_path = _extract_filesystem_path(resource_metadata.ef_uri)
        if resource_metadata.source_type == "url":
            # For URLs, we might want to include the content if available
            content_text = self.get_resource_content(resource_id, version, as_text=if_text_mime)
            if content_text is None:
                return None
            return URLDataElement(
                url=resource_metadata.uri,
                parser_type="simple",  # Default, original parser_type not stored
                content=content_text,
                mime=resource_metadata.mime or "text/html",
                path=file_path
            )

        elif resource_metadata.source_type == "file":
            # Get content for file elements

            content_bytes = self.get_resource_content(resource_id, version, as_text=if_text_mime)
            if content_bytes is None:
                return None
            return FileDataElement(
                mime=resource_metadata.mime or "application/octet-stream",
                filename=resource_metadata.filename,
                path=file_path,
                metadata=getattr(resource_metadata, "metadata", {}),
                content=content_bytes
            )

        elif resource_metadata.source_type == "raw_text":
            content_text = self.get_resource_content(resource_id, version, as_text=True)
            if content_text is None:
                return None
            return RawTextDataElement(
                text=content_text,
                name=resource_metadata.name,
                content=content_text,
                mime=resource_metadata.mime or "text/plain",
                path=file_path
            )
        else:
            # Unknown source type
            logger.warning(f"Unknown source type: {resource_metadata.source_type}")
            return None

    # ================================================================================
    #                          MODULAR PROCESSING API
    # ================================================================================

    async def process_resource(self,
                               resource_id: str,
                               version: Optional[str] = None,
                               stages: Optional[List[str]] = None,
                               force_reprocess: bool = False,
                               **kwargs) -> Dict[str, Any]:
        """Process a resource through the modular pipeline."""
        logger.info(f"Starting modular processing of resource {resource_id}")

        # Get version if not specified
        if version is None:
            version = self.storage.get_latest_version(resource_id)
            if not version:
                raise ValueError(f"Resource {resource_id} not found")

        # Reconstruct data element for modules that need it
        data_element = self.to_data_element(resource_id, version)
        if not data_element:
            raise ValueError(f"Cannot reconstruct data element for {resource_id}")

        # Process through pipeline with dependencies
        try:
            results = await self.pipeline.process_resource(
                resource_id,
                version,
                stages=stages,
                force_reprocess=force_reprocess,
                data_element=data_element,
                data_source=data_element.to_data_source(),
                kb=self,  # Pass self to modules that need it (like search_indexing)
                **kwargs
            )

            logger.info(f"Successfully processed resource {resource_id} v{version}")
            return results

        except Exception as e:
            logger.error(f"Error processing resource {resource_id} v{version}: {e}")
            logger.error(traceback.format_exc())

            # Clean up incomplete processing
            # self.pipeline.cleanup_incomplete_processing(resource_id, version, keep_stages=["raw", "extraction", "segmentation", "metadata"])

            self.storage.log_operation("process_resource_error", resource_id, {
                "version": version,
                "error": str(e),
                "force_reprocess": force_reprocess
            })

            raise

    def get_processing_status(self, resource_id: str, version: Optional[str] = None) -> Dict[str, bool]:
        """Get processing status for all stages."""
        if version is None:
            version = self.storage.get_latest_version(resource_id)
            if not version:
                return {}

        return self.pipeline.get_processing_status(resource_id, version)

    # ================================================================================
    #                          MODULE-SPECIFIC ACCESS METHODS
    # ================================================================================

    def get_extraction_module(self):
        """Get the extraction module for direct access."""
        return self.pipeline.get_module("extraction")

    def get_segmentation_module(self):
        """Get the segmentation module for direct access."""
        return self.pipeline.get_module("segmentation")

    def get_metadata_module(self):
        """Get the metadata module for direct access."""
        return self.pipeline.get_module("metadata")

    def get_summarization_module(self):
        """Get the summarization module for direct access."""
        return self.pipeline.get_module("summarization")

    def get_embedding_module(self):
        """Get the embedding module for direct access."""
        return self.pipeline.get_module("embedding")

    def get_search_indexing_module(self):
        """Get the search indexing module for direct access."""
        return self.pipeline.get_module("search_indexing")

    # ================================================================================
    #                          CONVENIENCE METHODS FOR COMMON OPERATIONS
    # ================================================================================

    async def extract_only(self, resource_id: str, version: Optional[str] = None, force_reprocess: bool = False) -> Dict[str, Any]:
        """Run only the extraction stage."""
        return await self.process_resource(resource_id, version, stages=["extraction"], force_reprocess=force_reprocess)

    # Access extraction assets
    def get_extraction_asset(self, resource_id: str, version: str, asset_filename: str) -> Optional[bytes]:
        """Get an extraction asset."""
        extraction_module = self.get_extraction_module()
        return extraction_module.get_asset(resource_id, version, asset_filename) if extraction_module else None

    def list_extraction_assets(self, resource_id: str, version: Optional[str] = None) -> Dict[str, List[str]]:
        """List extraction assets."""
        extraction_module = self.get_extraction_module()
        return extraction_module.list_assets(resource_id, version) if extraction_module else {}

    # Access segments
    def get_segments(self, resource_id: str,
                     version: Optional[str] = None,
                     segment_type: SegmentType = SegmentType.CONTINUOUS) -> Optional[List[Dict[str, Any]]]:
        """Get segmentation results."""
        segmentation_module = self.get_segmentation_module()
        return segmentation_module.get_segments(resource_id, version, segment_type) if segmentation_module else None


    def hybrid_search(self, query: str, resource_id: str, version: Optional[str] = None, top_k: int = 5) -> List[NavigationSearchResult]:
        """Search using discovery segments, return context segments with source info."""
        return self.search_component.search(
            query=query,
            resource_id=resource_id,
            top_k=top_k
        )

    def get_enhanced_source_data_from_search_result(self, search_result) -> Optional[Dict[str, Any]]:
        """
        Enhanced version that extracts source data including base segment information.

        Args:
            search_result: SearchResult object from search_for_context()

        Returns:
            Dict containing enhanced source data with base segment tracking
        """
        try:
            # Extract metadata from search result
            resource_id = search_result.search_metadata["resource_id"]
            version = search_result.search_metadata["version"]
            source_info = search_result.source_info

            # Get raw data (unchanged)
            raw_data = self.get_resource_content(resource_id, version, as_text=False)

            # Get extraction markdown (unchanged)
            extraction_index = source_info.get("extraction_index", 0)
            extraction_content_file = f"extraction_{extraction_index}.md"

            extraction_module = self.get_extraction_module()
            extraction_markdown = extraction_module.get_asset(
                resource_id, version, extraction_content_file
            )

            if extraction_markdown:
                extraction_markdown = extraction_markdown.decode('utf-8')

            # NEW: Get base segment information from the reworked system
            segmentation_module = self.get_segmentation_module()

            # Extract compound segment metadata
            compound_segment_metadata = search_result.segment.get("metadata", {})
            base_segment_guids = compound_segment_metadata.get("base_segment_guids", [])

            # Get base segments for detailed tracking
            base_segments = segmentation_module.get_base_segments(resource_id, version)
            base_lookup = {seg.guid: seg for seg in base_segments}

            # Find the specific base segments referenced by this compound segment
            referenced_base_segments = []
            for guid in base_segment_guids:
                if guid in base_lookup:
                    base_seg = base_lookup[guid]
                    referenced_base_segments.append({
                        "guid": base_seg.guid,
                        "heading": base_seg.heading,
                        "subheading": base_seg.subheading,
                        "text_preview": base_seg.text[:100] + ("..." if len(base_seg.text) > 100 else ""),
                        "line_range": {
                            "start": base_seg.start_line_num,
                            "end": base_seg.end_line_num
                        },
                        "position_range": {
                            "start": base_seg.start_position,
                            "end": base_seg.end_position
                        },
                        "rn": base_seg.rn,
                        "extracted_data_rns": base_seg.extracted_data_rns
                    })

            # Enhanced navigation info with base segment tracking
            navigation_info = {
                "resource_id": resource_id,
                "version": version,
                "extraction_index": extraction_index,
                "extraction_content_file": extraction_content_file,
                "source_location": source_info,
                "heading_path": " > ".join(source_info.get("heading_path", [])),
                "citation_text": source_info.get("citation_text", ""),
                "markdown_excerpt": source_info.get("markdown_excerpt", ""),
                # NEW: Enhanced with base segment information
                "compound_segment_id": search_result.segment.get("segment_id"),
                "segment_type": compound_segment_metadata.get("segment_type", "unknown"),
                "base_segment_count": len(base_segment_guids),
                "base_segment_guids": base_segment_guids
            }

            # NEW: Base segment specific information
            base_segment_info = {
                "referenced_base_segments": referenced_base_segments,
                "total_base_segments": len(referenced_base_segments),
                "can_backtrack_to_source": len(referenced_base_segments) > 0,
                "precise_line_tracking": any(
                    seg["line_range"]["start"] > 0 for seg in referenced_base_segments
                ),
                "extracted_assets_available": any(
                    seg["extracted_data_rns"] for seg in referenced_base_segments
                )
            }

            # NEW: Compound segment information
            compound_segment_info = {
                "segment_id": search_result.segment.get("segment_id"),
                "is_compound": compound_segment_metadata.get("constructed_on_the_fly", False),
                "base_segment_count": compound_segment_metadata.get("base_segment_count", 0),
                "segment_type": compound_segment_metadata.get("segment_type", "unknown"),
                "created_from_base_guids": base_segment_guids,
                "extracted_data_rns": compound_segment_metadata.get("extracted_data_rns", [])
            }

            # NEW: Enhanced backtrack capability information
            backtrack_capability = {
                "can_navigate_to_original": len(referenced_base_segments) > 0,
                "precise_location_available": any(
                    seg["line_range"]["start"] > 0 for seg in referenced_base_segments
                ),
                "multiple_source_segments": len(referenced_base_segments) > 1,
                "extraction_assets_linked": len(compound_segment_info["extracted_data_rns"]) > 0,
                "navigation_methods": [
                    "line_number_jump" if base_segment_info["precise_line_tracking"] else None,
                    "guid_based_lookup",
                    "rn_based_access"
                ]
            }

            # Highlight extraction markdown with enhanced precision
            highlighted_markdown = self.highlight_extraction_markdown_enhanced(
                extraction_markdown, navigation_info, referenced_base_segments
            )

            return {
                "raw_data": raw_data,
                "extraction_markdown": extraction_markdown,
                "highlighted_markdown": highlighted_markdown,
                "navigation_info": navigation_info,
                "search_context": {
                    "query": search_result.query,
                    "relevance_score": search_result.relevance_score,
                    "text_result": search_result.segment.get("text", ""),
                    "segment_rn": search_result.segment.get("rn")
                },
                # NEW: Reworked system specific data
                "base_segment_info": base_segment_info,
                "compound_segment_info": compound_segment_info,
                "backtrack_capability": backtrack_capability
            }

        except Exception as e:
            logger.error(f"Error getting enhanced source data from search result: {e}")
            return None

    def highlight_extraction_markdown_enhanced(self,
                                               extraction_markdown: str,
                                               navigation_info: Dict[str, Any],
                                               referenced_base_segments: List[Dict[str, Any]],
                                               highlight_format: str = "**{}**") -> str:
        """
        Enhanced highlighting using precise base segment location information.

        Args:
            extraction_markdown: The full extraction markdown content
            navigation_info: Navigation info from get_enhanced_source_data_from_search_result()
            referenced_base_segments: List of base segments with precise location data
            highlight_format: Format string for highlighting

        Returns:
            Markdown with highlighted relevant portions using precise base segment locations
        """
        try:
            if not referenced_base_segments:
                # Fallback to original highlighting method
                return self.highlight_extraction_markdown(extraction_markdown, navigation_info, highlight_format)

            highlighted_markdown = extraction_markdown

            # Sort base segments by start position to highlight in correct order
            sorted_segments = sorted(
                referenced_base_segments,
                key=lambda seg: (seg["line_range"]["start"], seg["position_range"]["start"])
            )

            # Apply highlighting for each base segment's precise location
            offset = 0  # Track offset due to highlighting additions

            for base_seg in sorted_segments:
                line_start = base_seg["line_range"]["start"]
                line_end = base_seg["line_range"]["end"]
                pos_start = base_seg["position_range"]["start"]
                pos_end = base_seg["position_range"]["end"]

                # Calculate character positions in the full markdown
                lines = highlighted_markdown.split('\n')

                if line_start < len(lines) and line_end < len(lines):
                    # Calculate start character position
                    char_start = sum(len(lines[i]) + 1 for i in range(line_start)) + pos_start + offset

                    # Calculate end character position
                    if line_start == line_end:
                        char_end = char_start + (pos_end - pos_start)
                    else:
                        char_end = sum(len(lines[i]) + 1 for i in range(line_end + 1)) + pos_end + offset

                    # Ensure valid bounds
                    char_start = max(0, min(char_start, len(highlighted_markdown)))
                    char_end = max(char_start, min(char_end, len(highlighted_markdown)))

                    if char_start < char_end:
                        # Extract text to highlight
                        text_to_highlight = highlighted_markdown[char_start:char_end]

                        # Apply highlighting
                        highlighted_text = highlight_format.format(text_to_highlight)

                        # Replace in the markdown
                        highlighted_markdown = (
                                highlighted_markdown[:char_start] +
                                highlighted_text +
                                highlighted_markdown[char_end:]
                        )

                        # Update offset for next highlighting
                        offset += len(highlighted_text) - len(text_to_highlight)

            return highlighted_markdown

        except Exception as e:
            logger.error(f"Error in enhanced markdown highlighting: {e}")
            # Fallback to original method
            return self.highlight_extraction_markdown(extraction_markdown, navigation_info, highlight_format)


    def get_base_segments(self, resource_id: str, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get base segments with complete source tracking information.

        Args:
            resource_id: Resource ID
            version: Version (None for latest)

        Returns:
            List of base segments with complete metadata
        """
        segmentation_module = self.get_segmentation_module()
        if not segmentation_module:
            return []

        base_segments = segmentation_module.get_base_segments(resource_id, version)

        # Convert to dictionary format for API compatibility
        return [base_seg.to_dict() for base_seg in base_segments]

    def get_source_data_from_search_result(self, search_result) -> Optional[Dict[str, Any]]:
        """
        Get the original raw data source and extraction markdown from a search result.

        Args:
            search_result: SearchResult object from search_for_context()

        Returns:
            Dict containing raw_data, extraction_markdown, and navigation_info
        """
        try:
            # Extract metadata from search result
            resource_id = search_result.search_metadata["resource_id"]
            version = search_result.search_metadata["version"]
            source_info = search_result.source_info

            # Get raw data
            raw_data = self.get_resource_content(resource_id, version, as_text=False)

            # Get extraction markdown
            extraction_index = source_info.get("extraction_index", 0)
            extraction_content_file = f"extraction_{extraction_index}.md"

            extraction_module = self.get_extraction_module()
            extraction_markdown = extraction_module.get_asset(
                resource_id, version, extraction_content_file
            )

            if extraction_markdown:
                extraction_markdown = extraction_markdown.decode('utf-8')

            # Prepare navigation info for highlighting/jumping to specific sections
            navigation_info = {
                "resource_id": resource_id,
                "version": version,
                "extraction_index": extraction_index,
                "extraction_content_file": extraction_content_file,
                "source_location": source_info,
                "heading_path": " > ".join(source_info.get("heading_path", [])),
                "citation_text": source_info.get("citation_text", ""),
                "markdown_excerpt": source_info.get("markdown_excerpt", ""),
                "char_range": {
                    "start": source_info.get("start_char", 0),
                    "end": source_info.get("end_char", 0)
                },
                "line_range": {
                    "start": source_info.get("start_line", 0),
                    "end": source_info.get("end_line", 0)
                }
            }

            return {
                "raw_data": raw_data,
                "extraction_markdown": extraction_markdown,
                "navigation_info": navigation_info,
                "search_context": {
                    "query": search_result.query,
                    "relevance_score": search_result.relevance_score,
                    "text_result": search_result.segment.get("text", ""),
                    "segment_rn": search_result.segment.get("rn")
                }
            }

        except Exception as e:
            logger.error(f"Error getting source data from search result: {e}")
            return None

    def highlight_extraction_markdown(self, extraction_markdown: str, navigation_info: Dict[str, Any],
                                      highlight_format: str = "**{}**") -> str:
        """
        Highlight the relevant portion of extraction markdown based on navigation info.

        Args:
            extraction_markdown: The full extraction markdown content
            navigation_info: Navigation info from get_source_data_from_search_result()
            highlight_format: Format string for highlighting (e.g., "**{}**" for bold)

        Returns:
            Markdown with highlighted relevant portion
        """
        try:
            char_range = navigation_info.get("char_range", {})
            start_char = char_range.get("start", 0)
            end_char = char_range.get("end", len(extraction_markdown))

            # Ensure valid bounds
            start_char = max(0, min(start_char, len(extraction_markdown)))
            end_char = max(start_char, min(end_char, len(extraction_markdown)))

            if start_char >= end_char:
                return extraction_markdown

            # Highlight the relevant portion
            before = extraction_markdown[:start_char]
            highlighted = highlight_format.format(extraction_markdown[start_char:end_char])
            after = extraction_markdown[end_char:]

            return before + highlighted + after

        except Exception as e:
            logger.error(f"Error highlighting extraction markdown: {e}")
            return extraction_markdown

    # Access summaries
    def get_document_summary(self, resource_id: str, version: Optional[str] = None) -> Optional[str]:
        """Get document summary."""
        summarization_module = self.get_summarization_module()
        return summarization_module.get_document_summary(resource_id, version) if summarization_module else None

    def search_resources(self, query: str, limit: int = 10) -> List[ResourceMetadata]:
        """Search resources by name or content (basic implementation)."""
        # This is a basic implementation - in a full system you'd use vector search
        results = []
        query_lower = query.lower()

        for resource in self.list_resources():
            # Search in name
            if query_lower in resource.name.lower():
                results.append(resource)
                continue

            # Search in content
            try:
                content = self.get_resource_text(resource.id)
                if content and query_lower in content.lower():
                    results.append(resource)
            except Exception as e:
                logger.warning(f"Error searching content of {resource.id}: {e}")

        return results[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics."""
        resources = self.list_resources()

        stats = {
            "total_resources": len(resources),
            "resources_by_type": {},
            "total_versions": 0,
            "total_size_bytes": 0,
            "registered_modules": list(self.pipeline.modules.keys()),
            "pipeline_order": self.pipeline.pipeline_order
        }

        for resource in resources:
            # Count by type
            source_type = resource.source_type
            stats["resources_by_type"][source_type] = stats["resources_by_type"].get(source_type, 0) + 1

            # Count versions and size
            versions = self.list_resource_versions(resource.id)
            stats["total_versions"] += len(versions)

            for version in versions:
                stats["total_size_bytes"] += version.size_bytes

        return stats

    def get_extraction_assets_for_resource(self, resource_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all extraction assets for a resource.

        Args:
            resource_id: Resource ID
            version: Version (None for latest)

        Returns:
            Dict containing all extraction assets and metadata
        """
        if version is None:
            version = self.storage.get_latest_version(resource_id)
            if not version:
                return {}

        extraction_module = self.get_extraction_module()

        # Get extraction results metadata
        extraction_results = extraction_module.get_extraction_results(resource_id, version)

        # Get all assets
        assets = extraction_module.list_assets(resource_id, version)

        # Get content for each asset
        asset_contents = {}
        for asset_type, asset_files in assets.items():
            asset_contents[asset_type] = {}
            for filename in asset_files:
                content = extraction_module.get_asset(resource_id, version, filename)
                if content and asset_type == "content":  # Decode markdown content
                    try:
                        content = content.decode('utf-8')
                    except:
                        pass
                asset_contents[asset_type][filename] = content

        return {
            "extraction_results": extraction_results,
            "assets": assets,
            "asset_contents": asset_contents,
            "resource_id": resource_id,
            "version": version
        }

    # Additional convenience methods for embeddings
    async def process_embeddings_only(self, resource_id: str, version: Optional[str] = None,
                                      embedding_model: ModelRecord = None,
                                      embedding_size: int = 1536,
                                      force_reprocess: bool = False) -> Dict[str, Any]:
        """Run only the embedding stage."""
        if embedding_model is None:
            raise ValueError("embedding_model is required for embedding processing")

        return await self.process_resource(
            resource_id, version,
            stages=["embedding"],
            stages_config={
                "embedding": {
                    "model_record": embedding_model,
                    "embedding_size": embedding_size
                }
            },
            force_reprocess=force_reprocess
        )

    def get_segment_embedding(self, resource_id: str, version: str, segment_id: str) -> Optional[List[float]]:
        """Get embedding for a specific segment."""
        embedding_module = self.get_embedding_module()
        if not embedding_module:
            return None

        from kdcube_ai_app.apps.knowledge_base.modules.segmentation import SegmentType
        return embedding_module.get_segment_embedding(
            resource_id, version, segment_id, SegmentType.RETRIEVAL
        )

    def get_embedding_stats(self, resource_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Get embedding statistics for a resource."""
        embedding_module = self.get_embedding_module()
        if not embedding_module:
            return {"error": "Embedding module not available"}

        from kdcube_ai_app.apps.knowledge_base.modules.segmentation import SegmentType

        # Get processing status
        status = embedding_module.get_processing_status(resource_id, version, SegmentType.RETRIEVAL)

        # Get all embeddings
        all_embeddings = embedding_module.get_all_embeddings(resource_id, version, SegmentType.RETRIEVAL)

        return {
            "processing_status": status,
            "total_embeddings": len(all_embeddings),
            "embedding_dimensions": len(list(all_embeddings.values())[0]) if all_embeddings else 0,
            "embedding_size_configured": getattr(embedding_module, 'embedding_size', None)
        }

    # ================================================================================
    #                          SEARCH INDEXING SPECIFIC METHODS
    # ================================================================================

    async def index_for_search(self, resource_id: str, version: Optional[str] = None,
                               force_reindex: bool = False) -> Dict[str, Any]:
        """Index a resource specifically for search (search_indexing stage only)."""
        return await self.process_resource(
            resource_id, version,
            stages=["search_indexing"],
            force_reprocess=force_reindex
        )

    def delete_from_search_index(self, resource_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Delete a resource from the search index."""
        search_indexing_module = self.get_search_indexing_module()
        if not search_indexing_module:
            raise ValueError("Search indexing module not available")

        return search_indexing_module.delete_from_search_index(resource_id, version)

    def get_indexed_resources(self) -> List[Dict[str, Any]]:
        """Get list of all resources that have been indexed for search."""
        search_indexing_module = self.get_search_indexing_module()
        if not search_indexing_module:
            return []

        return search_indexing_module.get_indexed_resources()

    def validate_search_index_consistency(self, resource_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Validate that search index is consistent with processed segments."""
        if version is None:
            version = self.storage.get_latest_version(resource_id)
            if not version:
                return {"error": f"Resource {resource_id} not found"}

        search_module = self.get_search_indexing_module()
        if not search_module:
            return {"error": "Search indexing module not available"}

        return search_module.validate_search_index(resource_id, version)