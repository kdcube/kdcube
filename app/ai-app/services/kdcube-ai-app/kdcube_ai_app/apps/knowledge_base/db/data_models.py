# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kb_data_models.py

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime


class DataSource(BaseModel):
    """
    Represents a data source (document, file, etc.) in the knowledge base.
    Each data source can have multiple versions.
    """
    id: str
    version: int
    provider: Optional[str]
    rn: Optional[str] = None

    # Core metadata
    title: str
    uri: str  # Original source URI
    system_uri: Optional[str] = None  # S3 URI when rehosted
    source_type: str  # file, url, git, etc.

    # Content metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Processing status
    status: str = "pending"  # 'pending', 'processing', 'completed', 'failed'
    segment_count: int = 0

    # Temporal
    created_at: Optional[datetime] = None


class EntityItem(BaseModel):
    """
    Represents a single entity extracted from a segment.
    """
    key: str
    value: str


class RetrievalSegment(BaseModel):
    """
    Represents a text segment that can be retrieved for RAG.
    Only the latest version per datasource is kept for search.
    Version always matches the datasource version.
    """
    id: str
    version: int  # Always matches datasource version
    provider: Optional[str]
    rn: Optional[str] = None

    # Link to datasource (version is same as segment version)
    resource_id: str  # matches datasource.id

    # Core content
    content: str
    summary: Optional[str] = None
    title: Optional[str] = None

    # Extracted entities and metadata from processing
    entities: List[EntityItem] = Field(default_factory=list)

    # Additional metadata
    tags: List[str] = Field(default_factory=list)
    word_count: Optional[int] = None
    sentence_count: Optional[int] = None

    processed_at: Optional[datetime] = None

    # Search vectors (managed by DB triggers)
    search_vector: Optional[str] = None  # TSVECTOR (read-only)
    embedding: Optional[Union[List[float], str]] = None  # Vector embedding

    # Temporal
    created_at: Optional[datetime] = None

    # Lineage (tracks source) - uses namespaced structure
    lineage: Dict[str, Any] = Field(default_factory=dict)

    # Extensions - non-indexed arbitrary data
    extensions: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """
    Represents a search result with optional distance/similarity scores.
    """
    segment: RetrievalSegment
    distance: Optional[float] = None
    similarity: Optional[float] = None
    rank: Optional[int] = None


class HybridSearchParams(BaseModel):
    """
    Parameters for hybrid search combining text and semantic search.
    """

    # Recall signals:
    # Text search
    query: Optional[str] = None
    tags: Optional[List[str]] = None
    # Semantic search
    embedding: Optional[List[float]] = None

    distance_type: str = "cosine"  # 'cosine', 'l2', 'ip'

    # Filters (facets)
    resource_ids: Optional[List[str]] = None
    entity_filters: Optional[List[EntityItem]] = None

    # Result controls
    top_n: int = 10
    min_similarity: Optional[float] = None

    # Hybrid search weights (if both text and semantic)
    text_weight: float = 0.5
    semantic_weight: float = 0.5

    # Controls recall signals attribution composition
    match_all: bool = False

    # Post-ANN cross-encoder options
    rerank_top_k: Optional[int]     = None   # after reranking, truncate to this many
    rerank_threshold: Optional[float] = None  # drop any row with rerank_score < this

    providers: Optional[List[str]] = None  # Specific data providers to filter by

    include_expired: bool = True

    published_after: Optional[Union[str, datetime]] = None
    published_before: Optional[Union[str, datetime]] = None
    published_on: Optional[Union[str, datetime]] = None   # exact day

    # Update time filters (datasource.metadata.metadata.modified_time_iso)
    modified_after: Optional[Union[str, datetime]] = None
    modified_before: Optional[Union[str, datetime]] = None
    modified_on: Optional[Union[str, datetime]] = None

    # reranking
    should_rerank: bool = False


class SegmentProcessingData(BaseModel):
    """
    Input data for creating/updating segments with all processing results.
    This combines the metadata and embedding data from your examples.
    """
    segment_id: str
    segment_type: str = "retrieval"
    resource_id: str
    version: str

    # Content
    content: str
    summary: Optional[str] = None
    title: Optional[str] = None

    # Extracted entities
    entities: List[EntityItem] = Field(default_factory=list)

    # Text statistics
    word_count: int = 0
    sentence_count: Optional[int] = None

    processed_at: Optional[datetime] = None

    # Embedding data
    embedding: Optional[List[float]] = None
    embedding_dimensions: Optional[int] = None
    embedding_size: Optional[int] = None
    provider: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None

    # Usage stats
    usage: Optional[Dict[str, Any]] = None

    # Resource naming
    rn: Optional[str] = None

    # Extensions for arbitrary data
    extensions: Dict[str, Any] = Field(default_factory=dict)

    def to_retrieval_segment(self, datasource_version: int) -> RetrievalSegment:
        """Convert to RetrievalSegment model for database storage."""
        return RetrievalSegment(
            id=self.segment_id,
            version=datasource_version,  # Segment version matches datasource version
            resource_id=self.resource_id,
            content=self.content,
            summary=self.summary,
            title=self.title,
            entities=self.entities,
            word_count=self.word_count,
            sentence_count=self.sentence_count,
            processed_at=self.processed_at,
            embedding=self.embedding,
            lineage={
                "resource_id": self.resource_id,
                "segment_type": self.segment_type,
                "original_rn": self.rn
            },
            extensions=self.extensions
        )


class BatchSegmentUpdate(BaseModel):
    """
    Represents a batch update of segments for a specific datasource version.
    """
    resource_id: str
    datasource_version: int  # Version of the datasource (same as segment versions)
    segments: List[SegmentProcessingData]
    cleanup_old_versions: bool = True

class ContentHash(BaseModel):
    """
    Represents an object hash record
    """
    id: int
    name: str
    value: str
    type: str
    provider: Optional[str]
    creation_time: datetime

# --- New typed models ---

class DataSourceRef(BaseModel):
    id: str
    version: int
    provider: Optional[str] = None
    title: Optional[str] = None
    uri: Optional[str] = None
    system_uri: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BacktrackNavItem(BaseModel):
    start_line: int = 0
    end_line: int = 0
    start_pos: int = 0
    end_pos: int = 0
    citations: List[str] = Field(default_factory=list)
    heading: str = ""
    subheading: str = ""


class BacktrackRawStage(BaseModel):
    rn: Optional[str] = None
    citations: List[str] = Field(default_factory=list)


class BacktrackExtractionStage(BaseModel):
    rn: Optional[str] = None
    related_rns: List[str] = Field(default_factory=list)


class BacktrackSegmentationStage(BaseModel):
    rn: Optional[str] = None
    navigation: List[BacktrackNavItem] = Field(default_factory=list)


class BacktrackEnrichmentStage(BaseModel):
    rn: Optional[str] = None


class Backtrack(BaseModel):
    raw: BacktrackRawStage = Field(default_factory=BacktrackRawStage)
    extraction: BacktrackExtractionStage = Field(default_factory=BacktrackExtractionStage)
    segmentation: BacktrackSegmentationStage = Field(default_factory=BacktrackSegmentationStage)
    enrichment: BacktrackEnrichmentStage = Field(default_factory=BacktrackEnrichmentStage)
    datasource: DataSourceRef = Field(default_factory=DataSourceRef)


class NavigationSearchResult(BaseModel):
    """Final search hit for UI/agents. Backtrack is typed, not a dict."""
    query: str
    relevance_score: float
    heading: str
    subheading: str
    content: str
    backtrack: Optional[Any] = None
