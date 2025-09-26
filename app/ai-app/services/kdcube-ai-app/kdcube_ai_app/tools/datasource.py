# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/datasource.py
from abc import ABC, abstractmethod
from typing import Dict, Optional, Union, Any, List, Literal

import hashlib, os, re
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from pydantic import Field, BaseModel, validator

from kdcube_ai_app.tools.processing import record_timing, \
    DataSourceExtractionResult
from kdcube_ai_app.tools.content_type import is_text_mime_type, is_html_mime_type, \
    extract_title_from_html
from kdcube_ai_app.tools.extract import PDFExtractor
from kdcube_ai_app.tools.reflection import fully_qualified_typename

import logging
logger = logging.getLogger("DatasourceDataModel")

class IngestModifiers(BaseModel):
    # Dedupe
    dedupe: Literal["content", "resource", "none"] = "content"
    compute_hash: bool = True

    # URL handling
    canonicalize_url: bool = True
    filename_strategy: Literal["deterministic", "from_url", "provided"] = "deterministic"
    provided_filename: Optional[str] = None

    # Content source
    content_source: Literal["auto", "element", "fetch"] = "auto"

    # MIME
    primary_mime_override: Optional[str] = None

    # Readiness policies:
    # new            -> always create a new version (default)
    # reuse_ready    -> if an existing version has required stages ready, just return it (no writes)
    # overwrite_latest -> delete latest version if exists, then create new one
    # probe_only     -> only check readiness; never write; return existing if ready, else None
    version_policy: Literal["new", "reuse_ready", "overwrite_latest", "probe_only"] = "new"

    # Stages to check for readiness (when version_policy in {"reuse_ready","probe_only"})
    ready_stages: Optional[List[Literal[
        "extraction","segmentation","enrichment","embedding","search_indexing","metadata"
    ]]] = None

    # If True -> all listed stages must be ready; if False -> any is enough
    require_all_ready: bool = True

