# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/knowledge_base/modules/segmentation.py
"""
Segmentation module
"""
import json
import uuid
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType, ProcessingMode, BaseSegment, \
    CompoundSegment
from kdcube_ai_app.apps.knowledge_base.storage import KnowledgeBaseStorage
from kdcube_ai_app.tools.parser import MarkdownParser, SimpleHtmlParser


class SmartHierarchyReconstructor:
    """Smart hierarchy reconstructor that properly handles section numbering and structure."""

    @staticmethod
    def reconstruct_with_proper_hierarchy(base_segments: List['BaseSegment'], debug: bool = False) -> str:
        """
        Reconstruct markdown with intelligent hierarchy analysis.
        """
        if not base_segments:
            return ""

        if debug:
            print("=== SMART HIERARCHY RECONSTRUCTION ===")
            SmartHierarchyReconstructor._debug_segments(base_segments)

        if len(base_segments) == 1:
            return SmartHierarchyReconstructor._reconstruct_single_segment(base_segments[0])

        # Analyze the structural relationships
        structure_analysis = SmartHierarchyReconstructor._analyze_structure(base_segments)

        if debug:
            print("Structure analysis:", structure_analysis)

        # Choose reconstruction strategy based on analysis
        if structure_analysis["has_major_section_violations"]:
            if debug:
                print("⚠️ Major section violations detected, using flat reconstruction")
            return SmartHierarchyReconstructor._reconstruct_flat(base_segments)
        elif structure_analysis["has_sibling_issues"]:
            if debug:
                print("⚠️ Sibling level issues detected, using corrected reconstruction")
            return SmartHierarchyReconstructor._reconstruct_with_correction(base_segments, debug)
        else:
            return SmartHierarchyReconstructor._reconstruct_hierarchical(base_segments, debug)

    @staticmethod
    def _analyze_structure(segments: List['BaseSegment']) -> Dict[str, Any]:
        """Analyze the structural relationships between segments."""
        analysis = {
            "has_major_section_violations": False,
            "has_sibling_issues": False,
            "level_1_headings": set(),
            "numbered_sections": [],
            "section_patterns": []
        }

        # Collect all headings and their levels
        all_headings = []
        for seg in segments:
            if seg.heading and seg.heading_level:
                all_headings.append((seg.heading, seg.heading_level, "heading"))
            if seg.subheading and seg.subheading_level:
                all_headings.append((seg.subheading, seg.subheading_level, "subheading"))

        # Check for major section violations (multiple different level-1 headings)
        level_1_headings = set()
        for heading, level, _ in all_headings:
            if level == 1:
                level_1_headings.add(heading)

        analysis["level_1_headings"] = level_1_headings
        analysis["has_major_section_violations"] = len(level_1_headings) > 1

        # Check for numbered section patterns and sibling issues
        numbered_pattern = re.compile(r'^(\d+(?:\.\d+)*)')

        for heading, level, _ in all_headings:
            match = numbered_pattern.match(heading)
            if match:
                section_num = match.group(1)
                analysis["numbered_sections"].append((section_num, heading, level))

        # Analyze numbered sections for sibling relationships
        if len(analysis["numbered_sections"]) > 1:
            analysis["has_sibling_issues"] = SmartHierarchyReconstructor._detect_sibling_issues(
                analysis["numbered_sections"]
            )

        return analysis

    @staticmethod
    def _detect_sibling_issues(numbered_sections: List[Tuple[str, str, int]]) -> bool:
        """Detect if numbered sections have incorrect sibling relationships."""
        # Group by section depth
        by_depth = {}
        for section_num, heading, level in numbered_sections:
            depth = section_num.count('.')
            if depth not in by_depth:
                by_depth[depth] = []
            by_depth[depth].append((section_num, heading, level))

        # Check if sections at the same depth have different levels
        for depth, sections in by_depth.items():
            if len(sections) > 1:
                levels = [level for _, _, level in sections]
                if len(set(levels)) > 1:
                    return True  # Same depth but different levels = sibling issue

        return False

    @staticmethod
    def _reconstruct_with_correction(segments: List['BaseSegment'], debug: bool = False) -> str:
        """Reconstruct with correction of sibling level issues."""
        parts = []

        # First pass: collect all headings and analyze numbering
        heading_info = []
        for seg in segments:
            if seg.heading and seg.heading_level:
                heading_info.append({
                    "text": seg.heading,
                    "level": seg.heading_level,
                    "type": "heading",
                    "segment": seg
                })
            if seg.subheading and seg.subheading_level:
                heading_info.append({
                    "text": seg.subheading,
                    "level": seg.subheading_level,
                    "type": "subheading",
                    "segment": seg
                })

        # Correct the levels based on numbering patterns
        corrected_levels = SmartHierarchyReconstructor._correct_heading_levels(heading_info)

        # Track what we've added to avoid duplicates
        added_headings = set()

        for seg in segments:
            segment_parts = []

            # Add corrected heading
            if seg.heading:
                corrected_level = corrected_levels.get((seg.heading, "heading"), seg.heading_level)
                if seg.heading not in added_headings and corrected_level:
                    heading_markdown = "#" * corrected_level + " " + seg.heading
                    segment_parts.append(heading_markdown)
                    added_headings.add(seg.heading)
                    if debug:
                        print(f"  ✅ Added corrected heading: {heading_markdown} (was level {seg.heading_level})")

            # Add corrected subheading
            if seg.subheading and seg.subheading != seg.heading:
                corrected_level = corrected_levels.get((seg.subheading, "subheading"), seg.subheading_level)
                if seg.subheading not in added_headings and corrected_level:
                    subheading_markdown = "#" * corrected_level + " " + seg.subheading
                    segment_parts.append(subheading_markdown)
                    added_headings.add(seg.subheading)
                    if debug:
                        print(f"  ✅ Added corrected subheading: {subheading_markdown} (was level {seg.subheading_level})")

            # Add content
            if seg.text.strip():
                segment_parts.append(seg.text.strip())

            if segment_parts:
                parts.append("\n\n".join(segment_parts))

        return "\n\n".join(parts)

    @staticmethod
    def _correct_heading_levels(heading_info: List[Dict]) -> Dict[Tuple[str, str], int]:
        """Correct heading levels based on numbering patterns."""
        corrections = {}
        numbered_pattern = re.compile(r'^(\d+(?:\.\d+)*)')

        # Group headings by their numbering depth
        numbered_headings = {}
        for info in heading_info:
            match = numbered_pattern.match(info["text"])
            if match:
                section_num = match.group(1)
                depth = section_num.count('.')
                if depth not in numbered_headings:
                    numbered_headings[depth] = []
                numbered_headings[depth].append(info)

        # Assign consistent levels based on depth
        for depth, headings in numbered_headings.items():
            # All headings at the same numbering depth should have the same level
            target_level = depth + 1  # Depth 0 -> level 1, depth 1 -> level 2, etc.

            for info in headings:
                key = (info["text"], info["type"])
                corrections[key] = target_level

        return corrections

    @staticmethod
    def _reconstruct_flat(segments: List['BaseSegment']) -> str:
        """Reconstruct segments as a flat list when hierarchy is problematic."""
        parts = []
        added_headings = set()

        for segment in segments:
            segment_parts = []

            # Add main heading if present and not already added
            if segment.heading and segment.heading_level and segment.heading not in added_headings:
                heading_markdown = "#" * segment.heading_level + " " + segment.heading
                segment_parts.append(heading_markdown)
                added_headings.add(segment.heading)

            # Add subheading if different, present, and not already added
            if (segment.subheading and
                    segment.subheading != segment.heading and
                    segment.subheading_level and
                    segment.subheading not in added_headings):
                subheading_markdown = "#" * segment.subheading_level + " " + segment.subheading
                segment_parts.append(subheading_markdown)
                added_headings.add(segment.subheading)

            # Add content
            if segment.text.strip():
                segment_parts.append(segment.text.strip())

            if segment_parts:
                parts.append("\n\n".join(segment_parts))

        return "\n\n".join(parts)

    @staticmethod
    def _reconstruct_hierarchical(segments: List['BaseSegment'], debug: bool = False) -> str:
        """Standard hierarchical reconstruction for well-structured segments."""
        parts = []
        added_headings = {}  # level -> heading_text

        for i, segment in enumerate(segments):
            if debug:
                print(f"Processing segment {i+1}: {segment.guid}")

            segment_parts = []

            # Handle main heading
            if segment.heading and segment.heading_level:
                should_add = SmartHierarchyReconstructor._should_add_heading(
                    segment.heading, segment.heading_level, added_headings, debug
                )

                if should_add:
                    heading_markdown = "#" * segment.heading_level + " " + segment.heading
                    segment_parts.append(heading_markdown)
                    added_headings[segment.heading_level] = segment.heading

                    if debug:
                        print(f"  ✅ Added heading: {heading_markdown}")

            # Handle subheading
            if (segment.subheading and
                    segment.subheading != segment.heading and
                    segment.subheading_level and
                    segment.subheading_level > (segment.heading_level or 0)):

                should_add_sub = SmartHierarchyReconstructor._should_add_heading(
                    segment.subheading, segment.subheading_level, added_headings, debug
                )

                if should_add_sub:
                    subheading_markdown = "#" * segment.subheading_level + " " + segment.subheading
                    segment_parts.append(subheading_markdown)
                    added_headings[segment.subheading_level] = segment.subheading

                    if debug:
                        print(f"  ✅ Added subheading: {subheading_markdown}")

            # Add content
            if segment.text.strip():
                segment_parts.append(segment.text.strip())

            if segment_parts:
                parts.append("\n\n".join(segment_parts))

        return "\n\n".join(parts)

    @staticmethod
    def _should_add_heading(heading_text: str, level: int, added_headings: Dict[int, str], debug: bool = False) -> bool:
        """Determine if we should add a heading."""
        if level in added_headings and added_headings[level] == heading_text:
            if debug:
                print(f"    Duplicate detected: '{heading_text}' at level {level}")
            return False
        return True

    @staticmethod
    def _reconstruct_single_segment(segment: 'BaseSegment') -> str:
        """Reconstruct a single segment with its proper heading structure."""
        parts = []

        if segment.heading and segment.heading_level:
            heading_markdown = "#" * segment.heading_level + " " + segment.heading
            parts.append(heading_markdown)

        if (segment.subheading and
                segment.subheading != segment.heading and
                segment.subheading_level and
                segment.subheading_level > (segment.heading_level or 0)):

            subheading_markdown = "#" * segment.subheading_level + " " + segment.subheading
            parts.append(subheading_markdown)

        if segment.text.strip():
            parts.append(segment.text.strip())

        return "\n\n".join(parts)

    @staticmethod
    def _debug_segments(segments: List['BaseSegment']):
        """Debug the segments to understand the structure."""
        print("SEGMENTS TO RECONSTRUCT:")
        for i, seg in enumerate(segments):
            print(f"  Segment {i+1}:")
            print(f"    GUID: {seg.guid}")
            print(f"    Heading: '{seg.heading}' (level: {seg.heading_level})")
            print(f"    Subheading: '{seg.subheading}' (level: {seg.subheading_level})")
            print(f"    Text preview: {seg.text[:100]}...")
            print()


