# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Base classes and interfaces for knowledge base processing modules.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union
from datetime import datetime

from kdcube_ai_app.apps.knowledge_base.db.kb_db_connector import KnowledgeBaseConnector
from kdcube_ai_app.apps.knowledge_base.storage import KnowledgeBaseStorage

logger = logging.getLogger("KnowledgeBase.Modules")


class ProcessingModule(ABC):
    """Base class for all processing modules."""

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline: 'ProcessingPipeline'):
        self.storage = storage
        self.project = project
        self.tenant = tenant
        self.logger = logging.getLogger(f"KnowledgeBase.{self.__class__.__name__}")
        self.pipeline = pipeline

    @property
    @abstractmethod
    def stage_name(self) -> str:
        """Return the storage stage name for this module."""
        pass

    @abstractmethod
    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs) -> Union[Any, Dict[str, Any]]:
        """Process a resource for this stage."""
        pass

    def is_processed(self, resource_id: str, version: str) -> bool:
        """Check if this stage has been processed for the given resource/version."""
        return self.storage.stage_exists(self.stage_name, resource_id, version)

    def get_results(self, resource_id: str, version: str) -> Optional[Dict[str, Any]]:
        """Get existing results for this stage."""
        try:
            content = self.storage.get_stage_content(self.stage_name, resource_id, version, f"{self.stage_name}.json")
            if content:
                import json
                return json.loads(content)
        except Exception as e:
            self.logger.error(f"Error getting results for {resource_id} v{version}: {e}")
        return None

    def save_results(self, resource_id: str, version: str, results: Dict[str, Any], filename: Optional[str] = None) -> None:
        """Save results for this stage."""
        if filename is None:
            filename = f"{self.stage_name}.json"

        import json
        content = json.dumps(results, indent=2, default=str, ensure_ascii=False)
        self.storage.save_stage_content(self.stage_name, resource_id, version, filename, content)

    def cleanup(self, resource_id: str, version: str) -> None:
        """Clean up incomplete processing for this stage."""
        self.storage.delete_stage_content(self.stage_name, resource_id, version)

    def log_operation(self, operation: str, resource_id: str, details: Dict[str, Any]) -> None:
        """Log an operation for this module."""
        details["module"] = self.__class__.__name__
        details["stage"] = self.stage_name
        self.storage.log_operation(operation, resource_id, details)


class ProcessingPipeline:
    """Orchestrates the processing pipeline across multiple modules."""

    def __init__(self, storage: KnowledgeBaseStorage, project: str):
        self.storage = storage
        self.project = project
        self.modules: Dict[str, ProcessingModule] = {}
        self.pipeline_order: List[str] = []
        self.logger = logging.getLogger("KnowledgeBase.Pipeline")

    def register_module(self, module: ProcessingModule, order: Optional[int] = None) -> None:
        """Register a processing module in the pipeline."""
        stage_name = module.stage_name
        self.modules[stage_name] = module

        if order is not None:
            # Insert at specific position
            if order >= len(self.pipeline_order):
                self.pipeline_order.append(stage_name)
            else:
                self.pipeline_order.insert(order, stage_name)
        else:
            # Append to end
            self.pipeline_order.append(stage_name)

        self.logger.info(f"Registered {module.__class__.__name__} for stage '{stage_name}'")

    def get_module(self, stage_name: str) -> Optional[ProcessingModule]:
        """Get a specific module by stage name."""
        return self.modules.get(stage_name)

    async def process_resource(self,
                             resource_id: str,
                             version: str,
                             stages: Optional[List[str]] = None,
                             force_reprocess: bool = False,
                             **kwargs) -> Dict[str, Any]:
        """Process a resource through specified stages or the full pipeline."""

        if stages is None:
            stages = self.pipeline_order

        results = {
            "resource_id": resource_id,
            "version": version,
            "processing_status": {},
            "stage_results": {},
            "pipeline_start": datetime.now().isoformat()
        }

        try:
            stages_config = kwargs.pop("stages_config", {})
            for stage_name in stages:
                stage_config = stages_config.get(stage_name, {})
                if stage_name not in self.modules:
                    self.logger.warning(f"Stage '{stage_name}' not registered, skipping")
                    continue

                module = self.modules[stage_name]

                # Check if already processed
                # TODO: on resoure deletion, make sure all folders are deleted (for all stages)
                if not force_reprocess and module.is_processed(resource_id, version):
                    self.logger.info(f"Stage '{stage_name}' already processed for {resource_id} v{version}")
                    results["processing_status"][stage_name] = "skipped"
                    results["stage_results"][stage_name] = module.get_results(resource_id, version)
                    continue

                # Process this stage
                self.logger.info(f"Processing stage '{stage_name}' for {resource_id} v{version}")
                stage_start = datetime.now()

                try:
                    stage_results = await module.process(resource_id, version, force_reprocess, **stage_config, **kwargs)

                    results["processing_status"][stage_name] = "completed"
                    results["stage_results"][stage_name] = stage_results

                    stage_duration = (datetime.now() - stage_start).total_seconds()
                    self.logger.info(f"Completed stage '{stage_name}' in {stage_duration:.2f}s")

                except Exception as e:
                    self.logger.error(f"Error in stage '{stage_name}': {e}")
                    results["processing_status"][stage_name] = "failed"
                    results["stage_results"][stage_name] = {"error": str(e)}

                    # Cleanup incomplete processing
                    module.cleanup(resource_id, version)

                    # Stop pipeline on error (configurable behavior)
                    if kwargs.get("stop_on_error", True):
                        raise

        except Exception as e:
            results["pipeline_status"] = "failed"
            results["pipeline_error"] = str(e)
            raise

        results["pipeline_status"] = "completed"
        results["pipeline_end"] = datetime.now().isoformat()

        return results

    def get_processing_status(self, resource_id: str, version: str) -> Dict[str, bool]:
        """Get processing status for all registered modules."""
        status = {}
        for stage_name, module in self.modules.items():
            status[stage_name] = module.is_processed(resource_id, version)
        return status

    def cleanup_incomplete_processing(self, resource_id: str, version: str,
                                      keep_stages: Optional[List[str]] = None) -> None:
        """Clean up incomplete processing, optionally keeping specified stages."""
        if keep_stages is None:
            keep_stages = []

        for stage_name, module in self.modules.items():
            if stage_name not in keep_stages:
                module.cleanup(resource_id, version)
                self.logger.info(f"Cleaned up stage '{stage_name}' for {resource_id} v{version}")


