# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# providers/knowledge_base_enhanced.py

from datetime import datetime
import datetime as dt
import json
from typing import Optional, List, Dict, Any, Union, Tuple

from psycopg2.extras import Json, execute_values

from kdcube_ai_app.ops.deployment.sql.db_deployment import SYSTEM_SCHEMA, PROJECT_DEFAULT_SCHEMA
from kdcube_ai_app.infra.relational.psql.psql_base import PostgreSqlDbMgr
from kdcube_ai_app.infra.relational.psql.utilities import transactional, to_pgvector_str
from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string, parse_embedding

# Import the data models
from kdcube_ai_app.apps.knowledge_base.db.data_models import (
    DataSource, RetrievalSegment, EntityItem, BatchSegmentUpdate, ContentHash
)


def _convert_entities_to_jsonb(entities: List[EntityItem]) -> List[Dict[str, str]]:
    """Convert EntityItem objects to dict format for JSONB storage."""
    return [{"key": e.key, "value": e.value} for e in entities]


def _convert_entities_from_jsonb(entities_json: List[Dict[str, str]]) -> List[EntityItem]:
    """Convert JSONB dict format back to EntityItem objects."""
    return [EntityItem(key=e["key"], value=e["value"]) for e in (entities_json or [])]


def _row_to_datasource(row_dict: Dict[str, Any]) -> DataSource:
    """Convert database row to DataSource object."""
    return DataSource(
        id=row_dict["id"],
        version=row_dict["version"],
        rn=row_dict.get("rn"),
        title=row_dict["title"],
        uri=row_dict["uri"],
        system_uri=row_dict.get("system_uri"),
        provider=row_dict.get("provider"),
        expiration=row_dict.get("expiration"),
        metadata=row_dict.get("metadata") or {},
        status=row_dict.get("status", "pending"),
        segment_count=row_dict.get("segment_count", 0),
        created_at=row_dict.get("created_at"),
        source_type=row_dict.get("source_type")
    )


def _row_to_retrieval_segment(row_dict: Dict[str, Any]) -> RetrievalSegment:
    """Convert database row to RetrievalSegment object - updated for new schema."""
    return RetrievalSegment(
        id=row_dict["id"],
        version=row_dict["version"],
        rn=row_dict.get("rn"),
        resource_id=row_dict["resource_id"],
        provider=row_dict.get("provider"),  # NEW
        content=row_dict["content"],
        summary=row_dict.get("summary"),
        title=row_dict.get("title"),
        # heading and subheading removed - content includes them
        entities=_convert_entities_from_jsonb(row_dict.get("entities", [])),
        tags=row_dict.get("tags", []),
        word_count=row_dict.get("word_count"),
        sentence_count=row_dict.get("sentence_count"),
        model_used=row_dict.get("model_used"),
        processed_at=row_dict.get("processed_at"),
        embedding=parse_embedding(row_dict.get("embedding")) if row_dict.get("embedding") else None,
        created_at=row_dict.get("created_at"),
        lineage=row_dict.get("lineage") or {},
        extensions=row_dict.get("extensions") or {}  # New extensions field
    )