class BaseDataElement(BaseModel, ABC):

    """Base class for all data elements with common functionality."""
    mime: Optional[str] = None
    parser_name: Optional[str] = None
    path: Optional[str] = None # system (raw) file path
    title: Optional[str] = None
    content: Optional[Union[str, bytes]] = None
    provider: Optional[str] = None
    ingest: Optional[IngestModifiers] = None

    metadata: Optional[dict] = None

    def __init__(self, /, **data: Any):
        super().__init__(**data)
        # Lazy initialization to avoid creating extractors for every element
        self._html_parsers = None
        self._pdf_extractor = None

    @property
    def html_parsers(self):
        """Lazy load HTML parsers."""
        if self._html_parsers is None:
            from kdcube_ai_app.tools.parser import RawTextWebParser, SimpleHtmlParser, MediumHtmlParser
            self._html_parsers = {
                "raw": RawTextWebParser(),
                "simple": SimpleHtmlParser(),
                "medium": MediumHtmlParser(),
            }
        return self._html_parsers

    @property
    def pdf_extractor(self):
        """Lazy load PDF extractor."""
        if self._pdf_extractor is None:
            self._pdf_extractor = PDFExtractor()
        return self._pdf_extractor

    def to_data_source(self) -> 'BaseDataSource':
        """Create appropriate data source based on element type."""
        if isinstance(self, URLDataElement):
            return URLSource(self)
        elif isinstance(self, FileDataElement):
            return FileSource(self)
        elif isinstance(self, RawTextDataElement):
            return RawTextSource(self)
        else:
            raise ValueError(f"Unsupported element type: {type(self)}")

    def get_display_path(self) -> str:
        """Get the appropriate path/identifier for display and logging."""
        if isinstance(self, URLDataElement):
            return self.url
        elif isinstance(self, FileDataElement):
            return self.path
        elif isinstance(self, RawTextDataElement):
            return self.name or "raw_text"
        else:
            return str(self)

    def get_effective_parser_name(self) -> str:
        """Get the parser name, handling different field names across element types."""
        if hasattr(self, 'parser_type') and self.parser_type:
            return self.parser_type
        elif self.parser_name:
            return self.parser_name
        else:
            return "simple"  # default

    def ensure_content_loaded(self):
        """Ensure content is loaded for the element."""
        if self.content is None:
            raise ValueError(f"Content not loaded for element: {self.get_display_path()}")

    @record_timing
    def extract(self) -> List[DataSourceExtractionResult]:
        """Extract content of the data element based on MIME type."""
        try:
            self.ensure_content_loaded()

            # Route to appropriate extractor based on MIME type
            if self.mime == "application/pdf":
                return self._extract_pdf()
            elif is_html_mime_type(self.mime):
                return self._extract_html_content()
            elif is_text_mime_type(self.mime):
                return self._extract_text_content()
            else:
                # For unknown MIME types, try to handle as text if possible
                display_path = self.get_display_path()
                logger.warning(f"Unknown MIME type {self.mime} for element {display_path}, attempting text extraction")
                return self._extract_text_content()

        except Exception as e:
            display_path = self.get_display_path()
            logger.error(f"Error extracting element {display_path} (MIME: {self.mime}): {str(e)}")
            return []

    def _extract_pdf(self) -> List[DataSourceExtractionResult]:
        """Extract content from PDF URL using PDF extractor."""
        try:
            # Use PDF extractor
            display_path = self.get_display_path()
            results = self.pdf_extractor.extract(self.content, self.path)

            # Update metadata to indicate this came from a URL
            for result in results:
                result.metadata.update({
                    "type": "pdf",
                    "parser": fully_qualified_typename(self.pdf_extractor),
                    "mime": self.mime,
                    "source_path": self.path,
                    "provider": getattr(self, "provider", None)
                })

            return results

        except Exception as e:
            display_path = self.get_display_path()
            logger.error(f"Error extracting PDF from {display_path}: {str(e)}")
            return []

    def _extract_html_content(self) -> List[DataSourceExtractionResult]:
        """Extract content from HTML using HTML parsers."""
        try:
            from kdcube_ai_app.tools.parser import SimpleHtmlParser

            # Get the appropriate parser
            parser_name = self.get_effective_parser_name()
            parser = self.html_parsers.get(parser_name, SimpleHtmlParser())

            # Ensure content is string for HTML parsing
            if isinstance(self.content, bytes):
                content_str = self.content.decode('utf-8', errors='ignore')
            else:
                content_str = self.content

            # Parse to markdown
            display_path = self.get_display_path()
            markdown = parser.parse(content_str, display_path)

            # Create result
            metadata = {
                "type": "html",
                "parser": fully_qualified_typename(parser),
                "mime": self.mime,
                "source_path": self.path,
                "title": extract_title_from_html(content_str),
                "provider": getattr(self, "provider", None)
            }

            return [DataSourceExtractionResult(content=markdown, metadata=metadata)]

        except Exception as e:
            display_path = self.get_display_path()
            logger.error(f"Error extracting HTML from {display_path}: {str(e)}")
            return []

    def _extract_text_content(self) -> List[DataSourceExtractionResult]:
        """Extract content from plain text or other text-based formats."""
        try:
            # Ensure content is string
            if isinstance(self.content, bytes):
                content_str = self.content.decode('utf-8', errors='ignore')
            else:
                content_str = self.content

            # For plain text, just return as-is (or with minimal formatting)
            display_path = self.get_display_path()
            metadata = {
                "type": "text",
                "parser": None,
                "mime": self.mime,
                "source_path": self.path,
                "provider": getattr(self, "provider", None)
            }

            return [DataSourceExtractionResult(content=content_str, metadata=metadata)]

        except Exception as e:
            display_path = self.get_display_path()
            logger.error(f"Error extracting text from {display_path}: {str(e)}")
            return []


class URLDataElement(BaseDataElement):
    """Model for URL-based data element."""
    type: Literal["url"] = "url"
    url: str
    parser_type: Optional[str] = "simple"
    content: Optional[Union[str, bytes]] = None

    def get_display_path(self) -> str:
        return self.url


class FileDataElement(BaseDataElement):
    """Model for file-based data element."""
    type: Literal["file"] = "file"
    filename: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    path: str
    content: Optional[Union[str, bytes]] = None

    @validator('mime')
    def validate_mime(cls, v):
        """Validate mime type."""
        supported_mimes = [
            "application/pdf",
            "text/markdown",
            "text/plain",
            "text/csv"
        ]
        # Just a warning, not an error - we'll try to handle it anyway
        if v and v not in supported_mimes:
            logger.warning(f"Mime type {v} may not be fully supported")
        return v

    def get_display_path(self) -> str:
        return self.path


