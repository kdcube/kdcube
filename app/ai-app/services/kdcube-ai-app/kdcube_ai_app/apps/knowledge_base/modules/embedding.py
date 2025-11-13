# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/knowledge_base/modules/embedding.py
"""
Improved Embedding processing module with async parallelization and batch processing.
"""

import asyncio
import contextvars
import json
import logging
import os
from typing import Dict, Any, List, Optional, Set, Union
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType, ProcessingMode
from kdcube_ai_app.apps.knowledge_base.storage import KnowledgeBaseStorage
from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord, EmbeddingResultWrapper
from kdcube_ai_app.infra.accounting import SystemResource

# Import your existing embedding function
from kdcube_ai_app.infra.embedding.embedding import get_embedding, parse_embedding


@dataclass
class ProcessingBatch:
    """Represents a batch of segments to process together."""
    batch_id: int
    segments: List[Dict[str, Any]]
    total_batches: int


class AsyncSegmentEmbeddingProcessor:
    """
    Improved processor for segment embeddings with async parallelization.
    Processes segments in parallel batches for better performance.
    """

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline,
                 self_hosted_serving_endpoint: str = None,
                 max_concurrent_requests: int = 5,
                 batch_size: int = 10,
                 max_workers: int = None):
        self.storage = storage
        self.project = project
        self.tenant = tenant
        self.pipeline = pipeline
        self.self_hosted_serving_endpoint = self_hosted_serving_endpoint
        self.logger = logging.getLogger(self.__class__.__name__)

        # Concurrency settings
        self.max_concurrent_requests = max_concurrent_requests
        self.batch_size = batch_size
        self.max_workers = max_workers or min(32, (os.cpu_count() or 1) + 4)

        # Semaphore to limit concurrent requests
        self.request_semaphore = asyncio.Semaphore(max_concurrent_requests)

        # Thread pool for CPU-intensive operations
        self.thread_pool = ThreadPoolExecutor(max_workers=self.max_workers)

    def _get_embedding_payload_for_segment(self, resource_id, version, segment_guid, default_text, use_enriched):
        if not use_enriched:
            return default_text
        try:
            c = self.storage.get_stage_content("enrichment", resource_id, version, f"segment_{segment_guid}_enrichment.json", as_text=True)
            if not c: return default_text
            rec = json.loads(c)
            return rec.get("embedding_text") or default_text
        except Exception:
            return default_text

    def _get_processed_segment_ids(self, resource_id: str, version: str, segment_type: SegmentType, embedding_size: int) -> Set[str]:
        """Get set of segment IDs that already have embeddings successfully computed."""
        try:
            subfolder = f"{segment_type.value}/size_{embedding_size}"
            files = self.storage.list_stage_files("embedding", resource_id, version, subfolder=subfolder)

            processed_ids = set()
            for file_path in files:
                if file_path.endswith("_embedding.json"):
                    try:
                        content = self.storage.get_stage_content(
                            "embedding", resource_id, version, file_path,
                            subfolder=subfolder, as_text=True
                        )

                        if content:
                            embedding_data = json.loads(content)
                            is_successful = (
                                    embedding_data.get("success", False) and
                                    embedding_data.get("embedding") is not None and
                                    embedding_data.get("embedding_dimensions", 0) > 0
                            )

                            if is_successful:
                                segment_id = file_path.replace("_embedding.json", "").replace("segment_", "")
                                processed_ids.add(segment_id)
                            else:
                                segment_id = file_path.replace("_embedding.json", "").replace("segment_", "")
                                error_msg = embedding_data.get("error_message", "Unknown error")
                                self.logger.debug(f"Skipping failed embedding for segment {segment_id}: {error_msg}")

                    except (json.JSONDecodeError, KeyError) as e:
                        self.logger.warning(f"Error parsing embedding file {file_path}: {e}")
                        continue

            self.logger.info(f"Found {len(processed_ids)} successfully processed {segment_type.value} segments with embeddings (size {embedding_size})")
            return processed_ids

        except Exception as e:
            self.logger.warning(f"Error checking processed segments: {e}")
            return set()

    def _get_unprocessed_segments(self, resource_id: str, version: str, segment_type: SegmentType, embedding_size: int) -> List[Dict[str, Any]]:
        """Get segments that need embedding processing."""
        segmentation_module = self.pipeline.get_module("segmentation")
        if not segmentation_module:
            self.logger.error("Segmentation module not found")
            return []

        all_segments = segmentation_module.get_segments_by_type(resource_id, version, segment_type)
        processed_ids = self._get_processed_segment_ids(resource_id, version, segment_type, embedding_size)

        unprocessed = []
        for segment in all_segments:
            segment_id = segment.get("segment_id")
            if segment_id and segment_id not in processed_ids:
                unprocessed.append(segment)

        self.logger.info(f"Found {len(unprocessed)} segments to process for {segment_type.value} embeddings "
                         f"(out of {len(all_segments)} total)")
        return unprocessed

    def _create_batches(self, segments: List[Dict[str, Any]]) -> List[ProcessingBatch]:
        """Split segments into processing batches."""
        batches = []
        total_batches = (len(segments) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(segments), self.batch_size):
            batch_segments = segments[i:i + self.batch_size]
            batch_id = i // self.batch_size + 1
            batches.append(ProcessingBatch(
                batch_id=batch_id,
                segments=batch_segments,
                total_batches=total_batches
            ))

        return batches

    async def _generate_embedding_with_semaphore(self, segment: Dict[str, Any], model_record: ModelRecord,
                                                 embedding_size: int, resource_id: str, version: str,
                                                 segment_type: SegmentType) -> EmbeddingResultWrapper:
        """Generate embedding for a single segment with semaphore control."""
        async with self.request_semaphore:
            return await self._generate_embedding(segment, model_record, embedding_size, resource_id, version, segment_type)

    async def _generate_embedding(self, segment: Dict[str, Any], model_record: ModelRecord,
                                  embedding_size: int, resource_id: str, version: str,
                                  segment_type: SegmentType) -> EmbeddingResultWrapper:
        """Generate embedding for a single segment and record usage."""
        segment_id = segment.get("segment_id")

        text = segment.get("summary", "")
        if not text:
            text = segment.get("text", "")

        if not text.strip():
            return EmbeddingResultWrapper.from_error(
                segment_id=segment_id,
                error_message="Empty text content",
                model=model_record.systemName,
                provider=model_record.provider.provider.value,
                text_length=0
            )

        try:
            seed_resources = [self._create_system_resource_from_segment(segment, resource_id, version)]

            from kdcube_ai_app.infra.accounting import with_accounting
            with with_accounting("kb.proc_pipeline.embedding",
                                 metadata={
                                    "segment_type": segment_type.value,
                                    "embedding_size": embedding_size,
                                    "phase": f"{segment_type}_segment_embedding"
                                },
                                 seed_system_resources=seed_resources):
                # Run embedding generation in thread pool to avoid blocking
                ctx = contextvars.copy_context()
                embedding = await asyncio.to_thread(
                    lambda: ctx.run(
                        get_embedding,
                        model=model_record,
                        text=text,
                        size=embedding_size,
                        self_hosted_serving_endpoint=self.self_hosted_serving_endpoint,
                    )
                )

            estimated_tokens = len(text.split()) * 1.3
            embedding_result = EmbeddingResultWrapper.from_success(
                segment_id=segment_id,
                embedding=embedding,
                model=model_record.systemName,
                provider=model_record.provider.provider.value,
                text_length=len(text),
                embedding_tokens=int(estimated_tokens)
            )
            return embedding_result

        except Exception as e:
            self.logger.error(f"Error generating embedding for segment {segment_id}: {e}")

            embedding_result = EmbeddingResultWrapper.from_error(
                segment_id=segment_id,
                error_message=str(e),
                model=model_record.systemName,
                provider=model_record.provider.provider.value,
                text_length=len(text),
                raw_response=e
            )
            return embedding_result

    async def _process_batch(self, batch: ProcessingBatch, model_record: ModelRecord,
                             embedding_size: int, resource_id: str, version: str,
                             segment_type: SegmentType) -> List[Dict[str, Any]]:
        """Process a batch of segments in parallel."""
        self.logger.info(f"Processing batch {batch.batch_id}/{batch.total_batches} "
                         f"with {len(batch.segments)} segments")

        # Create tasks for parallel processing within the batch
        tasks = []
        for segment in batch.segments:
            task = self._generate_embedding_with_semaphore(
                segment, model_record, embedding_size, resource_id, version, segment_type
            )
            tasks.append(task)

        # Execute all tasks in parallel
        embedding_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and save embeddings
        batch_metadata = []
        successful_count = 0
        failed_count = 0

        for i, (segment, result) in enumerate(zip(batch.segments, embedding_results)):
            if isinstance(result, Exception):
                self.logger.error(f"Exception in batch {batch.batch_id}, segment {i}: {result}")
                # Create error result
                result = EmbeddingResultWrapper.from_error(
                    segment_id=segment.get("segment_id"),
                    error_message=str(result),
                    model=model_record.systemName,
                    provider=model_record.provider.provider.value,
                    text_length=len(segment.get("text", ""))
                )
                failed_count += 1
            else:
                if result.success:
                    successful_count += 1
                else:
                    failed_count += 1

            # Create metadata
            metadata = self._create_segment_embedding_metadata(
                segment, result, i, resource_id, version, segment_type, embedding_size
            )

            batch_metadata.append(metadata)

            # Save immediately
            self._save_segment_embedding(metadata, resource_id, version, segment_type, embedding_size)

            # Log processing result
            tokens_used = result.usage.embedding_tokens if result.usage else 0
            self._log_segment_processed(
                segment.get("segment_id"),
                result.success,
                result.embedding_dimensions,
                tokens_used
            )

        self.logger.info(f"Batch {batch.batch_id}/{batch.total_batches} completed: "
                         f"{successful_count} successful, {failed_count} failed")

        return batch_metadata

    async def process_segments_parallel(self, resource_id: str, version: str, segment_type: SegmentType,
                                        model_record: ModelRecord, embedding_size: int) -> Dict[str, Any]:
        """
        Process segments to generate embeddings using parallel batch processing.

        Args:
            resource_id: Resource identifier
            version: Resource version
            segment_type: Type of segments to process (RETRIEVAL, CONTINUOUS)
            model_record: Model configuration for embedding generation
            embedding_size: Expected embedding dimensions

        Returns:
            Dictionary with processing statistics
        """
        self.logger.info(f"Starting parallel embedding generation for {resource_id} v{version} "
                         f"{segment_type.value} (size: {embedding_size})")
        self.logger.info(f"Concurrency settings: max_requests={self.max_concurrent_requests}, "
                         f"batch_size={self.batch_size}, max_workers={self.max_workers}")

        # Get unprocessed segments
        unprocessed_segments = self._get_unprocessed_segments(resource_id, version, segment_type, embedding_size)

        if not unprocessed_segments:
            self.logger.info("No unprocessed segments found")
            return {
                "processed_count": 0,
                "skipped_count": 0,
                "total_segments": 0,
                "embedding_size": embedding_size,
                "method": "parallel_batch",
                "batches_processed": 0
            }

        # Create batches
        batches = self._create_batches(unprocessed_segments)
        self.logger.info(f"Created {len(batches)} batches for {len(unprocessed_segments)} segments")

        # Process batches sequentially, but segments within each batch in parallel
        total_processed = 0
        total_failed = 0
        start_time = datetime.now()

        try:
            for batch in batches:
                batch_start = datetime.now()

                # Process batch
                batch_metadata = await self._process_batch(
                    batch, model_record, embedding_size, resource_id, version, segment_type
                )

                # Count results
                batch_successful = sum(1 for meta in batch_metadata if meta.get("success", False))
                batch_failed = len(batch_metadata) - batch_successful

                total_processed += batch_successful
                total_failed += batch_failed

                batch_duration = (datetime.now() - batch_start).total_seconds()
                self.logger.info(f"Batch {batch.batch_id} completed in {batch_duration:.2f}s")

        except Exception as e:
            self.logger.error(f"Error during parallel processing: {e}")
            raise

        finally:
            # Clean up thread pool
            # self.thread_pool.shutdown(wait=False)
            pass

        total_duration = (datetime.now() - start_time).total_seconds()
        self.logger.info(f"Parallel processing completed in {total_duration:.2f}s: "
                         f"{total_processed} successful, {total_failed} failed")

        return {
            "processed_count": total_processed,
            "failed_count": total_failed,
            "total_segments": len(unprocessed_segments),
            "embedding_size": embedding_size,
            "method": "parallel_batch",
            "batches_processed": len(batches),
            "batch_size": self.batch_size,
            "max_concurrent_requests": self.max_concurrent_requests,
            "processing_time_seconds": total_duration
        }

    def _create_segment_embedding_metadata(self, segment: Dict[str, Any], embedding_result: EmbeddingResultWrapper,
                                           position: int, resource_id: str, version: str,
                                           segment_type: SegmentType, embedding_size: int) -> Dict[str, Any]:
        """Create embedding metadata for a segment."""
        text = segment.get("text", "")

        return {
            "segment_id": segment.get("segment_id"),
            "segment_type": segment_type.value,
            "resource_id": resource_id,
            "version": version,
            "embedding": embedding_result.embedding_string,
            "embedding_dimensions": embedding_result.embedding_dimensions,
            "embedding_size": embedding_size,
            "text_length": len(text),
            "word_count": len(text.split()),
            "model_used": embedding_result.model,
            "provider": embedding_result.provider,
            "success": embedding_result.success,
            "error_message": embedding_result.error_message,
            "processed_at": datetime.now().isoformat(),
            "usage": embedding_result.usage.__dict__ if embedding_result.usage else None,
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:embedding:{segment_type.value}:{resource_id}:{version}:segment:{segment.get('segment_id', position)}"
        }

    def _save_segment_embedding(self, metadata: Dict[str, Any], resource_id: str, version: str,
                                segment_type: SegmentType, embedding_size: int):
        """Save embedding metadata for a single segment."""
        segment_id = metadata["segment_id"]
        filename = f"segment_{segment_id}_embedding.json"
        subfolder = f"{segment_type.value}/size_{embedding_size}"

        content = json.dumps(metadata, indent=2, ensure_ascii=False)
        self.storage.save_stage_content("embedding", resource_id, version, filename, content, subfolder=subfolder)

    def _create_system_resource_from_segment(self, segment: Dict[str, Any], resource_id: str, version: str) -> SystemResource:
        """Create a SystemResource object from a segment for usage tracking."""
        return SystemResource(
            resource_type="segment",
            resource_id=segment.get("segment_id"),
            resource_version=version,
            rn=segment.get("rn"),
            metadata={
                "source_id": resource_id,
                "text_length": len(segment.get("text", ""))
            }
        )

    def _log_segment_processed(self, segment_id: str, success: bool, dimensions: int = 0, tokens_used: int = 0):
        """Log brief status line for processed segment."""
        status = "✅ SUCCESS" if success else "❌ FAILED"
        self.logger.info(
            f"{status} | Segment: {segment_id} | "
            f"Tokens: {tokens_used} | "
            f"Dimensions: {dimensions}"
        )

    # Keep existing methods for compatibility
    def get_segment_embedding(self, resource_id: str, version: str, segment_id: str,
                              segment_type: SegmentType, embedding_size: int) -> Optional[List[float]]:
        """Get embedding for a specific segment."""
        try:
            subfolder = f"{segment_type.value}/size_{embedding_size}"
            filename = f"segment_{segment_id}_embedding.json"
            content = self.storage.get_stage_content(
                "embedding", resource_id, version, filename,
                as_text=True, subfolder=subfolder
            )
            if content:
                metadata = json.loads(content)
                embedding_str = metadata.get("embedding")
                if embedding_str:
                    return parse_embedding(embedding_str)
            return None
        except Exception as e:
            self.logger.error(f"Error getting segment embedding: {e}")
            return None