class ModuleFactory:
    """Factory for creating and configuring processing modules."""

    @staticmethod
    def create_default_pipeline(storage: KnowledgeBaseStorage,
                                project: str,
                                tenant: str,
                                processing_mode: str = "full_indexing",
                                db_connector: Optional[KnowledgeBaseConnector] = None) -> ProcessingPipeline:

        from kdcube_ai_app.apps.knowledge_base.modules.extraction import ExtractionModule
        from kdcube_ai_app.apps.knowledge_base.modules.segmentation import SegmentationModule
        from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import ProcessingMode
        from kdcube_ai_app.apps.knowledge_base.modules.metadata import MetadataModule
        from kdcube_ai_app.apps.knowledge_base.modules.summarization import SummarizationModule
        from kdcube_ai_app.apps.knowledge_base.modules.embedding import EmbeddingModule
        from kdcube_ai_app.apps.knowledge_base.modules.search_indexing import SearchIndexingModule
        from kdcube_ai_app.apps.knowledge_base.modules.enrichment import EnrichmentModule

        pipeline = ProcessingPipeline(storage, project)

        # Convert string mode to enum
        if processing_mode == "full_indexing":
            mode = ProcessingMode.FULL_INDEXING
        elif processing_mode == "retrieval_only":
            mode = ProcessingMode.RETRIEVAL_ONLY
        else:
            mode = ProcessingMode.FULL_INDEXING

        config = {
            "segmentation": {
                "processing_mode": mode,
                "continuous_min_tokens": 40,  # Renamed from curriculum_min_tokens

            },
            "metadata": {
                "processing_mode": mode,
            },
            "embedding": {
                "processing_mode": mode,
            },
            "search_indexing": {
                "db_connector": db_connector
            }
        }

        # Register modules in processing order
        pipeline.register_module(ExtractionModule(storage, project, tenant, pipeline), 0)
        pipeline.register_module(SegmentationModule(storage, project, tenant, pipeline, **config.get("segmentation", {})), 1)
        pipeline.register_module(EnrichmentModule(storage, project, tenant, pipeline), 2)

        pipeline.register_module(MetadataModule(storage, project, tenant, pipeline, **config.get("metadata", {})), 2)

        # pipeline.register_module(SummarizationModule(storage, project, tenant, pipeline), 3)
        pipeline.register_module(EmbeddingModule(storage, project, tenant, pipeline, **config.get("embedding", {})), 3)
        pipeline.register_module(SearchIndexingModule(storage, project, tenant, pipeline, **config.get("search_indexing", {})), 4)
        # pipeline.register_module(TfIdfModule(storage, project, tenant, pipeline), 5)

        return pipeline