class RawTextDataElement(BaseDataElement):
    """Model for raw text data element."""
    type: Literal["raw_text"] = "raw_text"
    text: str
    name: Optional[str] = None
    content: Optional[str] = None

    def __init__(self, /, **data: Any):
        super().__init__(**data)
        # For raw text, ensure content is set to text if not provided
        if self.content is None:
            self.content = self.text

    def get_display_path(self) -> str:
        return self.name or "raw_text"


class MetadataGenerationResult(BaseModel):
    """Result of metadata generation for a segment."""
    metadata: Dict[str, Any] = Field(..., description="Generated metadata")
    # key_concepts: List[str] = Field(..., description="Key concepts identified")


class SummaryGenerationResult(BaseModel):
    """Result of summary generation."""
    summary: str = Field(..., description="Generated summary")
    is_complete: bool = Field(..., description="Whether the summary is complete")


DataElement = Union[URLDataElement, FileDataElement, RawTextDataElement]


def create_data_element(**kwargs) -> DataElement:
    """Factory function to create the correct DataElement instance."""
    element_type = kwargs.get("type")

    if element_type == "url":
        return URLDataElement(**kwargs)
    elif element_type == "file":
        return FileDataElement(**kwargs)
    elif element_type == "raw_text":
        return RawTextDataElement(**kwargs)
    else:
        raise ValueError(f"Unknown data element type: {element_type}")


class BaseDataSource(ABC):
    """Base class for data sources."""
    source_id: str = None
    element: BaseDataElement # = Field(None, description="The data element this source is based on")
    mime: Optional[str] #  = Field(None, description="MIME type of the data source")

    def __init__(self, element: BaseDataElement, /, **data: Any):
        super().__init__(**data)
        self.element = element
        self.mime = element.mime

    @abstractmethod
    def get_source_type(self) -> str:
        """Get source type identifier."""
        pass

    def get_source_id(self) -> str:
        return self.source_id

    @abstractmethod
    def to_url(self) -> str:
        """Get URL representation of this data source."""
        pass

    @record_timing
    def extract(self) -> List[DataSourceExtractionResult]:
        """Extract data using the element's extraction logic."""
        return self.element.extract()


class URLSource(BaseDataSource):
    """Data source for a single URL with MIME type-aware extraction."""
    # url: Field(str, description="The URL to fetch content from")

    def __init__(self, element: URLDataElement, /, **data: Any):
        super().__init__(element, **data)
        self.url = element.url
        self.content = element.content
        self.mime = element.mime or "text/html"  # Get MIME type from element
        # TODO: Improve URL sanitization
        self.source_id = element.url.replace("https://", "").replace("http://", "").replace("/", "__").replace("&", "_").replace("?", "_").replace("=", "_")

    def get_source_type(self) -> str:
        return "url"

    def to_url(self) -> str:
        return self.url


class FileSource(BaseDataSource):
    """Data source for file-based elements."""

    def __init__(self, element: FileDataElement, /, **data: Any):
        super().__init__(element, **data)
        self.file_path = element.path

        self.filename = element.filename
        self.metadata = element.metadata
        self.source_id = self.file_path.replace("/", "__").replace(".", "_")
        self.content = element.content


    def get_source_type(self) -> str:
        return f"file/{self.mime.split('/')[-1]}"

    def to_url(self) -> str:
        return f"file://{self.file_path}"


class RawTextSource(BaseDataSource):
    """Data source for raw text content."""

    def __init__(self, element: RawTextDataElement, /, **data: Any):
        super().__init__(element, **data)
        self.content = element.text
        self.name = element.name
        self.source_id = "RAW_" + str(element.name)
        self.content = element.content

    def get_source_type(self) -> str:
        return "raw_text"

    def to_url(self) -> str:
        return f"raw_text://{self.name}"


