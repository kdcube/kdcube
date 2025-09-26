# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
kb_connector.py

Final KnowledgeBaseConnector implementation using the actual KnowledgeBaseDB and KnowledgeBaseSearch.
"""

import json
import logging
import traceback
from datetime import datetime

from typing import Dict, Any, List, Optional, Generator
from dataclasses import dataclass

from kdcube_ai_app.apps.chat.reg import EMBEDDERS

from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType

# Import your actual database classes
from kdcube_ai_app.apps.knowledge_base.db.providers.knowledge_base_db import KnowledgeBaseDB
from kdcube_ai_app.apps.knowledge_base.db.providers.knowledge_base_search import KnowledgeBaseSearch

from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord

# Import data models
from kdcube_ai_app.apps.knowledge_base.db.data_models import (
    DataSource, EntityItem, NavigationSearchResult, HybridSearchParams, ContentHash
)
from kdcube_ai_app.infra.embedding.embedding import get_embedding

logger = logging.getLogger(__name__)


class KnowledgeBaseConnector:
    """
    Lightweight connector for Knowledge Base operations using your existing DB classes.
    Provides high-level interface for data loading and search with proper navigation.
    """

    def __init__(self,
                 tenant: str,
                 schema_name: str,
                 project_name: str,
                 embedding_model: Optional[ModelRecord] = None,
                 self_hosted_serving_endpoint: str = None,
                 system_schema_name: Optional[str] = None):
        """
        Initialize connector with your existing database classes.

        Args:
            schema_name: Database schema name
            project_name: Project name for RN generation
            system_schema_name: Optional system schema name
        """
        self.tenant = tenant
        self.schema_name = schema_name
        self.project_name = project_name
        self.system_schema_name = system_schema_name
        self.embedding_model = embedding_model
        self.self_hosted_serving_endpoint = self_hosted_serving_endpoint

        # Initialize your existing database classes
        self.kb_db = KnowledgeBaseDB(tenant, schema_name, system_schema_name)
        self.kb_search = KnowledgeBaseSearch(tenant, schema_name, system_schema_name)

    def upsert_datasource_version(self,
                                  kb: 'KnowledgeBase',
                                  resource_id: str,
                                  version: Optional[str] = None) -> Dict[str, Any]:
        """
        Upsert datasource version from Knowledge Base.

        Args:
            kb: KnowledgeBase instance
            resource_id: Resource identifier
            version: Version (None for latest)

        Returns:
            Operation result
        """
        logger.info(f"Upserting datasource {resource_id} version {version}")

        # Get resource metadata from KB
        resource_metadata = kb.get_resource(resource_id)
        if not resource_metadata:
            raise ValueError(f"Resource {resource_id} not found in Knowledge Base")

        if version is None:
            version = resource_metadata.version

        # Build datasource record
        datasource_data = {
            "id": resource_metadata.id,
            "version": int(version),
            "provider": resource_metadata.provider,
            "rn": resource_metadata.rn,
            "source_type": resource_metadata.source_type,
            "title": resource_metadata.title,
            "uri": resource_metadata.uri,
            "system_uri": resource_metadata.ef_uri,
            "metadata": resource_metadata.light(),
            "status": "completed",
            "segment_count": 0  # Will be updated when segments are loaded
        }

        # Use KnowledgeBaseDB to upsert
        result = self.kb_db.upsert_datasource(datasource_data)

        logger.info(f"Upserted datasource {resource_id} v{version}: {result['operation']}")
        return result

    def delete_datasource(self,
                          resource_id: str,
                          version: Optional[str] = None) -> Dict[str, Any]:
        """
        Delete datasource entirely or specific version.

        Args:
            resource_id: Resource identifier
            version: Specific version to delete (None for all versions)

        Returns:
            Deletion statistics
        """
        logger.info(f"Deleting datasource {resource_id}" + (f" version {version}" if version else " (all versions)"))

        version_int = int(version) if version else None
        result = self.kb_db.delete_datasource_and_segments(resource_id, version_int)

        logger.info(f"Deleted datasource {resource_id}: {result}")
        return result

    def batch_upsert_retrieval_segments(self,
                                        kb: 'KnowledgeBase',
                                        resource_id: str,
                                        version: Optional[str] = None) -> Dict[str, Any]:
        """
        Batch upsert retrieval segments with full lineage and metadata.

        Args:
            kb: KnowledgeBase instance
            resource_id: Resource identifier
            version: Version (None for latest)

        Returns:
            Operation statistics
        """
        logger.info(f"Batch upserting retrieval segments for {resource_id} v{version}")

        # Get version if not specified
        if version is None:
            resource_metadata = kb.get_resource(resource_id)
            if not resource_metadata:
                raise ValueError(f"Resource {resource_id} not found")
            version = resource_metadata.version

        # Build enhanced segments with full lineage
        segments_data = self._build_enhanced_segments(kb, resource_id, version)

        if not segments_data:
            logger.warning(f"No segments found for {resource_id} v{version}")
            return {"segments_upserted": 0, "embeddings_loaded": 0, "metadata_loaded": 0}

        # Use KnowledgeBaseDB for transactional batch upsert
        result = self.kb_db.batch_upsert_retrieval_segments(
            resource_id=resource_id,
            version=int(version),
            segments_data=segments_data
        )

        logger.info(f"Batch upserted {result['segments_upserted']} segments for {resource_id} v{version}")
        return result

    def _build_enhanced_segments(self,
                                 kb: 'KnowledgeBase',
                                 resource_id: str,
                                 version: str) -> List[Dict[str, Any]]:
        """
        Build enhanced segments with complete lineage for search navigation.

        Args:
            kb: KnowledgeBase instance
            resource_id: Resource identifier
            version: Version

        Returns:
            List of enhanced segment dictionaries with proper lineage
        """
        # Get retrieval segments
        segments = kb.get_segmentation_module().get_retrieval_segments(resource_id, version)
        if not segments:
            return []

        # Get base segments for navigation data
        base_segments = kb.get_base_segments(resource_id, version)
        base_lookup = {seg['guid']: seg for seg in base_segments}

        all_metadata_records = dict()
        try:
            metadata_module = kb.get_metadata_module()
            if metadata_module:
                all_metadata_records = metadata_module.get_resource_records(resource_id, version, SegmentType.RETRIEVAL)
        except Exception as e:
            logger.warning(f"Could not load metadata records for {resource_id}: {e}")

        all_embedding_records = dict()
        try:
            embedding_module = kb.get_embedding_module()
            if embedding_module:
                all_embedding_records = embedding_module.get_resource_records(resource_id, version,
                                                                              SegmentType.RETRIEVAL)
        except Exception as e:
            logger.warning(f"Could not load embedding records for {resource_id}: {e}")

        # Get resource metadata for RNs
        resource_metadata = kb.get_resource(resource_id)
        raw_rn = resource_metadata.rn if resource_metadata else f"ef:{self.tenant}:{self.project_name}:knowledge_base:raw:{resource_id}:{version}"
        provider = resource_metadata.provider if resource_metadata else "provider_unknown"

        # Get extraction RNs
        extraction_module = kb.get_extraction_module()
        md_extraction_rn = None
        if extraction_module:
            md_extraction_rn = next(iter(
                [fi["rn"] for fi in (kb.storage.get_extraction_results(resource_id, version) or []) if
                 fi["content_file"].endswith(".md")]), None)

        enhanced_segments = []

        for segment in segments:
            # robust id resolution
            segment_id = (segment.get("segment_id")
                          or segment.get("id"))
            if not segment_id:
                continue

            # Get segment metadata and base lineage
            segment_metadata = segment.get("metadata", {}) or {}
            base_guids = segment_metadata.get("base_segment_guids", []) or []

            segment_base_segments = []
            extraction_rns = set()

            # Get metadata, enrichment, embedding records
            metadata_record = all_metadata_records.get(segment_id, {})
            embedding_record = all_embedding_records.get(segment_id, {})

            for guid in base_guids:
                if guid in base_lookup:
                    base_seg = base_lookup[guid]
                    base_segment_extraction_rns = base_seg.get('extracted_data_rns', []) or []
                    extraction_rns.update(base_segment_extraction_rns)
                    segment_base_segments.append({
                        "guid": base_seg['guid'],
                        # "text": base_seg['text'],
                        "start_line_num": base_seg['start_line_num'],
                        "end_line_num": base_seg['end_line_num'],
                        "start_position": base_seg['start_position'],
                        "end_position": base_seg['end_position'],
                        "rn": base_seg['rn'],
                        # "extracted_data_rns": base_segment_extraction_rns,
                        "heading": base_seg.get('heading', ''),
                        "subheading": base_seg.get('subheading', ''),
                    })

            extraction_rns = list(extraction_rns)

            lineage = {
                "resource_id": resource_id,
                "version": version,
                "provider": provider,
                "raw": { "rn": raw_rn },
                "extraction": { "related_rns": extraction_rns, "rn": md_extraction_rn },
                "segmentation": {
                    "rn": segment.get("rn"),
                    # "segment_id": segment_id,
                    # "base_segments": segment_base_segments
                },
                "embedding": { "rn": embedding_record.get("rn") }
            }
            if metadata_record:
                lineage["metadata"] = { "rn": metadata_record.get("rn") }

            # -------- Apply enrichment if available --------
            # Start from original retrieval text
            content = segment.get("text", "") or ""
            segment_meta = segment.get("metadata") or {}
            title = segment_meta.get("heading") or ""
            if not title or title == "Untitled":
                title = segment_meta.get("subheading", "") or "" # if not title: title = segment.get("metadata", {}) or {}.get("subheading", "") or ""

            extensions = {}

            enrichment = self._read_segment_enrichment(kb, resource_id, version, segment_id)

            enrichment_used = False
            enrichment_rn = None
            connector_summary = segment.get("summary", "") or ""

            if enrichment and enrichment.get("success"):
                enrichment_rn = enrichment.get("rn")
                enriched_text = enrichment.get("retrieval_doc") or content
                title = enrichment.get("title") or title
                if enriched_text:
                    content = enriched_text

                md = enrichment.get("metadata") or {}
                connector_summary = md.get("summary", "") or connector_summary

                extensions["enrichment"] = {
                    "rn": enrichment.get("rn"),
                    **{"metadata": enrichment.get("metadata") or {}},
                    "is_table": enrichment.get("is_table", False),
                    "is_image": enrichment.get("is_image", False),
                }
                enrichment_used = True
            else:
                extensions["enrichment"] = {}
            extensions["datasource"] = {
                "rn": resource_metadata.rn if resource_metadata else None,
                "title": resource_metadata.title if resource_metadata else None,
                "uri": resource_metadata.uri if resource_metadata else None,
                "provider": provider,
                "expiration": resource_metadata.expiration if resource_metadata else None,
                # "metadata": resource_metadata.light() if resource_metadata else {},
                "source_type": resource_metadata.source_type if resource_metadata else None,
                "mime": resource_metadata.mime if resource_metadata else None,
                "published_time_iso": (resource_metadata.metadata or {}).get("published_time_iso") if resource_metadata else None,
                "modified_time_iso": (resource_metadata.metadata or {}).get("modified_time_iso") if resource_metadata else None,
            }

            if enrichment_used and enrichment_rn:
                lineage["enrichment"] = {"rn": enrichment_rn}

            # Entities from metadata stage, as you already do
            entities = metadata_record.get("entities", [])
            if not isinstance(entities, list):
                entities = []

            enhanced_segment = {
                "id": segment_id,
                "version": int(version),
                "resource_id": resource_id,
                "rn": segment.get("rn"),
                "title": title,
                "content": content,                     # <- GLUED TEXT
                "summary": connector_summary,           # <- prefer enrichment summary
                "entities": entities,
                "tags": [],
                "word_count": len(content.split()) if content else 0,
                "sentence_count": (content.count('.') + content.count('!') + content.count('?')) if content else 0,
                "processed_at": metadata_record.get("processed_at"),
                "embedding": embedding_record.get("embedding"),
                "lineage": lineage,
                "extensions": extensions,                # <- enrichment stored here
                "provider": provider
            }

            # (optional) add enrichment RN to lineage if you want to navigate to it later
            if "enrichment" in extensions:
                lineage["enrichment"] = { "rn": extensions["enrichment"]["rn"] }

            enhanced_segments.append(enhanced_segment)

        return enhanced_segments

    def list_datasources(self, source_type: Optional[str] = None) -> List[DataSource]:
        """
        List all datasources, optionally filtered by source type.

        Args:
            source_type: Filter by source type (None for all)

        Returns:
            List of DataSource objects
        """
        return self.kb_db.list_datasources(source_type=source_type)

    @staticmethod
    def _extract_heading_from_content(content: str) -> Optional[str]:
        """
        Extract the first heading from content for display purposes.
        Since content includes headings, we can parse them for search results.
        """
        if not content:
            return None

        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            # Look for markdown headings
            if line.startswith('#'):
                # Remove markdown syntax and return clean heading
                return line.lstrip('#').strip()
            # Look for other heading patterns if needed

        # Fallback: use first sentence or truncated content
        first_sentence = content.split('.')[0].strip()
        if len(first_sentence) > 100:
            return first_sentence[:100] + "..."
        return first_sentence

    def hybrid_search(self,
                      query: str,
                      resource_id: Optional[str] = None,
                      top_k: int = 5,
                      relevance_threshold: float = 0.0) -> List[NavigationSearchResult]:

        """
        Perform hybrid search and return properly formatted results with navigation.

        Args:
            query: Search query
            resource_id: Optional resource filter
            top_k: Maximum results to return
            relevance_threshold: Minimum relevance score

        Returns:
            List of NavigationSearchResult objects with proper backtrack navigation
        """
        if not query.strip():
            return []

        logger.debug(f"Performing hybrid search for: '{query}'")

        search_results = self.kb_search.hybrid_search(
            query=query,
            resource_id=resource_id,
            top_k=top_k,
            relevance_threshold=relevance_threshold
        )

        return self._format_search_results(query, search_results)

    def advanced_search(self, params: HybridSearchParams) -> List[NavigationSearchResult]:
        """
        Advanced search using full HybridSearchParams - now using SQL-based implementation.
        This replaces the old KnowledgeBaseSearch module with pure SQL implementation.

        Args:
            params: Complete search parameters with all filters

        Returns:
            List of NavigationSearchResult objects with proper backtrack navigation
        """
        logger.debug(f"Performing advanced search: query='{params.query}', "
                     f"resources={params.resource_ids}, entities={len(params.entity_filters or [])}")

        # Use the enhanced SQL-based search
        search_results = self.kb_search.search(params)

        # Format results for navigation
        query = params.query or "semantic_search"
        return self._format_search_results(query, search_results)

    def pipeline_search(self, params: HybridSearchParams) -> List[NavigationSearchResult]:

        search_results = self.kb_search.hybrid_pipeline_search(params)
        query = params.query or "semantic_search"
        return self._format_search_results(query, search_results)

    def entity_search(self,
                      entities: List[EntityItem],
                      match_all: bool = False,
                      resource_ids: Optional[List[str]] = None,
                      top_k: int = 10) -> List[NavigationSearchResult]:
        """
        Pure entity-based search using SQL JSONB operators.

        Args:
            entities: List of entities to search for
            match_all: If True, segment must contain all entities
            resource_ids: Optional resource filter
            top_k: Maximum results

        Returns:
            List of search results
        """
        logger.debug(f"Performing entity search: entities={[(e.key, e.value) for e in entities]}")

        search_results = self.kb_search.entity_search(
            entity_filters=entities,
            match_all=match_all,
            resource_ids=resource_ids,
            top_k=top_k
        )

        return self._format_search_results("entity_search", search_results)

    def get_embedding(self, query: str):
        return get_embedding(
            model=self.embedding_model,
            text=query,
            self_hosted_serving_endpoint=self.self_hosted_serving_endpoint
        )

    def semantic_search(self,
                        query: str,
                        distance_type: str = "cosine",
                        resource_ids: Optional[List[str]] = None,
                        min_similarity: float = 0.0,
                        top_k: int = 10) -> List[NavigationSearchResult]:
        """
        Pure semantic search using vector similarity.

        Args:
            embedding: Query embedding vector
            distance_type: Distance metric
            resource_ids: Optional resource filter
            min_similarity: Minimum similarity threshold
            top_k: Maximum results

        Returns:
            List of search results
        """
        logger.debug(f"Performing semantic search with {distance_type} distance")

        from kdcube_ai_app.infra.accounting import with_accounting
        with with_accounting("kb.semantic_search", metadata={"query": query, "phase": "user_query_embedding"}):
            query_embedding = self.get_embedding(query=query)
        search_results = self.kb_search.semantic_search_only(
            embedding=query_embedding,
            distance_type=distance_type,
            resource_ids=resource_ids,
            min_similarity=min_similarity,
            top_k=top_k
        )

        return self._format_search_results("semantic_search", search_results)

    def _format_search_results(self, query: str, search_results: List[Dict[str, Any]]) -> List[NavigationSearchResult]:
        """
        Format search results with proper backtrack navigation.

        Args:
            query: Original query
            search_results: Raw search results from database

        Returns:
            List of NavigationSearchResult objects
        """
        formatted_results = []
        for result in search_results:
            try:
                backtrack = self._build_search_backtrack(query, result)

                # Extract heading from content for display
                content = result.get("content", "")
                heading = self._extract_heading_from_content(content) or result.get("title", "")

                search_result = NavigationSearchResult(
                    query=query,
                    relevance_score=result.get("relevance_score", 0.0),
                    heading=heading,
                    subheading="",  # No subheading in composite segments
                    backtrack=backtrack,
                    content=content
                )

                formatted_results.append(search_result)

            except Exception as e:
                logger.error(f"Error formatting search result: {e}")
                continue

        logger.debug(f"Formatted {len(formatted_results)} search results")
        return formatted_results

    # Deprecated method - redirect to new implementation
    def search_with_advanced_params(self, params: HybridSearchParams) -> List[NavigationSearchResult]:
        """
        DEPRECATED: Use advanced_search() instead.
        Redirects to the new SQL-based implementation.
        """
        logger.warning("search_with_advanced_params is deprecated, use advanced_search() instead")
        return self.advanced_search(params)

    def _build_search_backtrack(self, query: str, search_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build proper backtrack navigation from search result lineage.

        Args:
            query: Original search query
            search_result: Raw search result from database

        Returns:
            Properly formatted backtrack dict matching expected format
        """
        lineage = search_result.get("lineage", {})
        query_lower = (query or "").lower()

        # Extract lineage components (now namespaced)
        raw_info = lineage.get("raw", {})
        extraction_info = lineage.get("extraction", {})
        segmentation_info = lineage.get("segmentation", {})
        enrichment_info = lineage.get("enrichment", {})
        base_segments = segmentation_info.get("base_segments", [])

        # Build navigation from base segments
        navigation = []
        raw_citations = []

        for base_seg in base_segments:
            # Check if query matches this base segment
            base_text = base_seg.get("text", "")
            base_citations = []
            if query_lower and query_lower in base_text.lower():
                base_citations.append(query)
                raw_citations.append(query)

            navigation_item = {
                "start_line": base_seg.get("start_line_num", 0),
                "end_line": base_seg.get("end_line_num", 0),
                "start_pos": base_seg.get("start_position", 0),
                "end_pos": base_seg.get("end_position", 0),
                "citations": base_citations,
                # "text": base_text,
                # Base segments still have heading/subheading for navigation
                "heading": base_seg.get("heading", ""),
                "subheading": base_seg.get("subheading", "")
            }
            navigation.append(navigation_item)

        # Build backtrack structure exactly as specified
        backtrack = {
            "raw": {
                "citations": list(set(raw_citations)),  # Remove duplicates
                "rn": raw_info.get("rn", "")
            },
            "extraction": {
                "related_rns": extraction_info.get("related_rns", []),
                "rn": extraction_info.get("rn", "")
            },
            "segmentation": {
                "rn": segmentation_info.get("rn", ""),
                "navigation": navigation
            },
            "enrichment": {
                "rn": enrichment_info.get("rn", ""),
            }
        }

        ds_meta = search_result.get("datasource") or {}
        backtrack["datasource"] = ds_meta

        return backtrack

    def _read_segment_enrichment(self,
                                 kb: 'KnowledgeBase',
                                 resource_id: str,
                                 version: str,
                                 retrieval_segment_id: str) -> Optional[dict]:
        """
        Load enrichment payload for a retrieval segment if it exists.
        Ensures 'rn' is present (generates one if missing).
        """
        try:
            txt = kb.storage.get_stage_content(
                "enrichment",
                resource_id,
                version,
                f"segment_{retrieval_segment_id}_enrichment.json",
                as_text=True
            )
            if not txt:
                return None
            data = json.loads(txt)

            return data
        except Exception:
            logger.debug("No enrichment for segment %s (resource=%s v=%s)",
                         retrieval_segment_id, resource_id, version)
            return None

    def load_complete_resource(self,
                               kb: 'KnowledgeBase',
                               resource_id: str,
                               version: Optional[str] = None) -> Dict[str, Any]:
        """
        Complete workflow: upsert datasource + batch upsert segments.

        Args:
            kb: KnowledgeBase instance
            resource_id: Resource identifier
            version: Version (None for latest)

        Returns:
            Complete operation statistics
        """
        logger.info(f"Loading complete resource {resource_id} v{version}")

        try:
            # 1. Upsert datasource version
            datasource_result = self.upsert_datasource_version(kb, resource_id, version)

            # 2. Batch upsert segments with full lineage
            segments_result = self.batch_upsert_retrieval_segments(kb, resource_id, version)

            result = {
                "status": "success",
                "resource_id": resource_id,
                "version": version or datasource_result.get("version"),
                "datasource_result": datasource_result,
                "segments_result": segments_result
            }

            logger.info(
                f"Successfully loaded complete resource {resource_id}: {segments_result.get('segments_upserted', 0)} segments")
            return result

        except Exception as e:
            logger.error(f"Error loading complete resource {resource_id}: {e}")
            logger.error(traceback.format_exc())
            raise

    def get_connector_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded data."""
        return self.kb_db.get_knowledge_base_stats()

    def is_resource_indexed(self, resource_id: str, version: str) -> Dict[str, Any]:
        """Check if a specific resource version has indexed segments."""
        return self.kb_db.is_resource_indexed(resource_id, int(version))

    def get_resources_with_indexed_segments(self) -> List[Dict[str, Any]]:
        """
        Get list of resources that actually have segments indexed.

        Returns:
            List of resources with actual segment counts from retrieval_segment table
        """
        return self.kb_db.get_resources_with_indexed_segments()

    def content_hash_exists(self, hash_value: str) -> Optional[str]:
        return self.kb_db.content_hash_exists(hash_value)

    def get_object_hash(self, object_name: str) -> List[ContentHash]:
        return self.kb_db.get_object_hash(object_name)

    def get_content_hash(self, object_name: str) -> Optional[ContentHash]:
        return self.kb_db.get_content_hash(object_name)

    def add_content_hash(self, object_name: str, hash_value: str,
                         hash_type: str = "SHA-256", creation_time: Optional[datetime] = None):
        return self.kb_db.add_content_hash(object_name, hash_value, hash_type, creation_time)

    def remove_content_hash(self, hash_value: str) -> bool:
        return self.kb_db.remove_content_hash(hash_value)

    def remove_object_hash(self, object_name: str):
        return self.kb_db.remove_object_hash(object_name)

    def list_content_hashes(self, name_pattern: Optional[str] = None, hash_type: Optional[str] = None,
                            provider: Optional[str] = None, created_after: Optional[datetime] = None,
                            created_before: Optional[datetime] = None, limit: int = 100, offset: int = 0,
                            order_by: str = "creation_time", order_desc: bool = True) -> Dict[str, Any]:
        return self.kb_db.list_content_hashes(name_pattern=name_pattern, hash_type=hash_type, provider=provider,
                                              created_after=created_after, created_before=created_before, limit=limit,
                                              offset=offset, order_by=order_by, order_desc=order_desc)

    def list_content_hashes_generator(self, name_pattern: Optional[str] = None, hash_type: Optional[str] = None,
                                      provider: Optional[str] = None, created_after: Optional[datetime] = None,
                                      created_before: Optional[datetime] = None, limit: int = 100, offset: int = 0,
                                      order_by: str = "creation_time", order_desc: bool = True) -> Generator[tuple[ContentHash, int, int], None, None]:
        while True:
            result = self.kb_db.list_content_hashes(name_pattern=name_pattern, hash_type=hash_type, provider=provider,
                                                    created_after=created_after, created_before=created_before,
                                                    limit=limit, offset=offset, order_by=order_by,
                                                    order_desc=order_desc)
            items = result.get("items", [])
            if len(items) == 0:
                break
            i = 0
            for item in items:
                yield item, i + offset, result.get("total_count")
                i += 1
            if not result.get("has_more", False):
                break
            offset += len(items)

    def get_content_hash_count(self, name_pattern: Optional[str] = None, hash_type: Optional[str] = None,
                               provider: Optional[str] = None, created_after: Optional[datetime] = None,
                               created_before: Optional[datetime] = None) -> int:
        return self.kb_db.get_content_hash_count(name_pattern=name_pattern, hash_type=hash_type, provider=provider,
                                                 created_after=created_after, created_before=created_before)

    def clear_all_content_hashes(self) -> int:
        return self.kb_db.clear_all_content_hashes()

    def batch_add_content_hashes(self,
                                 content_hashes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Batch insert multiple content hashes.

        Args:
            content_hashes: List of dictionaries with keys:
                - name: str (required) - Object name
                - value: str (required) - Hash value
                - type: str (optional) - Hash type, defaults to 'SHA-256'
                - provider: str (optional) - Provider identifier
                - creation_time: datetime (optional) - Creation time, defaults to now()
            conn: Database connection (handled by decorator)

        Returns:
            Dict with statistics about the batch operation:
            {
                "total_processed": int,
                "newly_inserted": int,
                "already_existed": int,
                "inserted_hashes": List[ContentHash],
                "duplicate_values": List[str]
            }
        """
        return self.kb_db.batch_add_content_hashes(content_hashes)

