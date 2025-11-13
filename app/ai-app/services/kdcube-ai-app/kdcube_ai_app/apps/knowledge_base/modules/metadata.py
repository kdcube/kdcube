# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# knowledge_base/modules/metadata.py
"""
Updated segment metadata processor using the new generalized usage tracking system.
Processes only uncomputed segments and uses the centralized Usage class for tracking.
"""

import json
import logging
from typing import Dict, Any, List, Optional, Set, Tuple, Union
from datetime import datetime
from dataclasses import dataclass

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType, ProcessingMode
from kdcube_ai_app.apps.knowledge_base.storage import KnowledgeBaseStorage
from kdcube_ai_app.infra.llm.llm_data_model import Message, ModelRecord, wrap_batch_results
from kdcube_ai_app.infra.llm.streaming import llm_streaming_structured
from kdcube_ai_app.infra.llm.batching import create_batch, BatchMessage, BatchStatus

# Import the new usage system
from kdcube_ai_app.infra.accounting import SystemResource


@dataclass
class BatchJobInfo:
    """Information about a submitted batch job."""
    batch_id: str
    resource_id: str
    version: str
    segment_type: str
    segment_ids: List[str]
    model_name: str
    submitted_at: str
    status: str = "submitted"


class SegmentMetadataProcessor:
    """
    Processor for segment metadata that supports both streaming and batch processing.
    Only processes segments that haven't been computed yet.

    Four main APIs:
    1. stream() - Process immediately with streaming API
    2. batch_submit() - Submit batch job and return immediately
    3. batch_get_results() - Get results from previously submitted batch
    4. batch_process() - Submit batch, wait for completion, and return results
    """

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline):
        self.storage = storage
        self.project = project
        self.tenant = tenant
        self.pipeline = pipeline
        self.logger = logging.getLogger(self.__class__.__name__)

        # System prompt for metadata extraction
        self.system_prompt = """
You are an expert content analyzer specialized in extracting structured metadata from text segments.
You will receive inputs in this format:
- resource_name
- heading
- subheading  
- text

Your task: produce ONLY a valid JSON array of objects, each object exactly:
{ "key": "<class>", "value": "<value>" }
where:
• <class> is the metadata category (e.g. domain, topic, metric, tech, org, novel_concept, etc.)
• <value> is the corresponding concept or entity.
• The same key may appear multiple times with different values.

MANDATORY:
1. At least one object with `"key": "domain"`.
2. At least one object with `"key": "topic"`.

GUIDELINES:
– Choose keys that accurately reflect the content (e.g. "metric" only for true metrics, not generic counts).
– If in doubt on a new concept, use `"novel_concept"`.
– Do NOT invent extra fields—each object may only have `"key"` and `"value"`.
– Ensure every property follows `"property_name": "string_value"` syntax (no standalone tokens).
– Do not include any commentary, explanations, or trailing commas.

OUTPUT:
1. Build the full list of `{ "key": "...", "value": "..." }` pairs.
2. **Validate**: confirm your output is parseable by `json.loads(...)`.
3. Emit strictly the JSON array—nothing else.

Example:
Input:
Article Name: "Data Quality 101"
Heading: "Overview"
Subheading: "Why data quality matters"
Text: "Data quality is a metric assessing accuracy, completeness, and timeliness..."

Valid output:
[
    { "key": "domain", "value": "data quality" },
    { "key": "metric", "value": "accuracy" },
    { "key": "metric", "value": "completeness" },
    { "key": "metric", "value": "timeliness" }
]
"""

    def _get_processed_segment_ids(self, resource_id: str, version: str, segment_type: SegmentType) -> Set[str]:
        """Get set of segment IDs that already have metadata computed."""
        try:
            # List all metadata files for this segment type
            subfolder = segment_type.value
            files = self.storage.list_stage_files("metadata", resource_id, version, subfolder=subfolder)

            processed_ids = set()
            for file_path in files:
                if file_path.endswith("_metadata.json"):
                    try:
                        # Read and parse the metadata file content
                        content = self.storage.get_stage_content(
                            "metadata", resource_id, version, file_path,
                            subfolder=subfolder, as_text=True
                        )
                        if content:
                            metadata_data = json.loads(content)

                            # Check if processing was actually successful
                            is_successful = (
                                    metadata_data.get("entities") is not None and
                                    len(metadata_data.get("entities", [])) > 0
                            )

                            if is_successful:
                                # Extract segment ID from filename: segment_{id}_metadata.json
                                segment_id = file_path.replace("_metadata.json", "").replace("segment_", "")
                                processed_ids.add(segment_id)
                            else:
                                # Log failed metadata extractions for debugging
                                segment_id = file_path.replace("_metadata.json", "").replace("segment_", "")
                                error_msg = metadata_data.get("error_message", "Unknown error")
                                self.logger.debug(f"Skipping failed metadata for segment {segment_id}: {error_msg}")

                    except (json.JSONDecodeError, KeyError) as e:
                        self.logger.warning(f"Error parsing metadata file {file_path}: {e}")
                        continue

            self.logger.info(f"Found {len(processed_ids)} already processed {segment_type.value} segments with metadata")
            return processed_ids

        except Exception as e:
            self.logger.warning(f"Error checking processed segments: {e}")
            return set()

    def _get_unprocessed_segments(self, resource_id: str, version: str, segment_type: SegmentType) -> List[Dict[str, Any]]:
        """Get segments that need metadata processing."""
        # Get all segments of this type
        segmentation_module = self.pipeline.get_module("segmentation")
        if not segmentation_module:
            self.logger.error("Segmentation module not found")
            return []

        all_segments = segmentation_module.get_segments_by_type(resource_id, version, segment_type)

        # Get already processed segment IDs (those with metadata)
        processed_ids = self._get_processed_segment_ids(resource_id, version, segment_type)

        # Filter to only unprocessed segments
        unprocessed = []
        for segment in all_segments:
            segment_id = segment.get("segment_id")
            if segment_id and segment_id not in processed_ids:
                unprocessed.append(segment)

        self.logger.info(f"Found {len(unprocessed)} segments to process for {segment_type.value} "
                         f"(out of {len(all_segments)} total)")
        return unprocessed

    def _create_segment_metadata(self, segment: Dict[str, Any], entities: List[Dict[str, Any]],
                                 position: int, resource_id: str, version: str,
                                 segment_type: SegmentType, model_name: str) -> Dict[str, Any]:
        """Create unified metadata for a segment."""
        text = segment.get("text", "")
        words = text.split()
        sentences = self._split_into_sentences(text)
        metadata_info = segment.get("metadata", {})

        return {
            # Basic info
            "segment_id": segment.get("segment_id"),
            "segment_type": segment_type.value,
            "resource_id": resource_id,
            "version": version,

            # Content metadata (entities)
            "entities": entities,
            "entity_count": len(entities),
            "unique_entity_keys": list(set(e.get("key", "") for e in entities)),

            # Structural metadata
            "heading": metadata_info.get("heading", ""),
            "subheading": metadata_info.get("subheading", ""),
            "text_length": len(text),
            "word_count": len(words),
            "sentence_count": len(sentences),

            # Processing metadata
            "model_used": model_name,
            "processed_at": datetime.now().isoformat(),

            # RN
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:metadata:{segment_type.value}:{resource_id}:{version}:segment:{segment.get('segment_id', position)}"
        }

    def _save_segment_metadata(self, metadata: Dict[str, Any], resource_id: str, version: str, segment_type: SegmentType):
        """Save metadata for a single segment."""
        segment_id = metadata["segment_id"]
        filename = f"segment_{segment_id}_metadata.json"
        subfolder = segment_type.value

        content = json.dumps(metadata, indent=2, ensure_ascii=False)
        self.storage.save_stage_content("metadata", resource_id, version, filename, content, subfolder=subfolder)

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

    async def _extract_entities_streaming(self,
                                          segment: Dict[str, Any],
                                          resource_name: str,
                                          resource_id: str, version: str,
                                          model_record: ModelRecord,
                                          segment_type: SegmentType) -> Tuple[List[Dict[str, Any]], bool]:
        """Extract entities using streaming API and record usage."""
        metadata_info = segment.get("metadata", {})
        segment_id = segment.get("segment_id")

        input_content = f"""Resource Name: "{resource_name}"
Heading: "{metadata_info.get('heading', '')}"
Subheading: "{metadata_info.get('subheading', '')}"
Text: "{segment.get('text', '')}" """

        system_message = Message(role="system", content=self.system_prompt, cache_strategy="ephemeral")
        user_message = Message(role="user", content=input_content)
        messages = [system_message, user_message]
        seed_resource = self._create_system_resource_from_segment(segment, resource_id, version)

        from kdcube_ai_app.infra.accounting import with_accounting
        try:
            with with_accounting(
                    "kb.proc_pipeline.metadata",
                    metadata={
                        "segment_type": segment_type.value,
                        "processing_method": "streaming",
                        "model_used": model_record.systemName,
                        "resource_id": resource_id,
                        "version": version,
                        "segment_id": segment_id,
                    },
                    seed_system_resources=[seed_resource],
            ):
                streaming_result = await llm_streaming_structured(
                    model=model_record,
                    messages=messages,
                    message_id=f"{segment_id}_metadata",
                    parse_json=True,
                    temperature=0.3,
                    max_tokens=2000
                )

            if streaming_result.success:
                try:
                    entities = json.loads(streaming_result.content)
                    # Validate response format
                    if isinstance(entities, list):
                        validated_entities = []
                        for item in entities:
                            if isinstance(item, dict) and "key" in item and "value" in item:
                                validated_entities.append(item)
                            else:
                                self.logger.warning(f"Invalid entity format: {item}")
                        return validated_entities, True
                    else:
                        self.logger.warning(f"Unexpected LLM response format: {type(entities)}")
                        return [], False
                except json.JSONDecodeError:
                    self.logger.error(f"Failed to parse LLM response as JSON: {streaming_result.content}")
                    return [], False
            else:
                self.logger.error(f"Streaming failed for segment {segment_id}: {streaming_result.error_message}")
                return [], False

        except Exception as e:
            self.logger.error(f"Error extracting entities for segment {segment_id}: {e}")
            return [], False

    def _log_segment_processed(self, segment_id: str, success: bool, entity_count: int = 0, tokens_used: int = 0):
        """Log brief status line for processed segment."""
        status = "✅ SUCCESS" if success else "❌ FAILED"

        self.logger.info(
            f"{status} | Segment: {segment_id} | "
            # f"Tokens: {tokens_used} | "
            f"Entities: {entity_count}"
        )

    async def stream(self, resource_id: str, version: str, segment_type: SegmentType,
                     model_record: ModelRecord, resource_name: str = None) -> Dict[str, Any]:
        """
        Process segments using streaming API. Results and usage are stored immediately per segment.
        """
        self.logger.info(f"Starting streaming metadata extraction for {resource_id} v{version} {segment_type.value}")

        # Get unprocessed segments
        unprocessed_segments = self._get_unprocessed_segments(resource_id, version, segment_type)

        if not unprocessed_segments:
            self.logger.info("No unprocessed segments found")
            return {
                "processed_count": 0,
                "skipped_count": 0,
                "total_segments": 0,
                "method": "streaming"
            }

        resource_name = resource_name or resource_id
        processed_count = 0

        for i, segment in enumerate(unprocessed_segments):
            segment_id = segment.get('segment_id')
            self.logger.debug(f"Processing segment {i+1}/{len(unprocessed_segments)}: {segment_id}")

            # Extract entities and record usage
            entities, success = await self._extract_entities_streaming(
                segment, resource_name, resource_id, version, model_record, segment_type
            )

            # Log processing result
            self._log_segment_processed(segment_id, success, len(entities))

            if success:
                # Create unified metadata
                metadata = self._create_segment_metadata(
                    segment, entities, i, resource_id, version, segment_type, model_record.systemName
                )

                # Save metadata immediately
                self._save_segment_metadata(metadata, resource_id, version, segment_type)
                processed_count += 1

        return {
            "processed_count": processed_count,
            "skipped_count": len(unprocessed_segments) - processed_count,
            "total_segments": len(unprocessed_segments),
            "method": "streaming"
        }

    async def batch_submit(self, resource_id: str, version: str, segment_type: SegmentType,
                           model_record: ModelRecord, resource_name: str = None) -> BatchJobInfo:
        """
        Submit batch job for metadata extraction. Returns batch job info.
        """
        self.logger.info(f"Submitting batch job for {resource_id} v{version} {segment_type.value}")

        # Get unprocessed segments
        unprocessed_segments = self._get_unprocessed_segments(resource_id, version, segment_type)

        if not unprocessed_segments:
            raise ValueError("No unprocessed segments found")

        resource_name = resource_name or resource_id

        # Create batch messages
        batch_messages = []
        segment_ids = []

        for i, segment in enumerate(unprocessed_segments):
            metadata_info = segment.get("metadata", {})

            input_content = f"""Resource Name: "{resource_name}"
Heading: "{metadata_info.get('heading', '')}"
Subheading: "{metadata_info.get('subheading', '')}"
Text: "{segment.get('text', '')}" """

            system_message = Message(role="system", content=self.system_prompt, cache_strategy="ephemeral")
            user_message = Message(role="user", content=input_content)

            batch_message = BatchMessage(
                id=f"{resource_id}_{version}_{segment_type.value}_{segment.get('segment_id')}",
                messages=[system_message, user_message],
                max_tokens=2000
            )

            batch_messages.append(batch_message)
            segment_ids.append(segment.get('segment_id'))

        # Create and submit batch
        batch = await create_batch(model_record, messages=batch_messages)
        await batch.create()

        # Create batch job info
        batch_info = BatchJobInfo(
            batch_id=batch.id,
            resource_id=resource_id,
            version=version,
            segment_type=segment_type.value,
            segment_ids=segment_ids,
            model_name=model_record.systemName,
            submitted_at=datetime.now().isoformat()
        )

        # Save batch info to storage
        self._save_batch_info(batch_info)

        self.logger.info(f"Submitted batch {batch.id} with {len(batch_messages)} messages")
        return batch_info

    async def batch_process(self,
                            resource_id: str,
                            version: str,
                            segment_type: SegmentType,
                            model_record: ModelRecord,
                            resource_name: str = None,
                            polling_interval: float = 60.0,
                            timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        """
        Submit batch job, wait for completion, and store results. One-stop method.

        Args:
            polling_interval: Time between status checks in seconds (default: 60s)
            timeout_seconds: Maximum waiting time in seconds (None for no timeout)
        """
        self.logger.info(f"Starting batch processing for {resource_id} v{version} {segment_type.value}")

        # Submit batch job
        batch_info = await self.batch_submit(resource_id, version, segment_type, model_record, resource_name)

        # Create batch object for polling
        batch = await create_batch(model_record, batch_id=batch_info.batch_id)

        # Wait for completion with polling
        final_status = await batch.start_polling(
            interval_seconds=polling_interval,
            timeout_seconds=timeout_seconds,
            callback=lambda status: self.logger.info(f"Batch {batch_info.batch_id} status: {status}")
        )

        # Process and return results
        if final_status == BatchStatus.succeeded:
            return await self._process_batch_results(batch_info, batch)
        else:
            # Update batch info with failure
            batch_info.status = "failed"
            self._save_batch_info(batch_info)

            return {
                "status": final_status.value,
                "processed_count": 0,
                "error": batch.fail_reason or "Batch failed",
                "method": "batch"
            }

    async def batch_get_results(self, batch_id_or_info: Union[str, BatchJobInfo]) -> Dict[str, Any]:
        """
        Get results from completed batch job and store metadata.

        Args:
            batch_id_or_info: Either batch ID string or BatchJobInfo object
        """
        if isinstance(batch_id_or_info, str):
            batch_id = batch_id_or_info
            self.logger.info(f"Getting results for batch {batch_id}")

            # Load batch info
            batch_info = self._load_batch_info(batch_id)
            if not batch_info:
                raise ValueError(f"Batch info not found for {batch_id}. "
                                 "Consider using batch_get_results_with_info() if you have the BatchJobInfo.")
        else:
            batch_info = batch_id_or_info
            batch_id = batch_info.batch_id
            self.logger.info(f"Getting results for batch {batch_id} (using provided info)")

        # Create model record to get batch
        model_record = ModelRecord(systemName=batch_info.model_name, provider=None)  # Provider will be inferred
        batch = await create_batch(model_record, batch_id=batch_id)

        # Update batch status
        await batch.update_status()

        if batch.status != BatchStatus.succeeded:
            return {
                "status": batch.status.value,
                "processed_count": 0,
                "error": batch.fail_reason
            }

        return await self._process_batch_results(batch_info, batch)

    async def _process_batch_results(self, batch_info: BatchJobInfo, batch) -> Dict[str, Any]:
        """Process and store results from a completed batch."""
        processed_count = 0
        segment_type = SegmentType(batch_info.segment_type)

        # Get segments for position mapping
        unprocessed_segments = self._get_unprocessed_segments(
            batch_info.resource_id, batch_info.version, segment_type
        )
        segment_map = {seg.get('segment_id'): seg for seg in unprocessed_segments}

        # Wrap batch results for easier processing
        provider = batch_info.model_name.startswith('gpt') and 'openai' or 'anthropic'
        wrapped_results = wrap_batch_results(batch.messages or [], provider)

        for i, result in enumerate(wrapped_results):
            segment_id = batch_info.segment_ids[i] if i < len(batch_info.segment_ids) else None

            if not segment_id or segment_id not in segment_map:
                continue

            segment = segment_map[segment_id]

            # Create system resource for usage tracking
            seed_resource = self._create_system_resource_from_segment(
                segment, batch_info.resource_id, batch_info.version
            )
            # TODO: add with_accounting context manager to track usage

            if result.success:
                try:
                    # Parse entities from result
                    entities = json.loads(result.text_content) if result.text_content else []
                    if not isinstance(entities, list):
                        entities = []

                    # Validate entities
                    validated_entities = []
                    for item in entities:
                        if isinstance(item, dict) and "key" in item and "value" in item:
                            validated_entities.append(item)

                    # Create and save metadata
                    metadata = self._create_segment_metadata(
                        segment, validated_entities, i, batch_info.resource_id,
                        batch_info.version, segment_type, batch_info.model_name
                    )

                    self._save_segment_metadata(metadata, batch_info.resource_id, batch_info.version, segment_type)
                    processed_count += 1

                    # Log processing result
                    tokens_used = result.usage.total_tokens if result.usage else 0
                    self._log_segment_processed(segment_id, True, len(validated_entities), tokens_used)

                except Exception as e:
                    self.logger.error(f"Error processing batch result for segment {segment_id}: {e}")
                    self._log_segment_processed(segment_id, False, 0, 0)
            else:
                tokens_used = result.usage.total_tokens if result.usage else 0
                self._log_segment_processed(segment_id, False, 0, tokens_used)

        # Update batch info status
        batch_info.status = "completed"
        self._save_batch_info(batch_info)

        return {
            "status": "succeeded",
            "processed_count": processed_count,
            "total_messages": len(wrapped_results),
            "method": "batch"
        }

    def _save_batch_info(self, batch_info: BatchJobInfo):
        """Save batch job info to storage."""
        filename = f"batch_{batch_info.batch_id}.json"
        content = json.dumps({
            "batch_id": batch_info.batch_id,
            "resource_id": batch_info.resource_id,
            "version": batch_info.version,
            "segment_type": batch_info.segment_type,
            "segment_ids": batch_info.segment_ids,
            "model_name": batch_info.model_name,
            "submitted_at": batch_info.submitted_at,
            "status": batch_info.status
        }, indent=2, ensure_ascii=False)

        self.storage.save_stage_content("metadata", batch_info.resource_id, batch_info.version,
                                        filename, content, subfolder="batches")

    def _load_batch_info(self, batch_id: str) -> Optional[BatchJobInfo]:
        """Load batch job info from storage."""
        try:
            # Try to find batch info by searching through storage
            content = self.storage.get_stage_content(
                "metadata", "*", "*", f"batch_{batch_id}.json",
                as_text=True, subfolder="batches"
            )
            if content:
                data = json.loads(content)
                return BatchJobInfo(
                    batch_id=data["batch_id"],
                    resource_id=data["resource_id"],
                    version=data["version"],
                    segment_type=data["segment_type"],
                    segment_ids=data["segment_ids"],
                    model_name=data["model_name"],
                    submitted_at=data["submitted_at"],
                    status=data.get("status", "submitted")
                )
            return None

        except Exception as e:
            self.logger.error(f"Error loading batch info: {e}")
            return None

    # Helper methods
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        import re
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip()]


