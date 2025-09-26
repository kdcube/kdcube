# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Search indexing module for loading processed segments into search database.
"""
from typing import Dict, Any, Optional
from datetime import datetime
from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.apps.knowledge_base.db.kb_db_connector import KnowledgeBaseConnector

class SearchIndexingModule(ProcessingModule):
    def __init__(self, storage, project, tenant, pipeline, db_connector: Optional[KnowledgeBaseConnector] = None, **kwargs):
        super().__init__(storage, project, tenant, pipeline)
        self.db_connector = db_connector

    @property
    def stage_name(self) -> str:
        return "search_indexing"

    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs) -> Dict[str, Any]:
        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Search indexing already completed for {resource_id} v{version}, skipping")
            return self.get_results(resource_id, version) or {}

        self.logger.info(f"Starting search indexing for {resource_id} v{version}")
        kb = kwargs.get("kb")
        if not kb:
            raise ValueError("KnowledgeBase instance ('kb') must be provided for search indexing.")

        connector = self._get_or_create_connector(kb, kwargs)
        try:
            # Verify prerequisites are met
            self._verify_prerequisites(resource_id, version)

            # Load complete resource into search database
            load_result = connector.load_complete_resource(kb, resource_id, version)

            results = {
                "resource_id": resource_id,
                "version": version,
                "search_indexing_completed": True,
                "datasource_result": load_result.get("datasource_result", {}),
                "segments_result": load_result.get("segments_result", {}),
                "indexing_timestamp": datetime.now().isoformat(),
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
            }
            self.save_results(resource_id, version, results)
            segments_loaded = load_result.get("segments_result", {}).get("segments_upserted", 0)
            self.log_operation("search_indexing_complete", resource_id, {
                "version": version,
                "segments_loaded": segments_loaded,
                "force_reprocess": force_reprocess
            })

            self.logger.info(
                f"Successfully indexed {segments_loaded} segments for {resource_id} v{version} "
                f"into search database"
            )

            return results

        except Exception as e:
            error_msg = f"Error during search indexing for {resource_id} v{version}: {e}"
            self.logger.error(error_msg)

            # Create error results
            error_results = {
                "resource_id": resource_id,
                "version": version,
                "search_indexing_completed": False,
                "error": str(e),
                "indexing_timestamp": datetime.now().isoformat(),
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
            }

            # Save error results
            self.save_results(resource_id, version, error_results)

            # Log error operation
            self.log_operation("search_indexing_error", resource_id, {
                "version": version,
                "error": str(e),
                "force_reprocess": force_reprocess
            })

            raise

    def _get_or_create_connector(self, kb, kwargs) -> KnowledgeBaseConnector:
        """Get existing connector or create new one from KB instance."""
        # Use provided connector if available
        if self.db_connector:
            return self.db_connector

        # Try to get connector from KB instance
        if hasattr(kb, 'db_connector') and kb.db_connector:
            return kb.db_connector

        # Try to get connector from kwargs
        connector = kwargs.get("db_connector")
        if connector:
            return connector

        raise ValueError(
            "No database connector available. Either provide db_connector in module init, "
            "ensure KB instance has db_connector attribute, or pass db_connector in kwargs."
        )

    def _verify_prerequisites(self, resource_id: str, version: str) -> None:
        """
        Verify that prerequisite processing stages are completed.

        Args:
            resource_id: Resource identifier
            version: Resource version

        Raises:
            ValueError: If prerequisites are not met
        """
        # Check that segmentation is completed (required for retrieval segments)
        segmentation_module = self.pipeline.get_module("segmentation")
        if not segmentation_module:
            raise ValueError("Segmentation module not found in pipeline")

        if not segmentation_module.is_processed(resource_id, version):
            raise ValueError(
                f"Segmentation must be completed before search indexing for {resource_id} v{version}"
            )

        # Check that retrieval segments exist
        retrieval_segments = segmentation_module.get_retrieval_segments(resource_id, version)
        if not retrieval_segments:
            self.logger.warning(
                f"No retrieval segments found for {resource_id} v{version}. "
                f"Search indexing will proceed but no segments will be loaded."
            )

        self.logger.debug(f"Prerequisites verified for {resource_id} v{version}")

    def get_indexing_status(self, resource_id: str, version: str) -> Dict[str, Any]:
        """
        Get detailed indexing status for a resource.

        Args:
            resource_id: Resource identifier
            version: Resource version

        Returns:
            Dictionary with indexing status and statistics
        """
        try:
            # Check if indexing completed
            is_indexed = self.is_processed(resource_id, version)
            results = self.get_results(resource_id, version) if is_indexed else {}

            # Get connector stats if available
            connector_stats = {}
            # try:
            #     if self.db_connector:
            #         connector_stats = self.db_connector.get_connector_stats()
            # except Exception as e:
            #     self.logger.warning(f"Could not get connector stats: {e}")

            return {
                "resource_id": resource_id,
                "version": version,
                "is_indexed": is_indexed,
                "indexing_results": results,
                "status_timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error getting indexing status: {e}")
            return {
                "resource_id": resource_id,
                "version": version,
                "error": str(e),
                "status_timestamp": datetime.now().isoformat()
            }

    async def reindex_resource(self, resource_id: str, version: str, **kwargs) -> Dict[str, Any]:
        """
        Force reindexing of a resource.

        Args:
            resource_id: Resource identifier
            version: Resource version
            **kwargs: Additional arguments for processing

        Returns:
            Reindexing results
        """
        self.logger.info(f"Force reindexing resource {resource_id} v{version}")

        # Clean up existing indexing data
        self.cleanup(resource_id, version)

        # Reprocess with force flag
        return await self.process(resource_id, version, force_reprocess=True, **kwargs)

    def delete_from_search_index(self, resource_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """
        Delete resource from search index.

        Args:
            resource_id: Resource identifier
            version: Specific version to delete (None for all versions)

        Returns:
            Deletion results
        """
        try:
            if not self.db_connector:
                raise ValueError("Database connector not available for deletion")

            # Delete from search database
            deletion_result = self.db_connector.delete_datasource(resource_id, version)

            # Clean up local processing artifacts
            if version:
                self.cleanup(resource_id, version)
            else:
                # Clean up all versions
                versions = self.storage.list_versions(resource_id)
                for v in versions:
                    self.cleanup(resource_id, v)

            self.logger.info(f"Deleted {resource_id} v{version} from search index")
            return deletion_result

        except Exception as e:
            self.logger.error(f"Error deleting from search index: {e}")
            raise

    def validate_search_index(self, resource_id: str, version: str) -> Dict[str, Any]:
        """
        Validate that the search index is consistent with processed segments.

        Args:
            resource_id: Resource identifier
            version: Resource version

        Returns:
            Validation results
        """
        try:
            validation_results = {
                "resource_id": resource_id,
                "version": version,
                "is_valid": True,
                "issues": [],
                "validation_timestamp": datetime.now().isoformat()
            }

            # Check if resource has indexed segments (much cleaner!)
            if self.db_connector:
                indexing_status = self.db_connector.is_resource_indexed(resource_id, version)

                if not indexing_status["is_indexed"]:
                    validation_results["is_valid"] = False
                    validation_results["issues"].append("Resource has no indexed segments in search database")
                else:
                    # Check segment count consistency with actual file-based segments
                    segmentation_module = self.pipeline.get_module("segmentation")
                    if segmentation_module:
                        retrieval_segments = segmentation_module.get_retrieval_segments(resource_id, version)
                        expected_count = len(retrieval_segments) if retrieval_segments else 0
                        actual_count = indexing_status["segment_count"]

                        if actual_count != expected_count:
                            validation_results["is_valid"] = False
                            validation_results["issues"].append(
                                f"Segment count mismatch: DB has {actual_count} indexed segments, "
                                f"expected {expected_count} from segmentation"
                            )

                        validation_results["actual_segment_count"] = actual_count
                        validation_results["expected_segment_count"] = expected_count

            return validation_results

        except Exception as e:
            self.logger.error(f"Error validating search index: {e}")
            return {
                "resource_id": resource_id,
                "version": version,
                "is_valid": False,
                "error": str(e),
                "validation_timestamp": datetime.now().isoformat()
            }