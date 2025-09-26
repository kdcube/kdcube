# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/extraction_types.py

from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel

class ImageSpec(BaseModel):
    src: str                          # original URL (or data:…)
    filename: Optional[str] = None    # optional desired filename
    content: Optional[Union[str, bytes]] = None  # raw bytes or base64 str
    content_type: Optional[str] = None
    alt: Optional[str] = None

class HtmlPostPayload(BaseModel):
    type: str = "html_post"
    html: str
    base_url: Optional[str] = None
    images: Optional[List[ImageSpec]] = None     # optional pre-supplied images
    metadata: Optional[Dict[str, Any]] = None