class URLCrawlerSource(BaseDataSource):
    """Data source that crawls multiple URLs."""

    def __init__(self, start_url: str, element: BaseDataElement, /, parser_name: str = "simple",
                 rule_name: str = "simple", rule_params: Dict = None, **data: Any):

        super().__init__(element, **data)
        from kdcube_ai_app.tools.crawler import SimpleCrawlingRule, DomainLimitedCrawlingRule
        from kdcube_ai_app.tools.parser import SimpleHtmlParser, \
            MediumHtmlParser

        self.start_url = start_url
        # TODO: Improve URL sanitization
        self.source_id = start_url.replace("https://", "").replace("http://", "").replace("/", "__").replace("&", "_").replace("?", "_").replace("=", "_")
        self.parser_name = parser_name
        self.rule_name = rule_name
        self.rule_params = rule_params or {}

        # Initialize parsers
        self.parsers = {
            "simple": SimpleHtmlParser(),
            "medium": MediumHtmlParser(),
            # Add more parsers as needed
        }

        # Initialize rules
        from urllib.parse import urlparse
        parsed_url = urlparse(start_url)
        self.rules = {
            "simple": SimpleCrawlingRule(**self.rule_params),
            "domain": DomainLimitedCrawlingRule(parsed_url.netloc, **self.rule_params),
            # Add more rules as needed
        }

    @record_timing
    def extract(self) -> List[DataSourceExtractionResult]:
        """Crawl URLs and extract content."""
        from urllib.parse import urljoin
        from kdcube_ai_app.tools.crawler import SimpleCrawlingRule
        from kdcube_ai_app.tools.parser import SimpleHtmlParser

        try:
            results = []
            visited = set()
            to_visit = [(self.start_url, 0)]  # (url, depth)

            # Get the appropriate parser and rule
            parser = self.parsers.get(self.parser_name, SimpleHtmlParser())
            rule = self.rules.get(self.rule_name, SimpleCrawlingRule())

            while to_visit:
                url, depth = to_visit.pop(0)

                if url in visited:
                    continue

                visited.add(url)

                # Fetch content
                try:
                    response = requests.get(url)
                    response.raise_for_status()

                    # Parse to markdown
                    markdown = parser.parse(response.text, url)

                    # Create result
                    metadata = {
                        "source_url": url,
                        "parser": self.parser_name,
                        "depth": depth,
                        "title": BeautifulSoup(response.text, 'html.parser').title.text if BeautifulSoup(response.text, 'html.parser').title else "Untitled"
                    }

                    results.append(DataSourceExtractionResult(content=markdown, metadata=metadata))

                    # Find links to follow
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for link in soup.find_all('a', href=True):
                        next_url = urljoin(url, link['href'])

                        if rule.should_follow(next_url, depth + 1):
                            to_visit.append((next_url, depth + 1))

                except Exception as e:
                    logger.error(f"Error processing URL {url}: {str(e)}")
                    continue

            return results

        except Exception as e:
            logger.error(f"Error crawling from start URL {self.start_url}: {str(e)}")
            return []

    def get_source_type(self) -> str:
        return "url_crawler"

    def get_source_id(self) -> str:
        return self.source_id

    def to_url(self) -> str:
        return f"crawler://{self.start_url}"

def canonicalize_url(url: str) -> str:
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    # Drop default ports
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, host, path, "", p.query, ""))

def source_name_from_url(url: str) -> str:
    cu = canonicalize_url(url)
    p = urlparse(cu)
    base = f"{p.hostname}{p.path}".lower()
    base = re.sub(r"[^a-z0-9._/\-]+", "-", base).strip("-")
    short = hashlib.sha1(cu.encode("utf-8")).hexdigest()[:8]
    return f"{base}--{short}"

def ext_for_mime(mime: str, default: str = ".bin") -> str:
    import mimetypes
    if not mime:
        return default
    guess = mimetypes.guess_extension(mime)
    return guess or (".html" if mime == "text/html" else default)

def deterministic_url_filename(url: str, mime: str, default_ext: str = ".html") -> str:
    cu = canonicalize_url(url)
    p = urlparse(cu)
    tail = (p.path.split("/")[-1] or "index").lower()
    tail = re.sub(r"[^a-z0-9._\-]+", "-", tail).strip("-") or "index"
    host = re.sub(r"[^a-z0-9._\-]+", "-", (p.hostname or "site").lower())
    short = hashlib.sha1(cu.encode("utf-8")).hexdigest()[:8]
    _, ext = os.path.splitext(tail)
    ext = ext or ext_for_mime(mime, default_ext)
    base = tail if tail != "index" else f"{host}--{tail}"
    return f"{base}--{short}{ext}"