class KnowledgeBaseDB:
    """
    Enhanced database manager for Knowledge Base operations.
    Includes all CRUD operations, batch processing, and search support.
    """

    def __init__(self,
                 tenant: str,
                 schema_name: Optional[str] = None,
                 system_schema_name: Optional[str] = None,
                 config=None):
        self.dbmgr = PostgreSqlDbMgr()

        if schema_name and not schema_name.startswith(tenant):
            schema_name = f"{tenant}_{schema_name}"
        if schema_name and not schema_name.startswith("kdcube_"):
            schema_name = f"kdcube_{schema_name}"
        self.schema = schema_name or PROJECT_DEFAULT_SCHEMA
        self.system_schema = system_schema_name or SYSTEM_SCHEMA

    # ================================
    # DataSource CRUD Operations
    # ================================

    @transactional
    def create_datasource(self, datasource: DataSource, conn=None) -> DataSource:
        """Create a new datasource."""
        sql = f"""
        INSERT INTO {self.schema}.datasource (
            id, version, rn, title, uri, source_type, provider, system_uri, 
            metadata, status, segment_count, expiration, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """

        now = datetime.utcnow()
        datasource.created_at = datasource.created_at or now

        data = (
            datasource.id,
            datasource.version,
            datasource.rn,
            datasource.title,
            datasource.uri,
            datasource.source_type,
            datasource.provider,
            datasource.system_uri,
            Json(datasource.metadata),
            datasource.status,
            datasource.segment_count,
            datasource.expiration,
            datasource.created_at
        )

        with conn.cursor() as cur:
            cur.execute(sql, data)
            row = cur.fetchone()
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))
            return _row_to_datasource(row_dict)

    @transactional
    def upsert_datasource(self,
                          datasource_data: Dict[str, Any],
                          convert_to_obj: bool = False,
                          conn=None) -> Dict[str, Any]:
        """
        Upsert datasource record - UPDATED with provider and expiration.
        """
        sql = f"""
        INSERT INTO {self.schema}.datasource (
            id, version, rn, source_type, provider, title, uri, system_uri, 
            metadata, status, segment_count, expiration
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id, version) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            provider = EXCLUDED.provider,
            title = EXCLUDED.title,
            uri = EXCLUDED.uri,
            system_uri = EXCLUDED.system_uri,
            metadata = EXCLUDED.metadata,
            status = EXCLUDED.status,
            segment_count = EXCLUDED.segment_count,
            expiration = EXCLUDED.expiration
        RETURNING *, (xmax = 0) AS inserted
        """

        data = (
            datasource_data["id"],
            datasource_data["version"],
            datasource_data["rn"],
            datasource_data.get("source_type"),
            datasource_data.get("provider"),
            datasource_data["title"],
            datasource_data["uri"],
            datasource_data.get("system_uri"),
            Json(datasource_data.get("metadata", {})),
            datasource_data.get("status", "pending"),
            datasource_data.get("segment_count", 0),
            datasource_data.get("expiration")
        )

        with conn.cursor() as cur:
            cur.execute(sql, data)
            row = cur.fetchone()
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))

            operation = "created" if row_dict.get("inserted") else "updated"

            return {
                "operation": operation,
                "resource_id": datasource_data["id"],
                "version": datasource_data["version"],
                "datasource": _row_to_datasource(row_dict) if convert_to_obj else row_dict
            }

    @transactional
    def get_datasource(self, datasource_id: str, version: Optional[int] = None, conn=None) -> Optional[DataSource]:
        """Get a datasource by ID and optionally version. If no version specified, gets latest."""
        if version is not None:
            sql = f"""
            SELECT * FROM {self.schema}.datasource 
            WHERE id = %s AND version = %s
            """
            params = (datasource_id, version)
        else:
            sql = f"""
            SELECT * FROM {self.schema}.datasource 
            WHERE id = %s 
            ORDER BY version DESC 
            LIMIT 1
            """
            params = (datasource_id,)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))
            return _row_to_datasource(row_dict)

    @transactional
    def update_datasource_status(self, datasource_id: str, version: int, status: str,
                                 segment_count: Optional[int] = None, conn=None) -> bool:
        """Update datasource status and optionally segment count."""
        if segment_count is not None:
            sql = f"""
            UPDATE {self.schema}.datasource 
            SET status = %s, segment_count = %s
            WHERE id = %s AND version = %s
            """
            params = (status, segment_count, datasource_id, version)
        else:
            sql = f"""
            UPDATE {self.schema}.datasource 
            SET status = %s
            WHERE id = %s AND version = %s
            """
            params = (status, datasource_id, version)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount > 0

    @transactional
    def list_datasources(self,
                         status: Optional[str] = None,
                         source_type: Optional[str] = None,
                         provider: Optional[str] = None,  # NEW
                         include_expired: bool = True,  # NEW
                         limit: int = 100,
                         conn=None) -> List[DataSource]:
        """List datasources with optional filters."""
        where_clauses = []
        params = []

        if status:
            where_clauses.append("status = %s")
            params.append(status)

        if source_type:
            where_clauses.append("source_type = %s")
            params.append(source_type)

        if provider:
            where_clauses.append("provider = %s")
            params.append(provider)

        if not include_expired:
            where_clauses.append("(expiration IS NULL OR expiration > now())")

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
        SELECT * FROM {self.schema}.datasource 
        {where_clause}
        ORDER BY created_at DESC 
        LIMIT %s
        """
        params.append(limit)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]
            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append(_row_to_datasource(row_dict))
            return results

    @transactional
    def delete_datasource_and_segments(self, datasource_id: str, version: Optional[int] = None, conn=None) -> Dict[str, int]:
        """
        Delete a datasource and all its segments.
        If version is specified, delete only that version. Otherwise delete all versions.
        """
        if version is not None:
            # Delete specific version
            segments_sql = f"""
            DELETE FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s AND version = %s
            """
            datasource_sql = f"""
            DELETE FROM {self.schema}.datasource 
            WHERE id = %s AND version = %s
            """
            params = (datasource_id, version)
        else:
            # Delete all versions
            segments_sql = f"""
            DELETE FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s
            """
            datasource_sql = f"""
            DELETE FROM {self.schema}.datasource 
            WHERE id = %s
            """
            params = (datasource_id,)

        with conn.cursor() as cur:
            # Delete segments first (foreign key constraint)
            cur.execute(segments_sql, params)
            segments_deleted = cur.rowcount

            # Delete datasource(s)
            cur.execute(datasource_sql, params)
            datasources_deleted = cur.rowcount

        return {
            "datasources_deleted": datasources_deleted,
            "segments_deleted": segments_deleted
        }

    # ================================
    # NEW: Cache/Expiration Management Methods
    # ================================

    @transactional
    def cleanup_expired_data(self, conn=None) -> Dict[str, int]:
        """Clean up expired datasources and their segments."""
        sql = f"SELECT * FROM {self.schema}.cleanup_expired_data_{self.schema.replace('.', '_')}()"

        with conn.cursor() as cur:
            cur.execute(sql)
            result = cur.fetchone()
            return {
                "datasources_deleted": result[0] if result else 0,
                "segments_deleted": result[1] if result else 0
            }

    @transactional
    def get_expired_datasources(self, conn=None) -> List[DataSource]:
        """Get all expired datasources."""
        sql = f"""
        SELECT * FROM {self.schema}.expired_datasources
        ORDER BY expiration ASC
        """

        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]
            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append(_row_to_datasource(row_dict))
            return results

    @transactional
    def extend_datasource_expiration(self, datasource_id: str, version: int,
                                   new_expiration: datetime, conn=None) -> bool:
        """Extend the expiration time for a datasource."""
        sql = f"""
        UPDATE {self.schema}.datasource 
        SET expiration = %s
        WHERE id = %s AND version = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (new_expiration, datasource_id, version))
            return cur.rowcount > 0

    # ================================
    # RetrievalSegment CRUD Operations
    # ================================

    @transactional
    def create_retrieval_segment(self, segment: RetrievalSegment, conn=None) -> RetrievalSegment:
        """Create a new retrieval segment."""
        sql = f"""
        INSERT INTO {self.schema}.retrieval_segment (
            id, version, rn, resource_id, provider, content, summary, title, 
            entities, tags, word_count, sentence_count, processed_at,
            embedding, lineage, extensions, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """

        now = datetime.utcnow()
        segment.created_at = segment.created_at or now

        # Convert embedding to string format for storage
        embedding_str = convert_embedding_to_string(segment.embedding) if segment.embedding else None

        data = (
            segment.id,
            segment.version,
            segment.rn,
            segment.resource_id,
            segment.provider,
            segment.content,
            segment.summary,
            segment.title,
            Json(_convert_entities_to_jsonb(segment.entities)),
            segment.tags,
            segment.word_count,
            segment.sentence_count,
            segment.processed_at,
            embedding_str,
            Json(segment.lineage),
            Json(segment.extensions),
            segment.created_at
        )

        with conn.cursor() as cur:
            cur.execute(sql, data)
            row = cur.fetchone()
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))
            return _row_to_retrieval_segment(row_dict)

    @transactional
    def batch_upsert_retrieval_segments(self,
                                        resource_id: str,
                                        version: int,
                                        segments_data: List[Dict[str, Any]],
                                        conn=None) -> Dict[str, Any]:
        """
        Batch upsert retrieval segments
        """
        # Verify datasource exists
        datasource = self.get_datasource(resource_id, version, conn=conn)
        if not datasource:
            raise ValueError(f"Datasource {resource_id} version {version} does not exist")

        with conn.cursor() as cur:
            # Clean up older versions of segments for this resource
            cur.execute(
                f"DELETE FROM {self.schema}.retrieval_segment WHERE resource_id = %s AND version < %s",
                (resource_id, version)
            )
            older_segments_deleted = cur.rowcount

            # Clean up same version segments (for re-processing)
            cur.execute(
                f"DELETE FROM {self.schema}.retrieval_segment WHERE resource_id = %s AND version = %s",
                (resource_id, version)
            )
            same_version_deleted = cur.rowcount

            # Batch insert new segments
            segments_upserted = 0
            embeddings_loaded = 0
            metadata_loaded = 0

            if segments_data:
                rows_to_insert = []
                now = datetime.utcnow()

                for segment_data in segments_data:
                    # Count embeddings and metadata
                    if segment_data.get("embedding"):
                        embeddings_loaded += 1
                    if segment_data.get("entities"):
                        metadata_loaded += 1

                    # Inherit provider from datasource if not specified
                    provider = segment_data.get("provider") or datasource.provider

                    embedding_str = segment_data.get("embedding")

                    row_data = (
                        segment_data["id"],
                        segment_data["version"],
                        segment_data["rn"],
                        segment_data["resource_id"],
                        provider,
                        segment_data["content"],
                        segment_data.get("summary", ""),
                        segment_data.get("title", ""),
                        Json(segment_data.get("entities", [])),
                        segment_data.get("tags", []),
                        segment_data.get("word_count", 0),
                        segment_data.get("sentence_count", 0),
                        segment_data.get("processed_at"),
                        embedding_str,
                        Json(segment_data.get("lineage", {})),
                        Json(segment_data.get("extensions", {})),
                        now
                    )
                    rows_to_insert.append(row_data)

                # Batch insert
                sql = f"""
                INSERT INTO {self.schema}.retrieval_segment (
                    id, version, rn, resource_id, provider, content, summary, title, 
                    entities, tags, word_count, sentence_count, processed_at,
                    embedding, lineage, extensions, created_at
                )
                VALUES %s
                """

                execute_values(cur, sql, rows_to_insert)
                segments_upserted = len(rows_to_insert)

            # Update datasource segment count
            self.update_datasource_status(
                resource_id, version,
                status="completed",
                segment_count=segments_upserted,
                conn=conn
            )

            return {
                "segments_upserted": segments_upserted,
                "embeddings_loaded": embeddings_loaded,
                "metadata_loaded": metadata_loaded,
                "older_segments_deleted": older_segments_deleted,
                "same_version_deleted": same_version_deleted,
                "provider": datasource.provider
            }

    @transactional
    def get_retrieval_segment(self, segment_id: str, version: int, conn=None) -> Optional[RetrievalSegment]:
        """Get a retrieval segment by ID and version."""
        sql = f"""
        SELECT * FROM {self.schema}.retrieval_segment 
        WHERE id = %s AND version = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (segment_id, version))
            row = cur.fetchone()
            if not row:
                return None
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))
            return _row_to_retrieval_segment(row_dict)

    @transactional
    def list_segments_by_resource(self, resource_id: str, datasource_version: Optional[int] = None,
                                  limit: int = 100, conn=None) -> List[RetrievalSegment]:
        """List segments for a specific resource, optionally filtered by datasource version."""
        if datasource_version is not None:
            sql = f"""
            SELECT * FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s AND version = %s 
            ORDER BY created_at DESC 
            LIMIT %s
            """
            params = (resource_id, datasource_version, limit)
        else:
            sql = f"""
            SELECT * FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s 
            ORDER BY created_at DESC 
            LIMIT %s
            """
            params = (resource_id, limit)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]
            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append(_row_to_retrieval_segment(row_dict))
            return results


    # ================================
    # Batch Operations (Legacy)
    # ================================

    @transactional
    def cleanup_old_segments(self, resource_id: str, keep_version: int, conn=None) -> int:
        """Remove segments for old versions of a resource, keeping only the specified version."""
        sql = f"""
        DELETE FROM {self.schema}.retrieval_segment 
        WHERE resource_id = %s AND version != %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (resource_id, keep_version))
            return cur.rowcount

    @transactional
    def batch_update_segments(self, batch_update: BatchSegmentUpdate, conn=None) -> Dict[str, Any]:
        """
        Legacy batch update method - kept for compatibility.
        Use batch_upsert_retrieval_segments for new code.
        """
        resource_id = batch_update.resource_id
        datasource_version = batch_update.datasource_version

        try:
            # Clean up old versions if requested
            deleted_count = 0
            if batch_update.cleanup_old_versions:
                deleted_count = self.cleanup_old_segments(resource_id, datasource_version, conn=conn)

            # Prepare segments for insertion
            segments_to_insert = []
            for segment_data in batch_update.segments:
                segment = segment_data.to_retrieval_segment(datasource_version)
                segments_to_insert.append(segment)

            # Batch insert new segments
            if segments_to_insert:
                inserted_segments = self._batch_insert_segments(segments_to_insert, conn=conn)
            else:
                inserted_segments = []

            # Update datasource segment count
            segment_count = len(inserted_segments)
            self.update_datasource_status(
                resource_id, datasource_version,
                status="completed",
                segment_count=segment_count,
                conn=conn
            )

            return {
                "status": "success",
                "resource_id": resource_id,
                "datasource_version": datasource_version,
                "segments_inserted": len(inserted_segments),
                "old_segments_deleted": deleted_count,
                "segments": inserted_segments
            }

        except Exception as e:
            # Update datasource status to failed
            self.update_datasource_status(
                resource_id, datasource_version,
                status="failed",
                conn=conn
            )
            raise e

    @transactional
    def _batch_insert_segments(self, segments: List[RetrievalSegment], conn=None) -> List[RetrievalSegment]:
        """Internal method for batch inserting segments - updated for new schema."""
        if not segments:
            return []

        sql = f"""
        INSERT INTO {self.schema}.retrieval_segment (
            id, version, resource_id, provider, content, summary, title, 
            entities, tags, word_count, sentence_count, processed_at,
            embedding, lineage, extensions, created_at
        )
        VALUES %s
        RETURNING *
        """

        now = datetime.utcnow()
        rows_to_insert = []

        for segment in segments:
            segment.created_at = segment.created_at or now
            embedding_str = convert_embedding_to_string(segment.embedding) if segment.embedding else None

            row_data = (
                segment.id,
                segment.version,
                segment.resource_id,
                segment.provider,
                segment.content,
                segment.summary,
                segment.title,
                Json(_convert_entities_to_jsonb(segment.entities)),
                segment.tags,
                segment.word_count,
                segment.sentence_count,
                segment.processed_at,
                embedding_str,
                Json(segment.lineage),
                Json(segment.extensions),
                segment.created_at
            )
            rows_to_insert.append(row_data)

        with conn.cursor() as cur:
            execute_values(cur, sql, rows_to_insert)
            returned_rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]

            results = []
            for row in returned_rows:
                row_dict = dict(zip(colnames, row))
                results.append(_row_to_retrieval_segment(row_dict))

            return results

    # ================================
    # Utility Methods
    # ================================

    @transactional
    def get_segment_count_by_resource(self, resource_id: str, datasource_version: Optional[int] = None, conn=None) -> int:
        """Get count of segments for a resource."""
        if datasource_version is not None:
            sql = f"""
            SELECT COUNT(*) FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s AND version = %s
            """
            params = (resource_id, datasource_version)
        else:
            sql = f"""
            SELECT COUNT(*) FROM {self.schema}.retrieval_segment 
            WHERE resource_id = %s
            """
            params = (resource_id,)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchone()
            return result[0] if result else 0

    @transactional
    def get_segment_count_by_provider(self, provider: str, include_expired: bool = False, conn=None) -> int:
        """NEW: Get count of segments for a provider."""
        if include_expired:
            sql = f"""
            SELECT COUNT(*) FROM {self.schema}.retrieval_segment 
            WHERE provider = %s
            """
            params = (provider,)
        else:
            sql = f"""
            SELECT COUNT(*) FROM {self.schema}.active_retrieval_segments 
            WHERE provider = %s
            """
            params = (provider,)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchone()
            return result[0] if result else 0

    def get_knowledge_base_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the knowledge base."""
        with self.dbmgr.get_connection() as conn:
            with conn.cursor() as cur:
                stats = {}

                # Datasource stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.datasource")
                stats["total_datasources"] = cur.fetchone()[0]

                # Active datasource stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.active_datasources")
                stats["active_datasources"] = cur.fetchone()[0]

                # Expired datasource stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.expired_datasources")
                stats["expired_datasources"] = cur.fetchone()[0]

                # Segments stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.retrieval_segment")
                stats["total_segments"] = cur.fetchone()[0]

                # Active segments stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.active_retrieval_segments")
                stats["active_segments"] = cur.fetchone()[0]

                # Embeddings stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.retrieval_segment WHERE embedding IS NOT NULL")
                stats["segments_with_embeddings"] = cur.fetchone()[0]

                # Metadata stats
                cur.execute(f"SELECT COUNT(*) FROM {self.schema}.retrieval_segment WHERE jsonb_array_length(entities) > 0")
                stats["segments_with_metadata"] = cur.fetchone()[0]

                # Provider distribution - NEW
                cur.execute(f"SELECT provider, COUNT(*) FROM {self.schema}.datasource GROUP BY provider")
                stats["datasources_by_provider"] = dict(cur.fetchalls())

                # Source type distribution
                cur.execute(f"SELECT source_type, COUNT(*) FROM {self.schema}.datasource GROUP BY source_type")
                stats["datasources_by_type"] = dict(cur.fetchall())

                # Provider + source type combination - NEW
                cur.execute(f"SELECT provider, source_type, COUNT(*) FROM {self.schema}.datasource GROUP BY provider, source_type")
                provider_type_stats = {}
                for provider, source_type, count in cur.fetchall():
                    if provider not in provider_type_stats:
                        provider_type_stats[provider] = {}
                    provider_type_stats[provider][source_type] = count
                stats["datasources_by_provider_and_type"] = provider_type_stats

                # Calculate ratios
                if stats["total_segments"] > 0:
                    stats["embedding_coverage"] = stats["segments_with_embeddings"] / stats["total_segments"]
                    stats["metadata_coverage"] = stats["segments_with_metadata"] / stats["total_segments"]
                    stats["active_segment_ratio"] = stats["active_segments"] / stats["total_segments"]
                else:
                    stats["embedding_coverage"] = 0.0
                    stats["metadata_coverage"] = 0.0
                    stats["active_segment_ratio"] = 0.0

                if stats["total_datasources"] > 0:
                    stats["active_datasource_ratio"] = stats["active_datasources"] / stats["total_datasources"]
                else:
                    stats["active_datasource_ratio"] = 0.0

                return stats

    @transactional
    def is_resource_indexed(self, resource_id: str, version: int, conn=None) -> Dict[str, Any]:
        """
        Check if a specific resource version has indexed segments.
        """
        sql = f"""
        SELECT COUNT(id) as segment_count
        FROM {self.schema}.retrieval_segment 
        WHERE resource_id = %s AND version = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (resource_id, version))
            result = cur.fetchone()
            segment_count = result[0] if result else 0

            return {
                "is_indexed": segment_count > 0,
                "segment_count": segment_count,
                "resource_id": resource_id,
                "version": version
            }

    @transactional
    def get_resources_with_indexed_segments(self, provider: Optional[str] = None,
                                          include_expired: bool = True, conn=None) -> List[Dict[str, Any]]:
        """
        Get list of resources that have segments indexed - UPDATED with provider filtering.
        """
        where_clause = ""
        params = []

        if provider:
            where_clause = "WHERE ds.provider = %s"
            params.append(provider)

        if not include_expired:
            if where_clause:
                where_clause += " AND (ds.expiration IS NULL OR ds.expiration > now())"
            else:
                where_clause = "WHERE (ds.expiration IS NULL OR ds.expiration > now())"

        sql = f"""
        SELECT 
            rs.resource_id,
            rs.version,
            COUNT(rs.id) as actual_segment_count,
            ds.provider,
            ds.source_type,
            ds.title,
            ds.status,
            ds.expiration,
            ds.created_at,
            ds.uri,
            ds.rn
        FROM {self.schema}.retrieval_segment rs
        JOIN {self.schema}.datasource ds ON ds.id = rs.resource_id AND ds.version = rs.version
        {where_clause}
        GROUP BY rs.resource_id, rs.version, ds.provider, ds.source_type, ds.title, 
                 ds.status, ds.expiration, ds.created_at, ds.uri, ds.rn
        ORDER BY ds.created_at DESC
        """

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]

            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append({
                    "resource_id": row_dict["resource_id"],
                    "version": row_dict["version"],
                    "provider": row_dict["provider"],
                    "source_type": row_dict["source_type"],
                    "title": row_dict["title"],
                    "actual_segment_count": row_dict["actual_segment_count"],
                    "status": row_dict["status"],
                    "expiration": row_dict["expiration"],
                    "is_expired": row_dict["expiration"] is not None and row_dict["expiration"] <= datetime.utcnow(),  # NEW
                    "created_at": row_dict["created_at"],
                    "uri": row_dict["uri"],
                    "rn": row_dict["rn"]
                })

            return results

    @transactional
    def content_hash_exists(self, hash_value: str, conn=None) -> Optional[str]:
        """
        Return the `name` from <schema>.content_hash where value = hash_value.
        None if no match.
        """
        sql = f"""
            SELECT name
            FROM {self.schema}.content_hash
            WHERE value = %s
            LIMIT 1
        """
        with conn.cursor() as cur:
            cur.execute(sql, (hash_value,))
            row = cur.fetchone()
            return row[0] if row else None

    # @transactional
    # def content_hash_exists(self, hash_value:str, conn=None) -> Optional[str]:
    #     sql = f"""
    #             SELECT EXISTS(
    #                 SELECT 1 FROM {self.schema}.content_hash
    #                 WHERE value = %s
    #             )
    #         """
    #     with conn.cursor() as cur:
    #         cur.execute(sql, (hash_value,))
    #         return cur.fetchone()[0]

    def _row_to_content_hash(self, row_dict: Dict[str, Any]) -> ContentHash:
        """Convert database row to ContentHash object."""
        return ContentHash(
            id=row_dict["id"],
            name=row_dict["name"],
            value=row_dict["value"],
            type=row_dict["type"],
            provider=row_dict.get("provider"),
            creation_time=row_dict["creation_time"]
        )

    @transactional
    def get_object_hash(self, object_name: str, conn=None) -> List[ContentHash]:
        """Get all content hashes by object name."""
        sql = f"""
        SELECT * FROM {self.schema}.content_hash 
        WHERE name = %s
        ORDER BY creation_time DESC
        """

        with conn.cursor() as cur:
            cur.execute(sql, (object_name,))
            rows = cur.fetchall()
            if not rows:
                return []
            colnames = [desc[0] for desc in cur.description]
            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append(self._row_to_content_hash(row_dict))
            return results

    @transactional
    def get_content_hash(self, hash_value: str, conn=None) -> Optional[ContentHash]:
        """Get content hash by hash value."""
        sql = f"""
        SELECT * FROM {self.schema}.content_hash 
        WHERE value = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (hash_value,))
            row = cur.fetchone()
            if not row:
                return None
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))
            return self._row_to_content_hash(row_dict)

    @transactional
    def add_content_hash(self, object_name: str, hash_value:str,
                         hash_type:str = "SHA-256", creation_time:Optional[datetime]=None,
                         conn=None):
        sql = f"""
            INSERT INTO {self.schema}.content_hash (
                name, value, type, creation_time
            )
            VALUES (%s, %s, %s, COALESCE(%s, now()))
            ON CONFLICT (value) DO NOTHING
            RETURNING *, (xmax = 0) AS inserted
        """

        data = (
            object_name,
            hash_value,
            hash_type,
            creation_time
        )

        with conn.cursor() as cur:
            cur.execute(sql, data)
            row = cur.fetchone()
            colnames = [desc[0] for desc in cur.description]
            row_dict = dict(zip(colnames, row))

            exists = True if row_dict.get("inserted") else False

            return {
                "exists": exists,
            }

    @transactional
    def remove_content_hash(self, hash_value: str, conn=None) -> bool:
        """Remove content hash by hash value."""
        sql = f"""
        DELETE FROM {self.schema}.content_hash 
        WHERE value = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (hash_value,))
            return cur.rowcount > 0

    @transactional
    def remove_object_hash(self, object_name: str, conn=None) -> int:
        """Remove content hash(es) by object name. Returns number of rows deleted."""
        sql = f"""
        DELETE FROM {self.schema}.content_hash 
        WHERE name = %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, (object_name,))
            return cur.rowcount

    @transactional
    def list_content_hashes(self, name_pattern: Optional[str] = None, hash_type: Optional[str] = None,
                            provider: Optional[str] = None, created_after: Optional[datetime] = None,
                            created_before: Optional[datetime] = None, limit: int = 100, offset: int = 0,
                            order_by: str = "creation_time", order_desc: bool = True, conn=None) -> Dict[str, Any]:
        """
        List content hashes with optional filters and pagination.

        Args:
            provider: Filter by provider (optional)
            hash_type: Filter by hash type (e.g., 'SHA-256', 'MD5') (optional)
            name_pattern: Filter by name pattern using ILIKE (optional)
            created_after: Filter by creation time >= this datetime (optional)
            created_before: Filter by creation time <= this datetime (optional)
            limit: Maximum number of results (default: 100)
            offset: Number of results to skip for pagination (default: 0)
            order_by: Column to order by ('creation_time', 'name', 'type') (default: 'creation_time')
            order_desc: Whether to order descending (default: True)
            conn: Database connection (handled by decorator)

        Returns:
            Dict containing 'items' (list of ContentHash), 'total_count', 'limit', 'offset'
        """
        where_clauses = []
        params = []

        if provider:
            where_clauses.append("provider = %s")
            params.append(provider)

        if hash_type:
            where_clauses.append("type = %s")
            params.append(hash_type)

        if name_pattern:
            where_clauses.append("name ILIKE %s")
            params.append(f"%{name_pattern}%")

        if created_after:
            where_clauses.append("creation_time >= %s")
            params.append(created_after)

        if created_before:
            where_clauses.append("creation_time <= %s")
            params.append(created_before)

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Validate order_by column
        valid_order_columns = ['creation_time', 'name', 'type', 'id', 'provider']
        if order_by not in valid_order_columns:
            order_by = 'creation_time'

        order_direction = "DESC" if order_desc else "ASC"

        # Get total count for pagination
        count_sql = f"""
        SELECT COUNT(*) FROM {self.schema}.content_hash 
        {where_clause}
        """

        # Get paginated results
        data_sql = f"""
        SELECT * FROM {self.schema}.content_hash 
        {where_clause}
        ORDER BY {order_by} {order_direction}
        LIMIT %s OFFSET %s
        """

        with conn.cursor() as cur:
            # Get total count
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

            # Get paginated data
            data_params = params + [limit, offset]
            cur.execute(data_sql, data_params)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]

            results = []
            for row in rows:
                row_dict = dict(zip(colnames, row))
                results.append(self._row_to_content_hash(row_dict))

            return {
                "items": results,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(results) < total_count
            }

    @transactional
    def batch_add_content_hashes(self,
                                 content_hashes: List[Dict[str, Any]],
                                 conn=None) -> Dict[str, Any]:
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
        if not content_hashes:
            return {
                "total_processed": 0,
                "newly_inserted": 0,
                "already_existed": 0,
                "inserted_hashes": [],
                "duplicate_values": []
            }

        now = datetime.now(dt.UTC)
        rows_to_insert = []

        # Prepare data for batch insert
        for hash_data in content_hashes:
            if not hash_data.get("name") or not hash_data.get("value"):
                raise ValueError("Both 'name' and 'value' are required for each content hash")

            row_data = (
                hash_data["name"],
                hash_data["value"],
                hash_data.get("type", "SHA-256"),
                hash_data.get("provider"),
                hash_data.get("creation_time", now)
            )
            rows_to_insert.append(row_data)

        with conn.cursor() as cur:
            # First, check which hash values already exist
            hash_values = [row[1] for row in rows_to_insert]  # Extract hash values
            if hash_values:
                placeholders = ','.join(['%s'] * len(hash_values))
                existing_sql = f"""
                SELECT value FROM {self.schema}.content_hash 
                WHERE value IN ({placeholders})
                """
                cur.execute(existing_sql, hash_values)
                existing_values = {row[0] for row in cur.fetchall()}
            else:
                existing_values = set()

            # Filter out rows that would conflict (already exist)
            new_rows = []
            duplicate_values = []

            for row in rows_to_insert:
                hash_value = row[1]
                if hash_value in existing_values:
                    duplicate_values.append(hash_value)
                else:
                    new_rows.append(row)

            # Batch insert only new rows
            inserted_hashes = []
            if new_rows:
                sql = f"""
                INSERT INTO {self.schema}.content_hash (
                    name, value, type, provider, creation_time
                )
                VALUES %s
                RETURNING *
                """

                execute_values(cur, sql, new_rows)
                returned_rows = cur.fetchall()
                colnames = [desc[0] for desc in cur.description]

                for row in returned_rows:
                    row_dict = dict(zip(colnames, row))
                    inserted_hashes.append(self._row_to_content_hash(row_dict))

            return {
                "total_processed": len(content_hashes),
                "newly_inserted": len(inserted_hashes),
                "already_existed": len(duplicate_values),
                "inserted_hashes": inserted_hashes,
                "duplicate_values": duplicate_values
            }

    @transactional
    def get_content_hash_count(self, name_pattern: Optional[str] = None, hash_type: Optional[str] = None,
                               provider: Optional[str] = None, created_after: Optional[datetime] = None,
                               created_before: Optional[datetime] = None, conn=None) -> int:
        """
        Count content hash records with optional filters.

        Args:
            provider: Filter by provider (optional)
            hash_type: Filter by hash type (e.g., 'SHA-256', 'MD5') (optional)
            name_pattern: Filter by name pattern using ILIKE (optional)
            created_after: Filter by creation time >= this datetime (optional)
            created_before: Filter by creation time <= this datetime (optional)
            conn: Database connection (handled by decorator)

        Returns:
            Total count of matching content hash records
        """
        where_clauses = []
        params = []

        if provider:
            where_clauses.append("provider = %s")
            params.append(provider)

        if hash_type:
            where_clauses.append("type = %s")
            params.append(hash_type)

        if name_pattern:
            where_clauses.append("name ILIKE %s")
            params.append(f"%{name_pattern}%")

        if created_after:
            where_clauses.append("creation_time >= %s")
            params.append(created_after)

        if created_before:
            where_clauses.append("creation_time <= %s")
            params.append(created_before)

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
        SELECT COUNT(*) FROM {self.schema}.content_hash 
        {where_clause}
        """

        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchone()
            return result[0] if result else 0

    @transactional
    def clear_all_content_hashes(self, conn=None) -> int:
        """
        Delete ALL content hash records from the table.

        WARNING: This is a destructive operation that removes all content hash data.
        Use with caution, especially in production environments.

        Returns:
            Number of records deleted
        """
        sql = f"DELETE FROM {self.schema}.content_hash"

        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount