# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# knowledge_base/tenant.py
"""
TenantProjects - Manages projects for a tenant using storage backend

File: kdcube_ai_app/apps/knowledge_base/tenant.py
"""

import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from urllib.parse import urlparse

from kdcube_ai_app.storage.storage import IStorageBackend, create_storage_backend
from kdcube_ai_app.apps.knowledge_base.core import KnowledgeBase
from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord
from kdcube_ai_app.apps.knowledge_base.db.providers.tenant_db import TenantDB

logger = logging.getLogger("TenantProjects")


class ProjectMetadata:
    """Project metadata model."""

    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "")
        self.description = kwargs.get("description", "")
        self.created_at = kwargs.get("created_at", "")
        self.created_by = kwargs.get("created_by", "<user>")
        self.updated_at = kwargs.get("updated_at")
        self.updated_by = kwargs.get("updated_by")
        self.version = kwargs.get("version", "1.0")
        self.status = kwargs.get("status", "active")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "version": self.version,
            "status": self.status
        }
        if self.updated_at:
            data["updated_at"] = self.updated_at
        if self.updated_by:
            data["updated_by"] = self.updated_by
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectMetadata':
        """Create from dictionary."""
        return cls(**data)


class TenantProjects:
    """
    Manages projects for a tenant using a storage backend.

    This component provides project management functionality that is:
    - Storage backend agnostic (works with file, S3, etc.)
    - Database-aware for schema management
    - Independent of in-memory caches
    - Multi-tenant ready
    - Persistent across application restarts
    """

    def __init__(self,
                 storage_backend: IStorageBackend,
                 tenant_db: Optional[TenantDB] = None,
                 tenant_id: Optional[str] = None,
                 embedding_model_factory: Optional[Callable[[], ModelRecord]] = None):
        """
        Initialize TenantProjects.

        Args:
            storage_backend: Storage backend for this tenant
            tenant_db: TenantDB instance for database operations (optional)
            tenant_id: Optional tenant identifier for logging
            embedding_model_factory: Factory function to create embedding models for KBs
        """
        self.storage_backend = storage_backend
        self.tenant_id = tenant_id or "default"
        self.embedding_model_factory = embedding_model_factory

        # Initialize TenantDB if not provided
        if tenant_db is None:
            self.tenant_db = TenantDB(tenant_id=self.tenant_id)
        else:
            self.tenant_db = tenant_db

        logger.info(f"Initialized TenantProjects for tenant '{self.tenant_id}' with database support")

    def _get_project_storage_path(self, project_name: str) -> str:
        """Get the base storage path for a project."""
        return f"{self.tenant_id}/projects/{project_name}"

    def _get_project_metadata_path(self, project_name: str) -> str:
        """Get the metadata.json path for a project."""
        return f"{self._get_project_storage_path(project_name)}/metadata.json"

    def _validate_project_name(self, project_name: str) -> None:
        """Validate project name format."""
        if not project_name or not project_name.strip():
            raise ValueError("Project name cannot be empty")

        # Basic validation for project name (alphanumeric, hyphens, underscores)
        if not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
            raise ValueError(
                "Project name can only contain letters, numbers, hyphens, and underscores"
            )

        if len(project_name) > 100:
            raise ValueError("Project name cannot exceed 100 characters")

    def project_exists(self,
                       project_name: str) -> bool:
        """Check if a project exists."""
        try:
            metadata_path = self._get_project_metadata_path(project_name)
            return self.storage_backend.exists(metadata_path)
        except Exception as e:
            logger.error(f"Error checking project existence for '{project_name}': {e}")
            return False

    def list_projects(self) -> List[ProjectMetadata]:
        """
        List all projects for this tenant.

        Returns:
            List of ProjectMetadata objects
        """
        projects = []

        try:
            # Check if projects directory exists
            projects_dir = f"{self.tenant_id}/projects"
            if not self.storage_backend.exists(projects_dir):
                logger.info(f"No projects directory found for tenant '{self.tenant_id}'")
                return projects

            # List all items in projects directory
            try:
                project_names = self.storage_backend.list_dir(projects_dir)
            except Exception as e:
                logger.warning(f"Could not list projects directory: {e}")
                return projects

            for project_name in project_names:
                try:
                    metadata = self.get_project_metadata(project_name)
                    if metadata:
                        projects.append(metadata)
                    else:
                        # Create basic metadata for projects without metadata.json
                        logger.warning(f"Project '{project_name}' has no metadata, creating basic entry")
                        basic_metadata = ProjectMetadata(
                            name=project_name,
                            description="Legacy project (no metadata available)",
                            created_at="unknown",
                            created_by="<unknown>",
                            version="unknown",
                            status="unknown"
                        )
                        projects.append(basic_metadata)

                except Exception as e:
                    logger.error(f"Error processing project '{project_name}': {e}")
                    # Still include project but with error info
                    error_metadata = ProjectMetadata(
                        name=project_name,
                        description=f"Error loading project metadata: {str(e)}",
                        created_at="unknown",
                        created_by="<unknown>",
                        version="unknown",
                        status="error"
                    )
                    projects.append(error_metadata)

        except Exception as e:
            logger.error(f"Failed to list projects for tenant '{self.tenant_id}': {e}")

        return projects

    def get_project_metadata(self, project_name: str) -> Optional[ProjectMetadata]:
        """
        Get metadata for a specific project.

        Args:
            project_name: Name of the project

        Returns:
            ProjectMetadata object or None if not found
        """
        try:
            metadata_path = self._get_project_metadata_path(project_name)

            if not self.storage_backend.exists(metadata_path):
                return None

            # FIXED: Use read_text instead of load
            metadata_content = self.storage_backend.read_text(metadata_path)
            if not metadata_content:
                return None

            metadata_dict = json.loads(metadata_content)
            return ProjectMetadata.from_dict(metadata_dict)

        except Exception as e:
            logger.error(f"Error getting metadata for project '{project_name}': {e}")
            return None

    def create_project(self,
                      project_name: str,
                      description: str = "",
                      created_by: str = "<user>",
                      component_type: str = "knowledge_base") -> ProjectMetadata:
        """
        Create a new project.

        Args:
            project_name: Name of the project
            description: Project description
            created_by: User who created the project

        Returns:
            ProjectMetadata object for the created project

        Raises:
            ValueError: If project name is invalid or project already exists
        """
        # Validate project name
        self._validate_project_name(project_name)

        # Check if project already exists
        if self.project_exists(project_name):
            raise ValueError(f"Project '{project_name}' already exists")

        # Track what we've created for rollback purposes
        storage_created = False
        database_created = False

        try:
            # Step 1: Create database schema first
            logger.info(f"Creating database schema for project '{project_name}'")
            db_result = self.tenant_db.create_project_db(project_name, component_type)
            database_created = True
            logger.info(f"Database schema created successfully: {db_result}")

            # Step 2: Create project metadata in storage
            project_metadata = ProjectMetadata(
                name=project_name,
                description=description.strip(),
                created_at=datetime.utcnow().isoformat(),
                created_by=created_by,
                version="1.0",
                status="active"
            )

            # Create project directory structure
            metadata_path = self._get_project_metadata_path(project_name)
            metadata_content = json.dumps(project_metadata.to_dict(), indent=2, ensure_ascii=False)

            # FIXED: Use write_text instead of save with bytes
            self.storage_backend.write_text(metadata_path, metadata_content)
            storage_created = True

            logger.info(f"Created project '{project_name}' for tenant '{self.tenant_id}' with database and storage")
            return project_metadata

        except Exception as e:
            logger.error(f"Failed to create project '{project_name}': {e}")

            # Rollback operations in reverse order
            if storage_created:
                try:
                    logger.info(f"Rolling back storage for project '{project_name}'")
                    project_path = self._get_project_storage_path(project_name)
                    self.storage_backend.delete(project_path)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback storage for '{project_name}': {rollback_error}")

            if database_created:
                try:
                    logger.info(f"Rolling back database schema for project '{project_name}'")
                    self.tenant_db.delete_project_db(project_name, component_type)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback database for '{project_name}': {rollback_error}")

            raise Exception(f"Failed to create project: {str(e)}")

    def update_project(self,
                       project_name: str,
                       updates: Dict[str, Any],
                       updated_by: str = "<user>") -> Optional[ProjectMetadata]:
        """
        Update project metadata.

        Args:
            project_name: Name of the project
            updates: Dictionary of fields to update
            updated_by: User making the update

        Returns:
            Updated ProjectMetadata object or None if project not found
        """
        try:
            # Get existing metadata
            metadata = self.get_project_metadata(project_name)
            if not metadata:
                return None

            # Update allowed fields
            if "description" in updates:
                metadata.description = updates["description"].strip()

            # Update modification info
            metadata.updated_at = datetime.utcnow().isoformat()
            metadata.updated_by = updated_by

            # Save updated metadata
            metadata_path = self._get_project_metadata_path(project_name)
            metadata_content = json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False)

            # FIXED: Use write_text instead of save with bytes
            self.storage_backend.write_text(metadata_path, metadata_content)

            logger.info(f"Updated project '{project_name}' for tenant '{self.tenant_id}'")
            return metadata

        except Exception as e:
            logger.error(f"Failed to update project '{project_name}': {e}")
            raise Exception(f"Failed to update project: {str(e)}")

    def delete_project(self,
                       project_name: str) -> bool:
        """
        Delete a project and all its data.

        Args:
            project_name: Name of the project to delete

        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            if not self.project_exists(project_name):
                logger.warning(f"Project '{project_name}' does not exist, cannot delete")
                return False

            # Track deletion progress for partial cleanup handling
            storage_deleted = False
            database_deleted = False
            deletion_errors = []

            # Step 1: Delete storage first (less critical to rollback)
            try:
                logger.info(f"Deleting storage for project '{project_name}'")
                project_path = self._get_project_storage_path(project_name)
                self.storage_backend.delete(project_path)
                storage_deleted = True
                logger.info(f"Storage deleted successfully for project '{project_name}'")
            except Exception as e:
                error_msg = f"Failed to delete storage for project '{project_name}': {e}"
                logger.error(error_msg)
                deletion_errors.append(error_msg)

            # Step 2: Delete database schema
            try:
                component_type = "knowledge_base"
                logger.info(f"Deleting database schema for project '{project_name}'")
                db_result = self.tenant_db.delete_project_db(project_name, component_type)
                database_deleted = True
                logger.info(f"Database schema deleted successfully: {db_result}")
            except Exception as e:
                error_msg = f"Failed to delete database schema for project '{project_name}': {e}"
                logger.error(error_msg)
                deletion_errors.append(error_msg)

            # Determine overall success
            if storage_deleted and database_deleted:
                logger.info(f"Successfully deleted project '{project_name}' (storage and database)")
                return True
            elif storage_deleted or database_deleted:
                logger.warning(f"Partial deletion of project '{project_name}': storage={storage_deleted}, database={database_deleted}")
                logger.warning(f"Deletion errors: {deletion_errors}")
                # Consider this a success if at least one component was deleted
                # The remaining component can be cleaned up later
                return True
            else:
                logger.error(f"Failed to delete any components of project '{project_name}': {deletion_errors}")
                return False

        except Exception as e:
            logger.error(f"Unexpected error during deletion of project '{project_name}': {e}")
            return False

    def get_project_kb_storage_path(self, project_name: str) -> str:
        """
        Get the storage path for a project's KB data.

        Args:
            project_name: Name of the project

        Returns:
            Storage path for the project's KB data
        """
        return f"{self._get_project_storage_path(project_name)}/kb"

    def create_kb_for_project(self, project_name: str) -> Optional['KnowledgeBase']:
        """
        Create a KnowledgeBase instance for a project.

        Args:
            project_name: Name of the project

        Returns:
            KnowledgeBase instance or None if project doesn't exist or creation fails
        """
        try:
            if not self.project_exists(project_name):
                logger.error(f"Cannot create KB for non-existent project '{project_name}'")
                return None

            # Create a sub-storage backend for this project's KB data
            kb_storage_path = self.get_project_kb_storage_path(project_name)

            # Create KB storage backend that points to the project's KB directory
            kb_backend = self._create_project_kb_backend(kb_storage_path)

            # Get embedding model
            embedding_model = None
            embedding_model = self.embedding_model_factory()

            # Create KnowledgeBase instance
            kb = KnowledgeBase(
                tenant=self.tenant_id,
                project=project_name,
                storage_backend=kb_backend,
                embedding_model=embedding_model
            )

            logger.info(f"Created KB instance for project '{project_name}'")
            return kb

        except Exception as e:
            logger.error(f"Failed to create KB for project '{project_name}': {e}")
            return None

    def _create_project_kb_backend(self, kb_storage_path: str) -> IStorageBackend:
        """Create a storage backend for a project's KB data."""
        # This creates a "sub-backend" that operates within the project's KB directory

        if hasattr(self.storage_backend, 'base_path'):
            # For file backends, create a new backend with updated base path
            base_path = getattr(self.storage_backend, 'base_path', '')
            new_base_path = f"{base_path}/{kb_storage_path}".replace('//', '/')
            return create_storage_backend(f"file://{new_base_path}")

        elif hasattr(self.storage_backend, 'bucket'):
            # For S3 backends, create a new backend with updated prefix
            bucket = getattr(self.storage_backend, 'bucket', '')
            prefix = getattr(self.storage_backend, 'prefix', '')
            new_prefix = f"{prefix}/{kb_storage_path}".strip('/')
            return create_storage_backend(f"s3://{bucket}/{new_prefix}")

        else:
            # Generic approach - create a wrapper backend
            return ProjectKBStorageWrapper(self.storage_backend, kb_storage_path)

    def get_project_stats(self, project_name: str) -> Optional[Dict[str, Any]]:
        """
        Get statistics for a project.

        Args:
            project_name: Name of the project

        Returns:
            Dictionary with project statistics or None if project not found
        """
        try:
            kb = self.create_kb_for_project(project_name)
            if not kb:
                return None

            stats = kb.get_stats()
            return stats

        except Exception as e:
            logger.error(f"Failed to get stats for project '{project_name}': {e}")
            return {"error": str(e)}

    def provision_system_components(self) -> Dict[str, Any]:
        """
        Provision system-level database components for this tenant.

        This should be called once during tenant initialization.

        Returns:
            Dictionary with provisioning results
        """
        try:
            return self.tenant_db.provision_system_components()
        except Exception as e:
            logger.error(f"Failed to provision system components: {e}")
            return {
                "status": "error",
                "operation": "provision_system_components",
                "tenant_id": self.tenant_id,
                "error": str(e)
            }

    def cleanup_orphaned_resources(self) -> Dict[str, Any]:
        """
        Clean up orphaned database schemas and storage that don't match.

        This finds projects that exist in database but not storage (or vice versa)
        and cleans them up.

        Returns:
            Dictionary with cleanup results
        """
        try:
            logger.info(f"Starting orphaned resource cleanup for tenant '{self.tenant_id}'")

            # Get list of projects from storage
            storage_projects = []
            try:
                projects_metadata = self.list_projects()
                storage_projects = [p.name for p in projects_metadata]
            except Exception as e:
                logger.error(f"Could not list storage projects during cleanup: {e}")

            # Clean up orphaned database schemas
            db_cleanup_result = self.tenant_db.cleanup_orphaned_dbs(storage_projects)

            # TODO: Add storage cleanup for projects that exist in DB but not storage
            # This would require the TenantDB to provide a list of existing project databases

            cleanup_summary = {
                "status": "success",
                "operation": "cleanup_orphaned_resources",
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "storage_projects_found": len(storage_projects),
                "database_cleanup": db_cleanup_result
            }

            logger.info(f"Completed orphaned resource cleanup: {cleanup_summary}")
            return cleanup_summary

        except Exception as e:
            logger.error(f"Error during orphaned resource cleanup: {e}")
            return {
                "status": "error",
                "operation": "cleanup_orphaned_resources",
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }

    def get_tenant_health(self) -> Dict[str, Any]:
        """
        Get comprehensive health status for the tenant (storage + database).

        Returns:
            Dictionary with health information
        """
        try:
            health_info = {
                "status": "healthy",
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "components": {}
            }

            # Check storage health
            try:
                # Basic storage check - try to list projects directory
                projects_dir = f"{self.tenant_id}/projects"
                storage_accessible = self.storage_backend.exists(projects_dir)
                health_info["components"]["storage"] = {
                    "status": "healthy" if storage_accessible else "warning",
                    "accessible": storage_accessible,
                    "backend_type": type(self.storage_backend).__name__
                }
            except Exception as e:
                health_info["components"]["storage"] = {
                    "status": "unhealthy",
                    "error": str(e)
                }
                health_info["status"] = "degraded"

            # Check database health
            try:
                db_health = self.tenant_db.get_db_health()
                health_info["components"]["database"] = db_health
                if db_health.get("status") != "healthy":
                    health_info["status"] = "degraded"
            except Exception as e:
                health_info["components"]["database"] = {
                    "status": "unhealthy",
                    "error": str(e)
                }
                health_info["status"] = "degraded"

            # Check project consistency
            try:
                projects = self.list_projects()
                health_info["components"]["projects"] = {
                    "status": "healthy",
                    "total_projects": len(projects),
                    "project_names": [p.name for p in projects]
                }
            except Exception as e:
                health_info["components"]["projects"] = {
                    "status": "unhealthy",
                    "error": str(e)
                }
                health_info["status"] = "degraded"

            return health_info

        except Exception as e:
            logger.error(f"Error checking tenant health: {e}")
            return {
                "status": "unhealthy",
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }

    def validate_project_consistency(self, project_name: str) -> Dict[str, Any]:
        """
        Validate that a project's storage and database components are consistent.

        Args:
            project_name: Name of the project to validate

        Returns:
            Dictionary with validation results
        """
        try:
            validation_result = {
                "project_name": project_name,
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "storage_exists": False,
                "database_exists": False,
                "consistent": False,
                "issues": []
            }

            # Check storage existence
            try:
                validation_result["storage_exists"] = self.project_exists(project_name)
            except Exception as e:
                validation_result["issues"].append(f"Storage check failed: {str(e)}")

            # Check database existence
            try:
                validation_result["database_exists"] = self.tenant_db.project_db_exists(project_name)
            except Exception as e:
                validation_result["issues"].append(f"Database check failed: {str(e)}")

            # Determine consistency
            storage_exists = validation_result["storage_exists"]
            database_exists = validation_result["database_exists"]

            if storage_exists and database_exists:
                validation_result["consistent"] = True
                validation_result["status"] = "consistent"
            elif not storage_exists and not database_exists:
                validation_result["consistent"] = True
                validation_result["status"] = "consistently_missing"
            else:
                validation_result["consistent"] = False
                validation_result["status"] = "inconsistent"
                if storage_exists and not database_exists:
                    validation_result["issues"].append("Storage exists but database schema missing")
                elif database_exists and not storage_exists:
                    validation_result["issues"].append("Database schema exists but storage missing")

            return validation_result

        except Exception as e:
            logger.error(f"Error validating project consistency for '{project_name}': {e}")
            return {
                "project_name": project_name,
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                "status": "validation_error",
                "error": str(e)
            }


