# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/content_type.py
import requests, os, mimetypes
from urllib.parse import urlparse
from bs4 import BeautifulSoup

import logging
logger = logging.getLogger()

def get_mime_type_enhanced(filename: str, content: bytes = None) -> str:
    """Enhanced MIME type detection with content analysis fallback."""
    import mimetypes
    import magic  # You may need to install python-magic: pip install python-magic

    # First try filename-based detection
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type:
        return mime_type

    # Try magic-based detection if content is available
    if content:
        try:
            mime_type = magic.from_buffer(content, mime=True)
            if mime_type and mime_type != 'application/octet-stream':
                return mime_type
        except:
            pass  # Fall back to extension-based detection

    # Fallback to extension-based detection
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    mime_map = {
        'pdf': 'application/pdf',
        'txt': 'text/plain',
        'md': 'text/markdown',
        'csv': 'text/csv',
        'json': 'application/json',
        'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls': 'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'ppt': 'application/vnd.ms-powerpoint',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'html': 'text/html',
        'htm': 'text/html',
        'xml': 'application/xml',
        'yaml': 'application/x-yaml',
        'yml': 'application/x-yaml',
        'zip': 'application/zip',
        'rar': 'application/x-rar-compressed',
        '7z': 'application/x-7z-compressed',
        'tar': 'application/x-tar',
        'gz': 'application/gzip',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'svg': 'image/svg+xml',
        'mp4': 'video/mp4',
        'avi': 'video/x-msvideo',
        'mov': 'video/quicktime',
        'mp3': 'audio/mpeg',
        'wav': 'audio/wav',
        'flac': 'audio/flac'
    }
    return mime_map.get(ext, 'application/octet-stream')

def fetch_url_with_content_type(url: str) -> tuple[bytes, str, str]:
    """
    Fetch URL content and detect content type and filename.

    Returns:
        tuple: (content_bytes, content_type, filename)
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        # Get content type from headers
        content_type = response.headers.get('content-type', '').split(';')[0].strip()

        # Try to get filename from Content-Disposition header
        filename = None
        content_disposition = response.headers.get('content-disposition', '')
        if content_disposition:
            import re
            filename_match = re.search(r'filename[*]?=([^;]+)', content_disposition)
            if filename_match:
                filename = filename_match.group(1).strip('"\'')

        # If no filename from headers, try to extract from URL
        if not filename:
            parsed_url = urlparse(url)
            filename = os.path.basename(parsed_url.path)
            if not filename or '.' not in filename:
                filename = None

        # Read content
        content_bytes = response.content

        return content_bytes, content_type, filename

    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        # Fallback to existing method
        raise

def guess_mime_from_url(url: str) -> str:
    """
    Guess MIME type from URL extension.

    Args:
        url: The URL to analyze

    Returns:
        MIME type string or None
    """
    try:
        parsed_url = urlparse(url)
        path = parsed_url.path
        if path:
            mime_type, _ = mimetypes.guess_type(path)
            return mime_type
    except Exception:
        pass
    return None

def is_text_mime_type(mime_type: str) -> bool:
    """
    Determine if a MIME type represents text content that can be safely decoded as text.

    Args:
        mime_type: The MIME type to check

    Returns:
        True if the content should be treated as text, False for binary content
    """
    if not mime_type:
        return False

    mime_type = mime_type.lower().strip()

    # Common text MIME types
    text_mime_types = {
        "text/plain",
        "text/html",
        "text/xml",
        "text/css",
        "text/javascript",
        "text/csv",
        "text/markdown",
        "text/rtf",
        "application/json",
        "application/xml",
        "application/javascript",
        "application/xhtml+xml",
        "application/rss+xml",
        "application/atom+xml",
        "application/ld+json",
        "application/x-yaml",
        "application/yaml"
    }

    # Check exact matches
    if mime_type in text_mime_types:
        return True

    # Check if it starts with "text/"
    if mime_type.startswith("text/"):
        return True

    # Check for charset parameter (usually indicates text)
    if "charset=" in mime_type:
        return True

    # Default to binary for everything else (PDFs, images, videos, etc.)
    return False

def is_html_mime_type(mime_type: str) -> bool:
    """Check if MIME type is HTML-based."""
    if not mime_type:
        return False

    mime_type = mime_type.lower().strip()
    html_mime_types = {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml"
    }

    return mime_type in html_mime_types

def extension_from_mime(mime_type: str) -> str:
    """Best-effort file extension for a MIME type."""
    mime_type = (mime_type or "").lower().strip()
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type == "image/png":
        return "png"
    if mime_type == "image/jpeg":
        return "jpg"
    if mime_type == "image/gif":
        return "gif"
    if mime_type == "image/webp":
        return "webp"
    if mime_type == "text/plain":
        return "txt"
    if mime_type == "text/markdown":
        return "md"
    if mime_type == "text/csv":
        return "csv"
    if mime_type == "application/json":
        return "json"
    return "bin"

def extract_title_from_html(html_content: str) -> str:
    """Extract title from HTML content."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        if soup.title and soup.title.text:
            return soup.title.text.strip()
    except Exception:
        pass
    return "Untitled"