class SimplifiedEnhancedParser(MarkdownParser):
    """Simplified parser since levels are now preserved in MarkdownParser."""

    def __init__(self, project: str, tenant: str, min_tokens: int):
        super().__init__(min_tokens=min_tokens)
        self.project = project or ""
        self.tenant = tenant

    def create_base_segments(self, md_content: str, data_source, resource_id: str, version: str) -> List['BaseSegment']:
        """Create base segments - now much simpler since levels are preserved."""

        # Parse using the improved markdown parser
        segments = self.parse_markdown(md_content, data_source, min_tokens=0)

        base_segments = []
        current_pos = 0

        for i, segment in enumerate(segments):
            text = segment.get("text", "")
            heading = segment.get("heading", "")
            subheading = segment.get("subheading", "")

            # Levels are now directly available from the parser!
            heading_level = segment.get("heading_level")
            subheading_level = segment.get("subheading_level")

            # Position calculations
            start_pos = current_pos
            end_pos = start_pos + len(text)
            lines_before = md_content[:start_pos].count('\n')
            lines_in_text = text.count('\n')

            # Create base segment
            guid = str(uuid.uuid4())
            rn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:base:{resource_id}:{version}:segment:{guid}"

            extracted_data_rns = [
                f"ef:{self.tenant}:{self.project}:knowledge_base:extraction:{resource_id}:{version}:extraction_0.md"
            ]

            # Look for referenced assets
            import re
            image_refs = re.findall(r'!\[.*?\]\((.*?)\)', text)
            for img_ref in image_refs:
                img_rn = f"ef:{self.tenant}:{self.project}:knowledge_base:extraction:{resource_id}:{version}:{img_ref}"
                extracted_data_rns.append(img_rn)

            base_segment = BaseSegment(
                guid=guid,
                heading=heading,
                subheading=subheading,
                text=text,
                start_line_num=lines_before,
                end_line_num=lines_before + lines_in_text,
                start_position=0,
                end_position=len(text.split('\n')[-1]) if '\n' in text else len(text),
                rn=rn,
                extracted_data_rns=extracted_data_rns,
                heading_level=heading_level,    # Directly from parser!
                subheading_level=subheading_level,  # Directly from parser!
                segment_order=i
            )

            base_segments.append(base_segment)
            current_pos = end_pos

        return base_segments