class ProjectKBStorageWrapper:
    """
    A storage backend wrapper that prefixes all paths with a project KB path.
    This allows creating project-scoped storage backends from a parent backend.
    """

    def __init__(self, parent_backend: IStorageBackend, kb_storage_path: str):
        self.parent_backend = parent_backend
        self.kb_storage_path = kb_storage_path.strip('/')

    def _prefixed_path(self, path: str) -> str:
        """Add the KB storage path prefix to a path."""
        clean_path = path.strip('/')
        if clean_path:
            return f"{self.kb_storage_path}/{clean_path}"
        return self.kb_storage_path

    def exists(self, path: str) -> bool:
        return self.parent_backend.exists(self._prefixed_path(path))

    def write_text(self, path: str, content: str) -> str:
        """FIXED: Use write_text instead of save"""
        return self.parent_backend.write_text(self._prefixed_path(path), content)

    def write_bytes(self, path: str, data: bytes) -> str:
        """FIXED: Use write_bytes instead of save"""
        return self.parent_backend.write_bytes(self._prefixed_path(path), data)

    def read_text(self, path: str) -> str:
        """FIXED: Use read_text instead of load"""
        return self.parent_backend.read_text(self._prefixed_path(path))

    def read_bytes(self, path: str) -> bytes:
        """FIXED: Use read_bytes instead of load"""
        return self.parent_backend.read_bytes(self._prefixed_path(path))

    def delete(self, path: str) -> bool:
        return self.parent_backend.delete(self._prefixed_path(path))

    def list_dir(self, path: str = "") -> List[str]:
        return self.parent_backend.list_dir(self._prefixed_path(path))

    def get_size(self, path: str) -> int:
        return self.parent_backend.get_size(self._prefixed_path(path))

    def get_modified_time(self, path: str) -> float:
        return self.parent_backend.get_modified_time(self._prefixed_path(path))
