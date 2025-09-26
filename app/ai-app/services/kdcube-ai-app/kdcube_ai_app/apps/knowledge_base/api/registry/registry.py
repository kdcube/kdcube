# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# api/registry/registry.py
import urllib
from fastapi import Query, UploadFile, File, Response, Depends
from fastapi.responses import StreamingResponse
from werkzeug.utils import secure_filename
from pydantic import BaseModel, Field

import io
import re

# Add the KB import
from kdcube_ai_app.apps.knowledge_base.api.resolvers import (get_project,
                                                             get_kb_write_with_acct_dep,
                                                             get_kb_read_with_acct_dep, get_tenant)
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.tools.content_type import get_mime_type_enhanced, is_text_mime_type
from kdcube_ai_app.tools.datasource import URLDataElement, FileDataElement, IngestModifiers

"""
Registry API

File: api/registry/registry.py
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Callable, List, Dict, Any
import logging

# This will be set by the mount function
kb_getter: Optional[Callable] = None

logger = logging.getLogger("KBRegistry.API")

def get_kb_fn(router, project):
    """Get KB instance."""
    if not router.kb_getter:
        raise HTTPException(
            status_code=500,
            detail="KB getter not configured"
        )

    try:
        return router.kb_getter(project=project)
    except Exception as e:
        logger.error(f"Failed to get KB: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get knowledge base: {str(e)}"
        )

# Create router
router = APIRouter()

import functools
get_kb = functools.partial(get_kb_fn, router=router)

# ================================================================================
#                            KB REQUEST MODELS
# ================================================================================

class KBUploadResponse(BaseModel):
    success: bool
    resource_id: str
    resource_metadata: Dict[str, Any]
    message: str
    user_session_id: Optional[str] = None


class KBAddURLRequest(BaseModel):
    url: str
    parser_type: Optional[str] = "simple"
    provider: Optional[str] = None
    # Optional denoised content — if present, we'll store exactly this
    content: Optional[str] = None
    mime: Optional[str] = None

    # Pass-through ingest modifiers
    ingest: Optional[IngestModifiers] = None


class RNContentRequest(BaseModel):
    rn: str
    content_type: Optional[str] = "auto"


class RNContentResponse(BaseModel):
    rn: str
    content_type: str
    content: Any
    metadata: Dict[str, Any]

class SegmentContentRequest(BaseModel):
    rn: str
    segment_index: int
    highlight_citations: Optional[List[str]] = None
    include_context: bool = Field(default=True)
    context_lines: int = Field(default=3)


# ================================================================================
#                            KB ENDPOINTS
# ================================================================================

@router.post("/{project}/upload")
async def upload_file_to_kb(
        file: UploadFile = File(...),
        project: str = Depends(get_project),
        session: UserSession = Depends(get_kb_write_with_acct_dep())
):
    """Upload a file and immediately add it to the knowledge base - requires KB write access."""
    # TODO: should be tenant-aware
    kb = get_kb(project=project)
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        # Check file size (50MB limit)
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 50MB)")

        # Secure the filename
        original_filename = file.filename
        filename = secure_filename(file.filename)

        # Get file info with enhanced MIME type detection
        file_size = len(content)
        mime_type = get_mime_type_enhanced(original_filename, content)

        # For URLs that are saved as HTML files, update MIME type
        if original_filename.endswith('.html') or 'url' in original_filename.lower():
            mime_type = 'text/html'

        # Create FileDataElement and add to KB immediately
        element = FileDataElement(
            content=content,
            path=f"upload/{filename}",  # Virtual path
            filename=original_filename,
            mime=mime_type
        )

        # Add resource to KB
        resource_metadata = kb.add_resource(element)

        logger.info(f"Added resource to KB: {resource_metadata.id} with MIME type: {mime_type}; user: {session.username}")

        return KBUploadResponse(
            success=True,
            resource_id=resource_metadata.id,
            resource_metadata=resource_metadata.model_dump(),
            message=f"File uploaded and added to knowledge base as resource {resource_metadata.id}",
            user_session_id=session.session_id
        )

    except Exception as e:
        logger.error(f"Error uploading file to KB: {str(e)}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project}/resource/{resource_id}/preview")
async def preview_kb_resource(
        resource_id: str,
        version: Optional[str] = None,
        attached: Optional[str] = None,
        project: str = Depends(get_project),
        session=Depends(get_kb_read_with_acct_dep())):
    """Download/preview a KB resource - same pattern as your original file endpoint."""
    try:
        tenant = get_tenant()
        # TODO: Should be tenant-aware
        kb = get_kb(project=project)

        # Decode resource_id if it was URL encoded
        try:
            decoded_resource_id = urllib.parse.unquote(resource_id)
            logger.info(f"Decoded resource_id: {decoded_resource_id}; user: {session.username}")
        except:
            decoded_resource_id = resource_id

        # Get resource metadata
        resource = kb.get_resource(decoded_resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        if not version:
            version = resource.version

        # Get raw content
        content = kb.get_resource_content(decoded_resource_id, version, as_text=False)
        if content is None:
            raise HTTPException(status_code=404, detail="Resource content not found")

        # Ensure content is bytes
        if isinstance(content, str):
            content = content.encode('utf-8')

        # Determine MIME type
        mime_type = resource.mime or 'application/octet-stream'
        if not mime_type or mime_type == 'application/octet-stream':
            mime_type = get_mime_type_enhanced(resource.filename, content)

        logger.info(f"Using MIME type: {mime_type}; user: {session.username}")
        as_attachment = attached == "true" if attached else False

        if as_attachment:
            headers = {
                "Content-Disposition": f"attachment; filename={resource.filename}",
                "Content-Type": mime_type,
                "Content-Length": str(len(content))
            }
        else:
            headers = {
                "Content-Disposition": f"inline; filename={resource.filename}",
                "Content-Type": mime_type,
                "Content-Length": str(len(content))
            }

        logger.info(f"Response headers: {headers}; user: {session.username}")

        return StreamingResponse(
            io.BytesIO(content),
            media_type=mime_type,
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing KB resource: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

@router.options("/{project}/resource/{resource_id}/preview")
async def preview_kb_resource_options(resource_id: str,
                                      project: str = Depends(get_project),
                                      session=Depends(get_kb_read_with_acct_dep())):
    """Handle CORS preflight requests for preview endpoint."""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
            "Access-Control-Max-Age": "86400"
        }
    )

@router.post("/content/by-rn", response_model=RNContentResponse)
async def get_content_by_rn(request: RNContentRequest,
                            session=Depends(get_kb_read_with_acct_dep())):
    """Get content by Resource Name (RN) - requires KB read access."""
    try:
        rn = request.rn

        # Parse RN: ef:project:knowledge_base:stage:resource_id:version:...
        parts = rn.split(":")
        if len(parts) < 7 or parts[0] != "ef" or parts[3] != "knowledge_base":
            raise HTTPException(status_code=400, detail="Invalid RN format")

        tenant_name = parts[1]
        project_name = parts[2]
        stage = parts[4]
        resource_id = parts[5]
        version = parts[6]

        # Determine content type from stage and request
        if request.content_type == "auto":
            content_type = stage
        else:
            content_type = request.content_type

        # TODO: should be tenant-aware
        kb = get_kb(project=project_name)
        # Get content based on stage
        if stage == "raw":
            # Get resource metadata first to check MIME type
            resource_metadata = kb.get_resource(resource_id)
            if not resource_metadata:
                raise HTTPException(status_code=404, detail="Resource not found")

            mime_type = resource_metadata.mime or 'application/octet-stream'

            # Check if this is a binary/non-text file
            if not is_text_mime_type(mime_type):
                # For binary files, don't return content directly
                # Instead, provide info about how to access it
                metadata_dict = {
                    **resource_metadata.dict(),
                    "is_binary": True,
                    # {project}/resource/{resource_id}/preview
                    "preview_url": f"/api/kb/{project_name}/resource/{resource_id}/preview?version={version}",
                    "X": f"/api/kb/{project_name}/resource/{resource_id}/download?version={version}",
                    "message": f"Binary content ({mime_type}) - use preview_url or download_url to access"
                }

                return RNContentResponse(
                    rn=rn,
                    content_type=content_type,
                    content=f"[Binary content: {mime_type}] Use preview_url or download_url in metadata to access this file.",
                    metadata=metadata_dict
                )
            else:
                # For text files, get content as text
                content = kb.get_resource_content(resource_id, version, as_text=True)
                metadata_dict = resource_metadata.dict() if resource_metadata else {}

        elif stage == "extraction":
            # Extract filename from RN if present
            filename = parts[7] if len(parts) > 7 else None
            extraction_module = kb.get_extraction_module()

            if filename:
                content_bytes = extraction_module.get_asset(resource_id, version, filename)
                if content_bytes:
                    try:
                        content = content_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # Handle binary extraction assets
                        metadata_dict = {
                            "stage": stage,
                            "resource_id": resource_id,
                            "version": version,
                            "filename": filename,
                            "is_binary": True,
                            "message": f"Binary extraction asset: {filename}"
                        }
                        content = f"[Binary extraction asset: {filename}] - Cannot display as text"
                else:
                    content = None
            else:
                # Get first extraction content (should be text/markdown)
                extraction_results = extraction_module.get_extraction_results(resource_id, version)
                if extraction_results:
                    content = extraction_module.get_extraction_content(resource_id, version, 0)
                else:
                    content = None

            if 'metadata_dict' not in locals():
                metadata_dict = {"stage": stage, "resource_id": resource_id, "version": version}

        elif stage == "segmentation":
            # Handle segment-specific content
            segment_type = parts[7] if len(parts) > 7 else "continuous"
            segmentation_module = kb.get_segmentation_module()

            from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType
            seg_type = SegmentType.CONTINUOUS if segment_type == "continuous" else SegmentType.RETRIEVAL

            segments = segmentation_module.get_segments_by_type(resource_id, version, seg_type)
            content = {"segments": segments, "segment_type": segment_type}
            metadata_dict = {"stage": stage, "resource_id": resource_id, "version": version,
                             "segment_type": segment_type}

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported stage: {stage}")

        if content is None:
            raise HTTPException(status_code=404, detail="Content not found for the given RN")

        return RNContentResponse(
            rn=rn,
            content_type=content_type,
            content=content,
            metadata=metadata_dict
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting content by RN: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project}/resources")
async def list_kb_resources(project: str = Depends(get_project),
                            session=Depends(get_kb_read_with_acct_dep())):
    """List all resources in the knowledge base - requires KB read access."""
    try:
        # TODO: should be tenant-aware
        kb = get_kb(project=project)
        resources = kb.list_resources()

        # Convert to serializable format with processing status
        resource_list = []
        for resource in resources:
            # Get extraction info with better error handling
            try:
                extraction_info = kb.storage.get_extraction_results(resource.id, resource.version)
                resource.extraction_info = extraction_info or []
            except Exception as e:
                logger.warning(f"Could not get extraction info for {resource.id}: {e}; user: {session.username}")
                resource.extraction_info = []

            resource_dict = resource.model_dump()

            # Add processing status
            try:
                processing_status = kb.get_processing_status(resource.id, resource.version)
                resource_dict["processing_status"] = processing_status
                resource_dict["fully_processed"] = all([
                    processing_status.get("extraction", False),
                    processing_status.get("segmentation", False)
                ])
            except Exception as e:
                logger.warning(f"Could not get processing status for {resource.id}: {e}; user: {session.username}")
                resource_dict["processing_status"] = {}
                resource_dict["fully_processed"] = False

            # Add RNs for easy access
            resource_dict["rns"] = {
                "raw": f"ef:{kb.tenant}:{kb.project}:knowledge_base:raw:{resource.id}:{resource.version}",
                "extraction": f"ef:{kb.tenant}:{kb.project}:knowledge_base:extraction:{resource.id}:{resource.version}",
                "segmentation": f"ef:{kb.tenant}:{kb.project}:knowledge_base:segmentation:{resource.id}:{resource.version}"
            }

            resource_list.append(resource_dict)

        logger.info(f"Returning {len(resource_list)} resources; user: {session.username}")
        return {
            "resources": resource_list,
            "total_count": len(resource_list),
            "kb_stats": kb.get_stats(),
            "user": session.username
        }

    except Exception as e:
        logger.error(f"Error listing KB resources: {e}; user: {session.username}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project}/resource/{resource_id}/content")
async def get_kb_resource_content(
        resource_id: str,
        version: Optional[str] = Query(None),
        content_type: str = Query("raw", description="Type: raw, extraction, segments"),
        project: str = Depends(get_project),
        session=Depends(get_kb_read_with_acct_dep())):
    """Get content for a KB resource - requires KB read access."""
    try:
        # TODO: should be tenant-aware
        kb = get_kb(project=project)
        if not version:
            resource = kb.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")
            version = resource.version

        if content_type == "raw":
            content = kb.get_resource_content(resource_id, version, as_text=True)
            return {"resource_id": resource_id, "version": version, "content": content, "type": "raw", "user": session.username}

        elif content_type == "extraction":
            assets = kb.get_extraction_assets_for_resource(resource_id, version)
            if not assets or not assets.get("asset_contents", {}).get("content"):
                raise HTTPException(status_code=404, detail="No extraction data found")

            content_files = assets["asset_contents"]["content"]
            # Get first extraction file
            extraction_content = next(iter(content_files.values()), None)

            return {
                "resource_id": resource_id,
                "version": version,
                "content": extraction_content,
                "type": "extraction",
                "available_files": list(content_files.keys()),
                "user": session.username
            }

        elif content_type == "segments":
            segmentation_module = kb.get_segmentation_module()
            segments = segmentation_module.get_continuous_segments(resource_id, version)

            return {
                "resource_id": resource_id,
                "version": version,
                "segments": segments,
                "type": "segments",
                "segment_count": len(segments) if segments else 0,
                "user": session.username
            }

        else:
            raise HTTPException(status_code=400, detail="Invalid content_type. Use: raw, extraction, or segments")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting KB resource content: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project}/resource/{resource_id}/download")
async def download_kb_resource(resource_id: str,
                               version: Optional[str] = None,
                               project: str = Depends(get_project),
                               session=Depends(get_kb_read_with_acct_dep())):
    """Download a KB resource as attachment - requires KB read access."""
    return await preview_kb_resource(resource_id, version, attached="true", project=project, session=session)

@router.delete("/{project}/resource/{resource_id}")
async def delete_kb_resource(resource_id: str,
                             project: str = Depends(get_project),
                             session=Depends(get_kb_read_with_acct_dep())):
    """Delete a KB resource - requires KB write access."""
    try:
        # TODO: should be tenant-aware
        kb = get_kb(project=project)
        # Check if resource exists
        resource = kb.get_resource(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Delete the resource
        success = kb.delete_resource(resource_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete resource")

        logger.info(f"Resource {resource_id} deleted by user: {session.username}")
        return {"success": True, "message": f"Resource {resource_id} deleted successfully", "user": session.username}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting KB resource: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{project}/add-url")
async def add_url_to_kb(
        request: KBAddURLRequest,
        project: str = Depends(get_project),
        session=Depends(get_kb_read_with_acct_dep()),
):
    kb = get_kb(project=project)

    try:
        # Default ingest if not provided
        ingest = request.ingest or IngestModifiers()

        # If caller supplied denoised content, prefer it and set sensible defaults
        use_content = request.content is not None
        element = URLDataElement(
            url=request.url,
            parser_type=request.parser_type or "simple",
            provider=request.provider,
            mime=(request.mime or ("text/html" if use_content else None)),
            content=(request.content if use_content else None),
            ingest=(
                ingest if ingest else IngestModifiers()
            ),
        )

        md = kb.add_resource(element)

        # Probe path: md can be None if version_policy == "probe_only" and nothing ready
        if md is None:
            return KBUploadResponse(
                success=False,
                resource_id=None,
                resource_metadata=None,
                message="No existing version satisfies requested stage readiness.",
                user_session_id=session.session_id,
            )

        # Normalize MIME for URL fallback
        if md.mime == "application/octet-stream" and (request.mime or use_content):
            updated = md.model_dump()
            updated["mime"] = request.mime or "text/html"
            kb.storage.save_resource_metadata(md.id, updated)
            md = kb.get_resource(md.id)

        return KBUploadResponse(
            success=True,
            resource_id=md.id,
            resource_metadata=md.model_dump(),
            message=(
                "URL added (new version)"
                if md.status is None
                else f"URL {('ready' if md.status in ('probe_ready','reused_ready') else md.status)}"
            ),
            user_session_id=session.session_id,
        )

    except Exception as e:
        logger.error(f"Error adding URL to KB: {str(e)}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{project}/content/segment")
async def get_segment_content(request: SegmentContentRequest,
                              project: str = Depends(get_project),
                              session=Depends(get_kb_read_with_acct_dep())):
    """Get specific segment content with context and highlighting - requires KB read access."""
    try:
        # TODO: should be tenant-aware
        kb = get_kb(project=project)

        # Parse RN to get resource info
        rn_parts = request.rn.split(":")
        if len(rn_parts) < 6:
            raise HTTPException(status_code=400, detail="Invalid RN format")

        resource_id = rn_parts[4]
        version = rn_parts[5]

        # Get segmentation info
        segmentation_module = kb.get_segmentation_module()
        base_segments = segmentation_module.get_base_segments(resource_id, version)

        if request.segment_index >= len(base_segments):
            raise HTTPException(status_code=404, detail="Segment index out of range")

        target_segment = base_segments[request.segment_index]

        # Get segment content
        segment_content = target_segment.text

        # Apply highlighting if requested
        highlighted_content = segment_content
        if request.highlight_citations:
            for citation in request.highlight_citations:
                if citation.strip():
                    escaped_citation = re.escape(citation)
                    pattern = f"({escaped_citation})"
                    highlighted_content = re.sub(
                        pattern,
                        '<mark class="bg-yellow-200 px-1 rounded">\\1</mark>',
                        highlighted_content,
                        flags=re.IGNORECASE
                    )

        # Get context if requested
        context_before = None
        context_after = None
        if request.include_context:
            if request.segment_index > 0:
                context_segments = base_segments[
                                   max(0, request.segment_index - request.context_lines):request.segment_index]
                context_before = "\n".join([seg.text for seg in context_segments])

            if request.segment_index < len(base_segments) - 1:
                context_segments = base_segments[request.segment_index + 1:min(len(base_segments),
                                                                               request.segment_index + 1 + request.context_lines)]
                context_after = "\n".join([seg.text for seg in context_segments])

        return {
            "segment_content": segment_content,
            "highlighted_content": highlighted_content,
            "navigation_info": {
                "start_line": target_segment.start_line_num,
                "end_line": target_segment.end_line_num,
                "start_pos": target_segment.start_position,
                "end_pos": target_segment.end_position,
                "citations": request.highlight_citations or [],
                "text": segment_content,
                "heading": target_segment.heading,
                "subheading": target_segment.subheading
            },
            "context_before": context_before,
            "context_after": context_after,
            "segment_index": request.segment_index,
            "total_segments": len(base_segments),
            "user": session.username
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting segment content: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))