from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterable, Dict, Optional


@dataclass(frozen=True)
class BundleUploadedFile:
    filename: str
    content_type: str
    content: bytes
    field_name: str = "file"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BundleBinaryResponse:
    content: bytes
    filename: Optional[str] = None
    media_type: str = "application/octet-stream"
    headers: Dict[str, str] = field(default_factory=dict)
    status_code: int = 200


@dataclass(frozen=True)
class BundleFileResponse:
    path: str
    filename: Optional[str] = None
    media_type: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    status_code: int = 200


@dataclass(frozen=True)
class BundleStreamResponse:
    chunks: AsyncIterable[bytes]
    filename: Optional[str] = None
    media_type: Optional[str] = "application/octet-stream"
    headers: Dict[str, str] = field(default_factory=dict)
    status_code: int = 200