class ContextualRetrievalSegmenter:
    """Creates retrieval segments with proper parent context and hierarchy."""

    def __init__(self, min_tokens: int = 500, max_tokens: int = 1000, overlap: int = 100):
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap = overlap

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if not text or not text.strip():
            return 0
        import re
        tokens = re.findall(r'\b\w+\b', text.lower())
        return len(tokens)

    def _extract_section_number(self, heading: str) -> Optional[str]:
        """Extract section number from heading (e.g., '5.2' from '5.2 Causes of Hallucination')."""
        if not heading:
            return None
        match = re.match(r'^(\d+(?:\.\d+)*)', heading.strip())
        return match.group(1) if match else None

    def _get_major_section_number(self, section_number: str) -> str:
        """Get major section number (e.g., '5' from '5.2.1')."""
        return section_number.split('.')[0] if section_number else ""

    def _belongs_to_same_major_section(self, seg1: 'BaseSegment', seg2: 'BaseSegment') -> bool:
        """Check if two segments belong to the same major section."""
        # Get section numbers from both heading and subheading
        num1 = self._extract_section_number(seg1.heading) or self._extract_section_number(seg1.subheading)
        num2 = self._extract_section_number(seg2.heading) or self._extract_section_number(seg2.subheading)

        if num1 and num2:
            major1 = self._get_major_section_number(num1)
            major2 = self._get_major_section_number(num2)
            return major1 == major2

        # Fallback: check if both are level-1 headings
        if seg1.heading_level == 1 and seg2.heading_level == 1:
            return seg1.heading == seg2.heading

        return True  # Default to allowing grouping if unclear

    def _find_parent_context(self, segments: List['BaseSegment']) -> Optional[str]:
        if not segments:
            return None

        first_seg = segments[0]
        if first_seg.heading_level == 1:
            return f"# {first_seg.heading}"

        # if any segment in the group has an H1, use the first one
        for seg in segments:
            if seg.heading_level == 1 and seg.heading:
                return f"# {seg.heading}"

        # Existing numbering-based fallback
        section_num = self._extract_section_number(first_seg.heading) or self._extract_section_number(first_seg.subheading)
        if section_num:
            major_section = self._get_major_section_number(section_num)
            for seg in segments:
                for heading_text in [seg.heading, seg.subheading]:
                    if heading_text and heading_text.startswith(f"{major_section} "):
                        return f"# {heading_text}"

        return None


    def _normalize_heading_levels(self, segments: List['BaseSegment']) -> List[Tuple[str, str, int]]:
        """
        Normalize heading levels based on section numbering.
        Returns list of (heading_text, content, corrected_level).
        """
        normalized = []

        for seg in segments:
            # Process main heading
            if seg.heading:
                section_num = self._extract_section_number(seg.heading)
                if section_num:
                    # Calculate correct level based on numbering depth
                    depth = section_num.count('.')
                    correct_level = depth + 1  # 0 dots = level 1, 1 dot = level 2, etc.
                else:
                    correct_level = seg.heading_level or 1

                normalized.append((seg.heading, "", correct_level))

            # Process subheading (if different from heading)
            if seg.subheading and seg.subheading != seg.heading:
                section_num = self._extract_section_number(seg.subheading)
                if section_num:
                    depth = section_num.count('.')
                    correct_level = depth + 1
                else:
                    correct_level = seg.subheading_level or 2

                normalized.append((seg.subheading, seg.text, correct_level))
            elif not seg.subheading:
                # No subheading, content goes with the main heading
                if normalized and not normalized[-1][1]:  # If last entry has no content
                    # Update the last entry with content
                    heading, _, level = normalized[-1]
                    normalized[-1] = (heading, seg.text, level)
                else:
                    # Add content without heading
                    normalized.append(("", seg.text, 0))

        return normalized

    def _reconstruct_with_context(self, segments: List['BaseSegment']) -> str:
        """Reconstruct segments with proper parent context and normalized levels."""
        if not segments:
            return ""

        parts = []

        # Add parent context if needed
        parent_context = self._find_parent_context(segments)
        if parent_context:
            parts.append(parent_context)

        # Normalize and add segment content
        normalized = self._normalize_heading_levels(segments)
        added_headings = set()

        for heading_text, content, level in normalized:
            segment_parts = []

            # Add heading if present and not already added
            if heading_text and level > 0 and heading_text not in added_headings:
                heading_markdown = "#" * level + " " + heading_text
                segment_parts.append(heading_markdown)
                added_headings.add(heading_text)

            # Add content if present
            if content and content.strip():
                segment_parts.append(content.strip())

            if segment_parts:
                parts.append("\n\n".join(segment_parts))

        return "\n\n".join(parts)

    def create_retrieval_groups(self, base_segments: List['BaseSegment']) -> List[List['BaseSegment']]:
        if not base_segments:
            return []

        groups: List[List['BaseSegment']] = []
        n = len(base_segments)
        i = 0
        last_end = -1  # exclusive index where previous group ended

        while i < n:
            current_group: List['BaseSegment'] = []
            current_tokens = 0
            j = i

            # build the group
            while j < n:
                segment = base_segments[j]
                segment_tokens = self._count_tokens(segment.text)

                # never cross major sections
                if current_group and not self._belongs_to_same_major_section(current_group[-1], segment):
                    break

                would_exceed = current_tokens + segment_tokens > self.max_tokens
                has_minimum = current_tokens >= self.min_tokens
                if would_exceed and has_minimum:
                    break

                current_group.append(segment)
                current_tokens += segment_tokens
                j += 1

                # early natural break at section boundary once we have enough
                if (current_tokens >= self.min_tokens and
                        j < n and
                        not self._belongs_to_same_major_section(segment, base_segments[j])):
                    break

            # nothing added => stop
            if not current_group:
                break

            # If this group doesn't extend coverage past the previous group's end,
            # it's pure overlap (redundant). Skip and stop.
            if j <= last_end:
                i = j
                break

            # accept the group
            groups.append(current_group)
            last_end = j  # remember end (exclusive)

            # If we are at the end, don't start an overlap-only tail group
            if j >= n:
                i = j
                break

            # Calculate overlap for the NEXT group only if there are elements left
            if len(current_group) > 1 and self.overlap > 0:
                overlap_tokens = min(self.overlap, current_tokens // 2)
                segments_to_keep = 0
                overlap_accumulated = 0
                for k in range(len(current_group) - 1, -1, -1):
                    seg_tokens = self._count_tokens(current_group[k].text)
                    if overlap_accumulated + seg_tokens <= overlap_tokens:
                        overlap_accumulated += seg_tokens
                        segments_to_keep += 1
                    else:
                        break

                # start next window with some tail carried over, but only if there’s room
                next_i = max(j - segments_to_keep, i + 1)
                if next_i >= n:
                    i = n
                else:
                    i = next_i
            else:
                i = j

        return groups

        def create_retrieval_segments(self, base_segments: List['BaseSegment']) -> List[Dict[str, Any]]:
            """
            Create properly structured retrieval segments with parent context.
            """
            print(f"Creating retrieval segments from {len(base_segments)} base segments...")

            groups = self.create_retrieval_groups(base_segments)
            retrieval_segments = []

            for i, group in enumerate(groups):
                print(f"\nProcessing group {i+1}:")

                # Debug: show what's in this group
                for j, seg in enumerate(group):
                    section_num = self._extract_section_number(seg.heading or seg.subheading or "")
                    print(f"  Segment {j+1}: {section_num} - {seg.heading or seg.subheading}")

                # Reconstruct with proper context
                reconstructed_text = self._reconstruct_with_context(group)

                # Create retrieval segment
                segment_id = str(uuid.uuid4())
                segment = {
                    "segment_id": segment_id,
                    "text": reconstructed_text,
                    "metadata": {
                        "heading": group[0].heading,
                        "subheading": group[0].subheading,
                        "base_segment_count": len(group),
                        "token_count": self._count_tokens(reconstructed_text),
                        "base_segment_guids": [seg.guid for seg in group]
                    }
                }

                retrieval_segments.append(segment)
                print(f"  📄 Final segment: {self._count_tokens(reconstructed_text)} tokens")

            return retrieval_segments

class SegmentationModule(ProcessingModule):
    """Enhanced segmentation module with comprehensive structural fixes."""

    def __init__(self,
                 storage: KnowledgeBaseStorage,
                 project: str,
                 tenant: str,
                 pipeline,
                 processing_mode: ProcessingMode = ProcessingMode.FULL_INDEXING,
                 continuous_min_tokens: int = 40,
                 retrieval_min_tokens: int = 500,
                 retrieval_max_tokens: int = 1000,
                 retrieval_overlap: int = 100):
        super().__init__(storage, project, tenant, pipeline)
        self.processing_mode = processing_mode
        self.continuous_min_tokens = continuous_min_tokens
        self.retrieval_min_tokens = retrieval_min_tokens
        self.retrieval_max_tokens = retrieval_max_tokens
        self.retrieval_overlap = retrieval_overlap

        self.parser = SimplifiedEnhancedParser(min_tokens=0, project=project, tenant=tenant,)

    @property
    def stage_name(self) -> str:
        return "segmentation"

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using simple whitespace splitting."""
        if not text or not text.strip():
            return 0
        import re
        tokens = re.findall(r'\b\w+\b', text.lower())
        return len(tokens)

    def _extract_section_number(self, heading: str) -> Optional[str]:
        """Extract section number from heading (e.g., '5.2' from '5.2 Causes of Hallucination')."""
        import re
        match = re.match(r'^(\d+(?:\.\d+)*)', heading.strip())
        return match.group(1) if match else None

    def _ensure_markdown(self, text: str,
                         content_filename: str,
                         source_url: str | None = None,
                         page_title: str | None = None) -> str:
        """If content is HTML, convert to Markdown; otherwise return as-is."""
        if content_filename.lower().endswith(".html"):
            parser = SimpleHtmlParser()
            # The second arg is a display path/source path; we don't need a base URL here.
            return parser.parse(text, source_url or "", title=page_title)
        return text

    def unstructured_by_title_strategy(self, resource_id, version, **kwargs):
        try:
            from unstructured.partition.html import partition_html
            from unstructured.chunking.title import chunk_by_title
            # only needed if you later rehydrate from JSON on disk
            try:
                from unstructured.staging.base import elements_from_dicts
            except Exception:
                elements_from_dicts = None
        except Exception as e:
            raise RuntimeError(f"unstructured package not available: {e}")

        # 1) load HTML from extraction stage
        extraction_results = self._get_extraction_results(resource_id, version)
        if not extraction_results:
            raise ValueError(f"No extraction results found for {resource_id} v{version}")

        first = extraction_results[0]
        content_file = first.get("content_file") or "extraction_0.html"
        html = self.storage.get_stage_content("extraction", resource_id, version, content_file, as_text=True)
        if not html or not html.strip():
            raise ValueError(f"Extraction content '{content_file}' is empty for {resource_id} v{version}")

        # 2) partition then chunk by title
        elements = partition_html(text=html, infer_table_structure=True, strategy="fast")

        # persist partition artifacts (optional)
        elements_dicts = [el.to_dict() for el in elements]
        self.storage.save_stage_content(
            self.stage_name, resource_id, version, "unstructured_elements.json",
            json.dumps(elements_dicts, indent=2, ensure_ascii=False),
        )

        max_characters = kwargs.get("max_characters", 2048)
        combine_text_under_n_chars = kwargs.get("combine_text_under_n_chars", 256)
        new_after_n_chars = kwargs.get("new_after_n_chars", 1800)

        # IMPORTANT: feed actual Element instances into chunk_by_title
        chunks = chunk_by_title(
            elements,
            max_characters=max_characters,
            combine_text_under_n_chars=combine_text_under_n_chars,
            new_after_n_chars=new_after_n_chars,
        )

        # 3) persist lightweight chunk metadata
        chunks_meta = []
        for idx, ch in enumerate(chunks):
            meta = ch.metadata.to_dict() if hasattr(ch, "metadata") and hasattr(ch.metadata, "to_dict") else {}
            chunks_meta.append({
                "index": idx,
                "text_preview": (getattr(ch, "text", "") or "")[:200],
                "metadata": meta,
            })
        self.storage.save_stage_content(
            self.stage_name, resource_id, version, "unstructured_chunks.json",
            json.dumps(chunks_meta, indent=2, ensure_ascii=False),
        )

        # 4) build base + retrieval segments (unchanged except: use `content_file` rn)
        from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import BaseSegment, CompoundSegment, SegmentType

        base_segments: List[BaseSegment] = []
        extracted_rn = f"ef:{self.tenant}:{self.project}:knowledge_base:extraction:{resource_id}:{version}:{content_file}"
        page_title = (first.get("metadata") or {}).get("title") or (first.get("metadata") or {}).get("page_title") or ""

        for i, ch in enumerate(chunks):
            text = getattr(ch, "text", "") or ""
            meta = ch.metadata.to_dict() if hasattr(ch, "metadata") and hasattr(ch.metadata, "to_dict") else {}
            is_table = "text_as_html" in meta
            title = meta.get("category") or meta.get("section_title") or page_title or ""

            guid = str(uuid.uuid4())
            rn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:base:{resource_id}:{version}:segment:{guid}"

            seg = BaseSegment(
                guid=guid,
                heading=title,
                subheading="",
                text=text,
                start_line_num=0,
                end_line_num=max(0, text.count("\n")),
                start_position=0,
                end_position=len(text.split("\n")[-1]) if "\n" in text else len(text),
                rn=rn,
                extracted_data_rns=[extracted_rn],
                heading_level=1,
                subheading_level=None,
                segment_order=i,
            )
            segd = seg.to_dict()
            segd.setdefault("metadata", {})
            segd["metadata"].update({"is_table": bool(is_table), "text_as_html": meta.get("text_as_html")})
            base_segments.append(BaseSegment.from_dict(segd))

        base_payload = [s.to_dict() for s in base_segments]
        self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json",
                                        json.dumps(base_payload, indent=2, ensure_ascii=False))

        retrieval_segments: List[CompoundSegment] = []
        for s in base_segments:
            cguid = str(uuid.uuid4())
            crn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:retrieval:{resource_id}:{version}:segment:{cguid}"
            retrieval_segments.append(CompoundSegment(
                guid=cguid,
                heading=s.heading,
                subheading=s.subheading or "",
                base_segment_guids=[s.guid],
                rn=crn,
            ))

        retr_json = [c.to_dict() for c in retrieval_segments]
        self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json",
                                        json.dumps(retr_json, indent=2, ensure_ascii=False), subfolder=SegmentType.RETRIEVAL.value)
        self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json",
                                        json.dumps(retr_json, indent=2, ensure_ascii=False), subfolder=SegmentType.CONTINUOUS.value)

        results = {
            "resource_id": resource_id,
            "version": version,
            "processing_mode": self.processing_mode.value,
            "base_segments_count": len(base_segments),
            "compound_segments": {
                SegmentType.RETRIEVAL.value: len(retrieval_segments),
                SegmentType.CONTINUOUS.value: len(retrieval_segments),
            },
            "timestamp": datetime.now().isoformat(),
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}",
            "strategy": "unstructured_by_title",
        }
        self.save_results(resource_id, version, results)
        return results


    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs) -> Dict[str, Any]:
        """Create base and compound segments. Supports strategy='single_chunk' to make one big segment per article."""
        if not force_reprocess and self.is_processed(resource_id, version):
            return self.get_results(resource_id, version) or {}

        strategy = kwargs.get("strategy")  # None | 'single_chunk'

        extraction_results = self._get_extraction_results(resource_id, version)
        if not extraction_results:
            raise ValueError(f"No extraction results found for {resource_id} v{version}")
        kb = kwargs.get("kb")

        data_source = kwargs.get("data_source")
        if not data_source:
            data_element = kwargs.get("data_element")
            if data_element:
                data_source = data_element.to_data_source()

        all_base_segments = []

        resource_metadata = kb.get_resource(resource_id)

        if strategy == "single_chunk":
            # Concatenate all extraction markdown into one base segment
            from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import BaseSegment, SegmentType
            import uuid, json
            from datetime import datetime

            # Read content from the first extraction (or concatenate if many)
            md_parts = []
            first_meta = None
            first_content_file = None

            for ex in extraction_results:
                content_file = ex.get("content_file", f"extraction_{ex['index']}.md")
                raw = self.storage.get_stage_content("extraction", resource_id, version, content_file, as_text=True)
                if raw is None:
                    continue
                md = self._ensure_markdown(raw, content_file, page_title=resource_metadata.title)   # <-- ensure MD
                md_parts.append(md)
                first_content_file = first_content_file or content_file
                first_meta = first_meta or ex.get("metadata", {})

            md_content = "\n\n".join(md_parts).strip()
            title = (first_meta or {}).get("title") or (first_meta or {}).get("page_title") or ""

            guid = str(uuid.uuid4())
            rn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:base:{resource_id}:{version}:segment:{guid}"
            extracted_data_rns = [f"ef:{self.tenant}:{self.project}:knowledge_base:extraction:{resource_id}:{version}:{extraction_results[0].get('content_file', 'extraction_0.md')}"]

            base_segment = BaseSegment(
                guid=guid,
                heading=title,
                subheading="",
                text=md_content,
                start_line_num=0,
                end_line_num=md_content.count('\n'),
                start_position=0,
                end_position=len(md_content.split('\n')[-1]) if '\n' in md_content else len(md_content),
                rn=rn,
                extracted_data_rns=extracted_data_rns,
                heading_level=1,
                subheading_level=None,
                segment_order=0
            )
            all_base_segments = [base_segment]

            # Save base
            base_data = [s.to_dict() for s in all_base_segments]
            self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json", json.dumps(base_data, indent=2, ensure_ascii=False))

            # Make one compound segment for both types
            from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import CompoundSegment
            comp_guid = str(uuid.uuid4())
            comp_rn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:retrieval:{resource_id}:{version}:segment:{comp_guid}"
            compound = CompoundSegment(
                guid=comp_guid,
                heading=title,
                subheading="",
                base_segment_guids=[guid],
                rn=comp_rn
            )

            comp_json = [compound.to_dict()]
            self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json", json.dumps(comp_json, indent=2, ensure_ascii=False), subfolder=SegmentType.RETRIEVAL.value)
            self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json", json.dumps(comp_json, indent=2, ensure_ascii=False), subfolder=SegmentType.CONTINUOUS.value)

            results = {
                "resource_id": resource_id,
                "version": version,
                "processing_mode": self.processing_mode.value,
                "base_segments_count": 1,
                "compound_segments": {SegmentType.RETRIEVAL.value: 1, SegmentType.CONTINUOUS.value: 1},
                "timestamp": datetime.now().isoformat(),
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
            }
            self.save_results(resource_id, version, results)
            return results
        elif strategy == "unstructured_by_title":
            return self.unstructured_by_title_strategy(resource_id, version, **kwargs)

        # Default path: original behavior
        data_source = data_source or kwargs.get("data_source")
        if not data_source:
            data_element = kwargs.get("data_element")
            if data_element:
                data_source = data_element.to_data_source()
            else:
                raise ValueError("data_source or data_element required")

        # Create base segments with the improved parser as before
        for extraction_result in extraction_results:
            content_file = extraction_result.get("content_file", f"extraction_{extraction_result['index']}.md")
            raw = self.storage.get_stage_content("extraction", resource_id, version, content_file, as_text=True)
            if not raw:
                continue
            md_content = self._ensure_markdown(raw, content_file, page_title=resource_metadata.title)   # <-- ensure MD even if .html slipped in
            base_segments = self.parser.create_base_segments(md_content, data_source, resource_id, version)
            all_base_segments.extend(base_segments)

        # Save base segments
        import json
        base_data = [seg.to_dict() for seg in all_base_segments]
        self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json", json.dumps(base_data, indent=2, ensure_ascii=False))

        # Compound segments (original logic)
        from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType
        compound_results = {}
        if self.processing_mode == ProcessingMode.FULL_INDEXING:
            enabled_types = [SegmentType.CONTINUOUS, SegmentType.RETRIEVAL]
        else:
            enabled_types = [SegmentType.RETRIEVAL]

        for segment_type in enabled_types:
            compound_segments = self._create_compound_segments(all_base_segments, resource_id, version, segment_type)
            if segment_type == SegmentType.RETRIEVAL:
                base_lookup = {seg.guid: seg for seg in all_base_segments}
                validation_issues = self.validate_retrieval_segments(compound_segments, base_lookup)
                if validation_issues:
                    self.logger.warning(f"Validation issues found in retrieval segments: {validation_issues}")
            comp_data = [seg.to_dict() for seg in compound_segments]
            self.storage.save_stage_content(self.stage_name, resource_id, version, "segments.json", json.dumps(comp_data, indent=2, ensure_ascii=False), subfolder=segment_type.value)
            compound_results[segment_type.value] = len(compound_segments)

        from datetime import datetime
        results = {
            "resource_id": resource_id,
            "version": version,
            "processing_mode": self.processing_mode.value,
            "base_segments_count": len(all_base_segments),
            "compound_segments": compound_results,
            "timestamp": datetime.now().isoformat(),
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
        }
        self.save_results(resource_id, version, results)
        return results

    def validate_retrieval_segments(self, compound_segments: List[CompoundSegment],
                                    base_lookup: Dict[str, BaseSegment]) -> List[str]:
        """Enhanced validation for retrieval segments."""
        issues = []

        for compound in compound_segments:
            referenced_bases = [base_lookup[guid] for guid in compound.base_segment_guids
                                if guid in base_lookup]

            if len(referenced_bases) > 1:
                # Check for multiple major sections
                major_sections = set()
                for base in referenced_bases:
                    section_num = self._extract_section_number(base.heading or base.subheading or "")
                    if section_num:
                        major_sections.add(section_num.split('.')[0])

                if len(major_sections) > 1:
                    issues.append(f"Compound segment {compound.guid} crosses major sections: {major_sections}")

                # Check for multiple level-1 headings
                level_1_headings = set()
                for base in referenced_bases:
                    if base.heading_level == 1:
                        level_1_headings.add(base.heading)
                    if base.subheading_level == 1:
                        level_1_headings.add(base.subheading)

                if len(level_1_headings) > 1:
                    issues.append(f"Compound segment {compound.guid} contains multiple level-1 headings: {level_1_headings}")

        return issues

    def _create_compound_segments(self,
                                  base_segments: List[BaseSegment],
                                  resource_id: str, version: str,
                                  segment_type: SegmentType) -> List[CompoundSegment]:
        """Create compound segments from base segments."""
        compound_segments = []

        if segment_type == SegmentType.CONTINUOUS:
            groups = self._group_for_continuous(base_segments)
        else:  # RETRIEVAL - use the enhanced version with structural awareness
            # groups = self._group_for_retrieval_with_structure(base_segments)
            segmenter = ContextualRetrievalSegmenter(
                min_tokens=self.retrieval_min_tokens,
                max_tokens=self.retrieval_max_tokens,
                overlap=self.retrieval_overlap
            )
            groups = segmenter.create_retrieval_groups(base_segments)

        for group in groups:
            if not group:
                continue

            guid = str(uuid.uuid4())
            rn = f"ef:{self.tenant}:{self.project}:knowledge_base:segmentation:{segment_type.value}:{resource_id}:{version}:segment:{guid}"

            # Use real heading/subheading from first segment
            heading = group[0].heading
            subheading = group[0].subheading

            # For multiple segments, use last non-empty subheading
            if len(group) > 1:
                for seg in reversed(group):
                    if seg.subheading.strip():
                        subheading = seg.subheading
                        break

            compound_segment = CompoundSegment(
                guid=guid,
                heading=heading,
                subheading=subheading,
                base_segment_guids=[seg.guid for seg in group],
                rn=rn
            )

            compound_segments.append(compound_segment)

        return compound_segments

    def _group_for_continuous(self, base_segments: List[BaseSegment]) -> List[List[BaseSegment]]:
        """Group base segments for continuous learning (merge small ones)."""
        groups = []
        current_group = []
        current_tokens = 0

        for segment in base_segments:
            tokens = self._count_tokens(segment.text)

            if current_tokens + tokens >= self.continuous_min_tokens and current_group:
                groups.append(current_group)
                current_group = [segment]
                current_tokens = tokens
            else:
                current_group.append(segment)
                current_tokens += tokens

        if current_group:
            groups.append(current_group)

        return groups

    def _get_extraction_results(self, resource_id: str, version: str) -> Optional[List[Dict[str, Any]]]:
        """Get extraction results."""
        return self.storage.get_extraction_results(resource_id, version)

    # API methods
    def get_base_segments(self, resource_id: str, version: str) -> List[BaseSegment]:
        """Get base segments."""
        try:
            content = self.storage.get_stage_content(self.stage_name, resource_id, version, "segments.json", as_text=True)
            if content:
                data = json.loads(content)
                return [BaseSegment.from_dict(item) for item in data]
        except Exception as e:
            self.logger.error(f"Error loading base segments: {e}")
        return []

    def get_compound_segments(self, resource_id: str, version: str, segment_type: SegmentType) -> List[CompoundSegment]:
        """Get compound segments."""
        try:
            content = self.storage.get_stage_content(
                self.stage_name, resource_id, version, "segments.json",
                as_text=True, subfolder=segment_type.value
            )
            if content:
                data = json.loads(content)
                return [CompoundSegment.from_dict(item) for item in data]
        except Exception as e:
            self.logger.error(f"Error loading compound segments: {e}")
        return []

    def get_segments_by_type(self,
                             resource_id: str,
                             version: Optional[str] = None,
                             segment_type: SegmentType = SegmentType.CONTINUOUS,
                             reconstruct_markdown: bool = True) -> List[Dict[str, Any]]:
        """Get segments constructed on-the-fly with smart hierarchy reconstruction."""
        if version is None:
            version = self.storage.get_latest_version(resource_id)
            if not version:
                return []

        # Get base and compound segments
        base_segments = self.get_base_segments(resource_id, version)
        compound_segments = self.get_compound_segments(resource_id, version, segment_type)

        if not base_segments or not compound_segments:
            return []

        # Create lookup
        base_lookup = {seg.guid: seg for seg in base_segments}

        # Construct segments
        constructed = []
        for compound in compound_segments:
            # Get referenced base segments
            referenced_bases = []
            for guid in compound.base_segment_guids:
                if guid in base_lookup:
                    referenced_bases.append(base_lookup[guid])

            if not referenced_bases:
                continue

            # Choose text combination method
            if reconstruct_markdown:
                combined_text = SmartHierarchyReconstructor.reconstruct_with_proper_hierarchy(referenced_bases)
            else:
                # Fallback to simple joining
                combined_text = "\n\n".join(base.text for base in referenced_bases)

            # Create constructed segment
            segment = {
                "segment_id": compound.guid,
                "text": combined_text,
                "metadata": {
                    "heading": compound.heading,
                    "subheading": compound.subheading,
                    "resource_id": resource_id,
                    "version": version,
                    "base_segment_guids": compound.base_segment_guids,
                    "reconstructed_markdown": reconstruct_markdown
                },
                "rn": compound.rn
            }

            constructed.append(segment)

        return constructed

    # Convenience methods
    def get_continuous_segments(self, resource_id: str, version: Optional[str] = None,
                                reconstruct_markdown: bool = True) -> List[Dict[str, Any]]:
        """Get continuous segments with markdown reconstruction."""
        return self.get_segments_by_type(resource_id, version, SegmentType.CONTINUOUS, reconstruct_markdown)

    def get_retrieval_segments(self, resource_id: str, version: Optional[str] = None,
                               reconstruct_markdown: bool = True) -> List[Dict[str, Any]]:
        """Get retrieval segments with markdown reconstruction."""
        return self.get_segments_by_type(resource_id, version, SegmentType.RETRIEVAL, reconstruct_markdown)