# Convenience functions for common operations
def create_kb_connector(tenant: str,
                        schema_name: str,
                        project_name: str,
                        embedding_model: ModelRecord,
                        system_schema_name: Optional[str] = None) -> KnowledgeBaseConnector:
    """
    Create a KnowledgeBaseConnector with your existing database classes.

    Args:
        schema_name: Database schema name
        project_name: Project name for RN generation
        system_schema_name: Optional system schema name

    Returns:
        KnowledgeBaseConnector instance
    """
    return KnowledgeBaseConnector(tenant, schema_name, project_name, embedding_model, system_schema_name)


def embedding_model() -> ModelRecord:
    from kdcube_ai_app.infra.llm.llm_data_model import AIProviderName, AIProvider
    from kdcube_ai_app.infra.llm.util import get_service_key_fn

    # from kdcube_ai_app.infra.embedding.embedding import embedder_model
    # Use OpenAI embeddings (1536 dimensions)
    # return embedder_model(size=1536, get_key_fn=get_api_key)
    provider = AIProviderName.open_ai
    provider = AIProvider(provider=provider,
                          apiToken=get_service_key_fn(provider))
    model_config = EMBEDDERS.get("openai-text-embedding-3-small")
    model_name = model_config.get("model_name")
    return ModelRecord(
        modelType="base",
        status="active",
        provider=provider,
        systemName=model_name,
    )