class MetadataModule(ProcessingModule):
    """
    Processing module for segment metadata extraction.
    Uses SegmentMetadataProcessor internally for both streaming and batch processing.
    """

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline,
                 processing_mode: ProcessingMode = ProcessingMode.FULL_INDEXING,
                 use_batch: bool = False,
                 batch_polling_interval: float = 60.0,
                 batch_timeout_seconds: Optional[float] = None):
        """
        Initialize metadata module.

        Args:
            processing_mode: FULL_INDEXING or RETRIEVAL_ONLY
            use_batch: Whether to use batch processing by default
            batch_polling_interval: Polling interval for batch processing
            batch_timeout_seconds: Timeout for batch processing
        """
        super().__init__(storage, project, tenant, pipeline)
        self.processing_mode = processing_mode
        self.use_batch = use_batch
        self.batch_polling_interval = batch_polling_interval
        self.batch_timeout_seconds = batch_timeout_seconds

        # Create the processor instance
        self.processor = SegmentMetadataProcessor(storage, project, tenant, pipeline)

    @property
    def stage_name(self) -> str:
        return "metadata"

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
        """Generate comprehensive metadata using streaming or batch processing."""

        # Check if metadata already exists
        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Metadata already exists for {resource_id} v{version}, skipping")
            return self.get_results(resource_id, version) or {}

        model_record: Optional[Union[dict, ModelRecord]] = kwargs.get("model_record")
        if not model_record:
            raise ValueError("model_record is required for metadata processing")
        if not isinstance(model_record, ModelRecord):
            model_record = ModelRecord(**model_record)
        if not model_record:
            raise ValueError("model_record is required for metadata processing")

        resource_name = kwargs.get("resource_name", resource_id)
        use_batch = kwargs.get("use_batch", self.use_batch)

        self.logger.info(f"Generating metadata for {resource_id} v{version} "
                         f"(mode: {self.processing_mode.value}, method: {'batch' if use_batch else 'stream'})")

        # Get enabled segment types
        enabled_types = self.get_enabled_segment_types()

        # Process each segment type
        all_results = {}
        total_processed = 0

        for segment_type in enabled_types:
            self.logger.info(f"Processing metadata for {segment_type.value} segments")

            if use_batch:
                # Use batch processing
                result = await self.processor.batch_process(
                    resource_id=resource_id,
                    version=version,
                    segment_type=segment_type,
                    model_record=model_record,
                    resource_name=resource_name,
                    polling_interval=self.batch_polling_interval,
                    timeout_seconds=self.batch_timeout_seconds
                )
            else:
                # Use streaming processing
                result = await self.processor.stream(
                    resource_id=resource_id,
                    version=version,
                    segment_type=segment_type,
                    model_record=model_record,
                    resource_name=resource_name
                )

            all_results[segment_type.value] = result
            total_processed += result.get("processed_count", 0)

        # Create overall results
        overall_results = {
            "resource_id": resource_id,
            "version": version,
            "processing_mode": self.processing_mode.value,
            "enabled_types": [t.value for t in enabled_types],
            "results_by_type": all_results,
            "total_segments_processed": total_processed,
            "processing_method": "batch" if use_batch else "stream",
            "generation_timestamp": datetime.now().isoformat(),
            "llm_model_used": model_record.systemName,
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
        }

        # Save overall results
        self.save_results(resource_id, version, overall_results)

        # Log operation
        self.log_operation("metadata_complete", resource_id, {
            "version": version,
            "processing_mode": self.processing_mode.value,
            "enabled_types": [t.value for t in enabled_types],
            "total_segments_processed": total_processed,
            "processing_method": "batch" if use_batch else "stream",
            "llm_model": model_record.systemName
        })

        self.logger.info(f"Generated metadata for {total_processed} segments "
                         f"using {'batch' if use_batch else 'stream'} processing for {resource_id} v{version}")
        return overall_results

    # Expose processor methods for advanced usage
    async def stream_segments(self, resource_id: str, version: str, segment_type: SegmentType,
                              model_record: ModelRecord, resource_name: str = None) -> Dict[str, Any]:
        """Stream process segments of a specific type."""
        return await self.processor.stream(resource_id, version, segment_type, model_record, resource_name)

    async def batch_submit_segments(self, resource_id: str, version: str, segment_type: SegmentType,
                                    model_record: ModelRecord, resource_name: str = None) -> BatchJobInfo:
        """Submit batch job for segments of a specific type."""
        return await self.processor.batch_submit(resource_id, version, segment_type, model_record, resource_name)

    async def batch_process_segments(self, resource_id: str, version: str, segment_type: SegmentType,
                                     model_record: ModelRecord, resource_name: str = None,
                                     polling_interval: float = None, timeout_seconds: float = None) -> Dict[str, Any]:
        """Submit and wait for batch processing of segments of a specific type."""
        return await self.processor.batch_process(
            resource_id, version, segment_type, model_record, resource_name,
            polling_interval or self.batch_polling_interval,
            timeout_seconds or self.batch_timeout_seconds
        )

    async def get_batch_results(self, batch_id_or_info: Union[str, BatchJobInfo]) -> Dict[str, Any]:
        """Get results from a completed batch job."""
        return await self.processor.batch_get_results(batch_id_or_info)

    def get_processing_status(self, resource_id: str, version: str, segment_type: SegmentType) -> Dict[str, Any]:
        """Get detailed processing status."""
        try:
            # Get all segment counts
            segmentation_module = self.pipeline.get_module("segmentation")
            if not segmentation_module:
                return {"error": "Segmentation module not found"}

            all_segments = segmentation_module.get_segments_by_type(resource_id, version, segment_type)
            total_segments = len(all_segments)

            # Get processed counts
            metadata_ids = self.processor._get_processed_segment_ids(resource_id, version, segment_type)
            completed_segments = len(metadata_ids)
            unprocessed_segments = total_segments - completed_segments

            return {
                "resource_id": resource_id,
                "version": version,
                "segment_type": segment_type.value,
                "total_segments": total_segments,
                "completed_segments": completed_segments,
                "unprocessed_segments": unprocessed_segments,
                "completion_rate": completed_segments / total_segments if total_segments > 0 else 0
            }

        except Exception as e:
            self.logger.error(f"Error getting processing status: {e}")
            return {"error": str(e)}

    def get_segment_entities(self, resource_id: str, version: str, segment_id: str, segment_type: SegmentType) -> List[Dict[str, Any]]:
        """Get extracted entities for a specific segment."""
        try:
            subfolder = segment_type.value
            filename = f"segment_{segment_id}_metadata.json"
            content = self.storage.get_stage_content(
                self.stage_name, resource_id, version, filename,
                as_text=True, subfolder=subfolder
            )
            if content:
                metadata = json.loads(content)
                return metadata.get("entities", [])
            return []
        except Exception as e:
            self.logger.error(f"Error getting segment entities: {e}")
            return []

    def get_resource_records(self,
                            resource_id: str,
                            version: str,
                            segment_type: SegmentType) -> Dict[str, List[float]]:
        """Get records for a resource and segment type."""
        try:
            subfolder = f"{segment_type.value}"
            files = self.storage.list_stage_files("metadata", resource_id, version, subfolder=subfolder)

            records = {}
            for file_path in files:
                if file_path.endswith("_metadata.json"):
                    segment_id = file_path.replace("_metadata.json", "").replace("segment_", "")

                    content = self.storage.get_stage_content(
                        "metadata", resource_id, version, file_path,
                        as_text=True, subfolder=subfolder
                    )
                    if content:
                        records[segment_id] = json.loads(content)
            return records

        except Exception as e:
            self.logger.error(f"Error getting metadata records: {e}")
            return {}