class EmbeddingModule(ProcessingModule):
    """
    Improved Processing module for segment embedding generation with parallel processing.
    """

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline,
                 processing_mode: ProcessingMode = ProcessingMode.FULL_INDEXING,
                 embedding_size: int = 1536,
                 self_hosted_serving_endpoint: str = None,
                 max_concurrent_requests: int = 5,
                 batch_size: int = 10,
                 max_workers: int = None):
        """
        Initialize improved embedding module.

        Args:
            max_concurrent_requests: Maximum concurrent embedding requests
            batch_size: Number of segments to process in each batch
            max_workers: Maximum thread pool workers for CPU-intensive operations
        """
        super().__init__(storage, project, tenant, pipeline)
        self.processing_mode = processing_mode
        self.embedding_size = embedding_size
        self.self_hosted_serving_endpoint = self_hosted_serving_endpoint

        # Create the improved processor instance
        self.processor = AsyncSegmentEmbeddingProcessor(
            storage, project, tenant, pipeline, self_hosted_serving_endpoint,
            max_concurrent_requests, batch_size, max_workers
        )

    @property
    def stage_name(self) -> str:
        return "embedding"

    def get_enabled_segment_types(self) -> List[SegmentType]:
        """Get the segment types that should be processed based on processing mode."""
        if self.processing_mode == ProcessingMode.FULL_INDEXING:
            return [SegmentType.CONTINUOUS, SegmentType.RETRIEVAL]
        else:  # RETRIEVAL_ONLY
            return [SegmentType.RETRIEVAL]

    async def process(self,
                      resource_id: str,
                      version: str,
                      force_reprocess: bool = False,
                      **kwargs) -> Dict[str, Any]:
        """Generate embeddings for segments using improved parallel processing."""

        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Embeddings already exist for {resource_id} v{version}, skipping")
            return self.get_results(resource_id, version) or {}

        model_record: Optional[Union[dict, ModelRecord]] = kwargs.get("model_record")
        if not model_record:
            raise ValueError("model_record is required for metadata processing")
        if not isinstance(model_record, ModelRecord):
            model_record = ModelRecord(**model_record)

        embedding_size = (model_record.metadata or {}).get("dim", self.embedding_size)

        self.logger.info(f"Generating embeddings for {resource_id} v{version} "
                         f"(mode: {self.processing_mode.value}, size: {embedding_size})")

        enabled_types = self.get_enabled_segment_types()
        all_results = {}
        total_processed = 0

        for segment_type in enabled_types:
            self.logger.info(f"Processing embeddings for {segment_type.value} segments")

            # Use improved parallel processing
            result = await self.processor.process_segments_parallel(
                resource_id=resource_id,
                version=version,
                segment_type=segment_type,
                model_record=model_record,
                embedding_size=embedding_size
            )

            all_results[segment_type.value] = result
            total_processed += result.get("processed_count", 0)

        # Create overall results
        overall_results = {
            "resource_id": resource_id,
            "version": version,
            "processing_mode": self.processing_mode.value,
            "enabled_types": [t.value for t in enabled_types],
            "embedding_size": embedding_size,
            "results_by_type": all_results,
            "total_segments_processed": total_processed,
            "generation_timestamp": datetime.now().isoformat(),
            "embedding_model_used": model_record.systemName,
            "embedding_provider": model_record.provider.provider.value,
            "processing_method": "parallel_batch",
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
        }

        self.save_results(resource_id, version, overall_results)

        self.log_operation("embedding_complete", resource_id, {
            "version": version,
            "processing_mode": self.processing_mode.value,
            "enabled_types": [t.value for t in enabled_types],
            "embedding_size": embedding_size,
            "total_segments_processed": total_processed,
            "embedding_model": model_record.systemName,
            "embedding_provider": model_record.provider.provider.value,
            "processing_method": "parallel_batch"
        })

        self.logger.info(f"Generated embeddings for {total_processed} segments "
                         f"(size: {embedding_size}) for {resource_id} v{version}")
        return overall_results

    # Keep other existing methods for compatibility
    def get_processing_status(self, resource_id: str, version: str, segment_type: SegmentType) -> Dict[str, Any]:
        """Get detailed processing status."""
        return self.processor.get_processing_status(resource_id, version, segment_type)

    def get_segment_embedding(self, resource_id: str, version: str, segment_id: str,
                              segment_type: SegmentType) -> Optional[List[float]]:
        """Get embedding for a specific segment."""
        return self.processor.get_segment_embedding(
            resource_id, version, segment_id, segment_type, self.embedding_size
        )

    def get_all_embeddings(self, resource_id: str, version: str,
                           segment_type: SegmentType) -> Dict[str, List[float]]:
        """Get all embeddings for a resource and segment type."""
        try:
            subfolder = f"{segment_type.value}/size_{self.embedding_size}"
            files = self.storage.list_stage_files("embedding", resource_id, version, subfolder=subfolder)

            embeddings = {}
            for file_path in files:
                if file_path.endswith("_embedding.json"):
                    segment_id = file_path.replace("_embedding.json", "").replace("segment_", "")

                    content = self.storage.get_stage_content(
                        "embedding", resource_id, version, file_path,
                        as_text=True, subfolder=subfolder
                    )

                    if content:
                        metadata = json.loads(content)
                        if metadata.get("success") and metadata.get("embedding"):
                            embedding = parse_embedding(metadata["embedding"])
                            if embedding:
                                embeddings[segment_id] = embedding

            return embeddings

        except Exception as e:
            self.logger.error(f"Error getting all embeddings: {e}")
            return {}

    def get_resource_records(self,
                             resource_id: str,
                             version: str,
                             segment_type: SegmentType) -> Dict[str, List[float]]:
        """Get records for a resource and segment type."""
        try:
            subfolder = f"{segment_type.value}/size_{self.embedding_size}"
            files = self.storage.list_stage_files("embedding", resource_id, version, subfolder=subfolder)

            records = {}
            for file_path in files:
                if file_path.endswith("_embedding.json"):
                    segment_id = file_path.replace("_embedding.json", "").replace("segment_", "")

                    content = self.storage.get_stage_content(
                        "embedding", resource_id, version, file_path,
                        as_text=True, subfolder=subfolder
                    )
                    if content:
                        records[segment_id] = json.loads(content)
            return records

        except Exception as e:
            self.logger.error(f"Error getting embedding records: {e}")
            return {}