# Example usage
if __name__ == "__main__":
    # Create connector
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("KB.DBConnector")

    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())

    resource_id = "file|LLM Hallucination Prevention Techniques.pdf"
    import os

    project = os.environ.get("DEFAULT_PROJECT_NAME", None)
    tenant = os.environ.get("DEFAULT_TENANT", None)

    connector = create_kb_connector(
        tenant=tenant,
        schema_name=project.replace("-", "_"),
        project_name=project,
        embedding_model=embedding_model()
    )

    STORAGE_PATH = os.environ.get("KDCUBE_STORAGE_PATH")


    def kb_workdir(tenant: str, project: str):
        return f"{STORAGE_PATH}/kb/tenants/{tenant}/projects/{project}/knowledge_base"


    from kdcube_ai_app.apps.knowledge_base.core import KnowledgeBase

    kb = KnowledgeBase(tenant, project, kb_workdir(tenant, project), embedding_model=embedding_model())

    # Example operations
    # 1. Load complete resource
    resources = kb.list_resources()
    result = connector.load_complete_resource(kb, resource_id, "1")

    # 3. List datasources by type
    datasources = connector.list_datasources(source_type="file")
    print(f"Found {len(datasources)} file datasources")

    import time

    # 1. Simple text search
    op = "Simple Search"
    print(f"=== {op} ===")
    start = time.perf_counter()
    search_results = connector.hybrid_search("hallucination", top_k=5)
    elapsed = time.perf_counter() - start
    logger.info(f"[{op}] %s executed in {elapsed:.3f} seconds")

    for result in search_results:
        print(f"Found: {result.heading} (score: {result.relevance_score})")
        print(f"Navigation segments: {len(result.backtrack['segmentation']['navigation'])}")
        # 2. Advanced search with multiple filters

    op = "Advanced Search with Filters"
    print(f"\n=== {op} ===")
    start = time.perf_counter()

    query = "LLM uncertainty handling"
    query = "uncertainty in LLM"

    from kdcube_ai_app.infra.accounting import with_accounting

    with with_accounting("kb.connector.debug",
                         metadata={
                             "query": query,
                             "phase": "test_query_embedding",
                         }):
        query_embedding = connector.get_embedding(query)

    params = HybridSearchParams(
        query=query,
        resource_ids=["file|ai_book.pdf", "file|ml_paper.pdf", "file|LLM Hallucination Prevention Techniques.pdf"],
        entity_filters=[
            EntityItem(key="topic", value="deep learning"),
            EntityItem(key="domain", value="computer vision")
        ],
        tags=["technical", "research"],
        top_n=20,
        min_similarity=0.2,
        text_weight=0.5,
        semantic_weight=0.5,
        embedding=query_embedding,
        match_all=False,
        rerank_threshold=0.6,
        rerank_top_k=5,
    )

    # advanced_results = connector.advanced_search(params)
    pipe_results = connector.pipeline_search(params)
    elapsed = time.perf_counter() - start
    logger.info(f"[{op}] %s executed in {elapsed:.3f} seconds")
    # for result in advanced_results:
    #     print(f"Advanced: {result.heading} (score: {result.relevance_score:.3f})")

    # 3. Pure entity search
    op = "Entity Search"
    print(f"\n=== {op} ===")
    start = time.perf_counter()
    entity_results = connector.entity_search(
        entities=[
            EntityItem(key="topic", value="hallucinations"),  # {"key": "topic", "value": "hallucinations"}
            EntityItem(key="domain", value="LLM")
        ],
        match_all=False,  # Any entity matches
        top_k=5
    )
    elapsed = time.perf_counter() - start
    logger.info(f"[{op}] %s executed in {elapsed:.3f} seconds")
    for result in entity_results:
        print(f"Entity match: {result.heading}")

    # 4. Pure semantic search (requires embedding)
    op = "Semantic Search"
    print(f"\n=== {op} ===")
    query = "what causes AI hallucinations"
    query = "uncertainty in LLM"
    start = time.perf_counter()
    semantic_results = connector.semantic_search(
        query=query,
        distance_type="cosine",
        min_similarity=0.42,
        top_k=5
    )
    elapsed = time.perf_counter() - start
    logger.info(f"[{op}] %s executed in {elapsed:.3f} seconds")
    for result in semantic_results:
        print(f"Semantic: {result.heading} (similarity: {result.relevance_score:.3f})")
    print()
