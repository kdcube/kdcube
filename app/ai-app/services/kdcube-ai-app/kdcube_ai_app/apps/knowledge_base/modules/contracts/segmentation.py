# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Any


class SegmentType(Enum):
    CONTINUOUS = "continuous"  # For learning/curriculum
    RETRIEVAL = "retrieval"    # For search


class ProcessingMode(Enum):
    """Processing modes."""
    FULL_INDEXING = "full_indexing"
    RETRIEVAL_ONLY = "retrieval_only"


@dataclass
class BaseSegment:
    """Base segment with proper heading level information."""
    guid: str
    heading: str
    subheading: str
    text: str
    start_line_num: int
    end_line_num: int
    start_position: int
    end_position: int
    rn: str
    extracted_data_rns: List[str]

    # CRITICAL: Store the actual heading levels from markdown parsing
    heading_level: Optional[int] = None        # 1 for h1, 2 for h2, etc.
    subheading_level: Optional[int] = None     # Level of the subheading if different

    # Store position in document structure
    segment_order: int = 0                     # Order in the original document
    metadata: Optional[Dict[str, Any]] = None  # Additional metadata if needed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "heading": self.heading,
            "subheading": self.subheading,
            "text": self.text,
            "start_line_num": self.start_line_num,
            "end_line_num": self.end_line_num,
            "start_position": self.start_position,
            "end_position": self.end_position,
            "rn": self.rn,
            "extracted_data_rns": self.extracted_data_rns,
            "heading_level": self.heading_level,
            "subheading_level": self.subheading_level,
            "segment_order": self.segment_order
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseSegment':
        return cls(**data)


@dataclass
class CompoundSegment:
    """Compound segment that references base segments."""
    guid: str
    heading: str
    subheading: str
    base_segment_guids: List[str]
    rn: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "heading": self.heading,
            "subheading": self.subheading,
            "base_segment_guids": self.base_segment_guids,
            "rn": self.rn
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CompoundSegment':
        return cls(**data)
