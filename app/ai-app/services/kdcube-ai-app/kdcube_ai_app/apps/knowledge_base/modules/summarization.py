# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# modules/summarization.py
"""
Summarization module for generating summaries of segments and documents.
"""
import json
import re
from typing import Dict, Any, List, Optional
from datetime import datetime

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule


class SummarizationModule(ProcessingModule):
    """Module responsible for generating summaries for segments and documents."""

    @property
    def stage_name(self) -> str:
        return "summarization"

    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs) -> Dict[str, Any]:
        """Generate summaries for segments and the overall document."""

        # Check if summarization already exists
        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Summarization already exists for {resource_id} v{version}, skipping")
            existing_content = self.storage.get_stage_content(self.stage_name, resource_id, version, "summaries.json")
            return json.loads(existing_content) if existing_content else {}

        self.logger.info(f"Generating summaries for {resource_id} v{version}")

        # Get segments and metadata
        segments = self.storage.get_segments(resource_id, version)
        if not segments:
            raise ValueError(f"No segments found for {resource_id} v{version}")

        # Get metadata for additional context
        metadata_results = kwargs.get("metadata_results")
        if not metadata_results:
            # Try to get existing metadata
            try:
                metadata_content = self.storage.get_stage_content("metadata", resource_id, version, "metadata.json")
                if metadata_content:
                    metadata_results = json.loads(metadata_content)
            except Exception:
                metadata_results = {}

        # Generate comprehensive summaries
        summary_results = {
            "document_summary": self._generate_document_summary(segments, metadata_results),
            "section_summaries": self._generate_section_summaries(segments),
            "segment_summaries": self._generate_segment_summaries(segments),
            "generation_timestamp": datetime.now().isoformat(),
            "summarization_metadata": {
                "total_segments": len(segments),
                "total_original_length": sum(len(s.get("text", "")) for s in segments),
                "compression_ratio": 0.0  # Will be calculated after summarization
            },
            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}"
        }

        # Calculate compression ratio
        total_summary_length = sum(len(s.get("summary", "")) for s in summary_results["segment_summaries"])
        if summary_results["summarization_metadata"]["total_original_length"] > 0:
            summary_results["summarization_metadata"]["compression_ratio"] = (
                    total_summary_length / summary_results["summarization_metadata"]["total_original_length"]
            )

        # Save summarization results
        self.save_results(resource_id, version, summary_results, "summaries.json")

        # Save individual summaries for easy access
        for i, segment_summary in enumerate(summary_results["segment_summaries"]):
            summary_filename = f"segment_summary_{i}.json"
            summary_content = json.dumps(segment_summary, indent=2, ensure_ascii=False)
            self.storage.save_stage_content(self.stage_name, resource_id, version, summary_filename, summary_content)

        # Log operation
        self.log_operation("summarization_complete", resource_id, {
            "version": version,
            "segment_count": len(segments),
            "compression_ratio": summary_results["summarization_metadata"]["compression_ratio"]
        })

        self.logger.info(f"Generated summaries for {len(segments)} segments of {resource_id} v{version}")
        return summary_results

    def _generate_document_summary(self, segments: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Generate overall document summary."""

        # Extract key information from all segments
        all_text = " ".join(segment.get("text", "") for segment in segments)

        # Get key sentences from each segment
        key_sentences = []
        for segment in segments:
            text = segment.get("text", "")
            if text:
                sentences = self._extract_key_sentences(text, max_sentences=2)
                key_sentences.extend(sentences)

        # Create document summary
        summary_text = " ".join(key_sentences[:10])  # Top 10 key sentences

        return {
            "summary": summary_text,
            "word_count": len(summary_text.split()),
            "covers_sections": len(set(s.get("heading", "") for s in segments if s.get("heading"))),
            "main_topics": self._extract_main_topics(segments),
            "document_type": self._classify_document_type(segments, metadata),
            "confidence_score": self._calculate_summary_confidence(segments, summary_text)
        }

    def _generate_section_summaries(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate summaries for each major section."""

        # Group segments by heading
        sections = {}
        for segment in segments:
            heading = segment.get("heading", "")
            if heading:
                if heading not in sections:
                    sections[heading] = []
                sections[heading].append(segment)

        section_summaries = []
        for heading, section_segments in sections.items():
            section_text = " ".join(s.get("text", "") for s in section_segments)

            summary = {
                "heading": heading,
                "segment_count": len(section_segments),
                "summary": self._summarize_text(section_text, target_length=200),
                "subsections": list(set(s.get("subheading", "") for s in section_segments if s.get("subheading"))),
                "word_count": len(section_text.split()),
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:section:{heading}"
            }

            section_summaries.append(summary)

        return section_summaries

    def _generate_segment_summaries(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate summaries for individual segments."""

        segment_summaries = []

        for segment in segments:
            text = segment.get("text", "")

            if not text:
                continue

            summary = self._summarize_text(text, target_length=100)

            segment_summary = {
                "segment_id": segment.get("segment_id"),
                "heading": segment.get("heading", ""),
                "subheading": segment.get("subheading", ""),
                "summary": summary,
                "original_length": len(text),
                "summary_length": len(summary),
                "compression_ratio": len(summary) / len(text) if text else 0,
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:segment:{segment.get('segment_id')}"
            }

            segment_summaries.append(segment_summary)

        return segment_summaries

    # Helper methods for text processing

    def _summarize_text(self, text: str, target_length: int = 100) -> str:
        """Basic extractive summarization."""
        sentences = self._split_sentences(text)
        if not sentences:
            return ""

        if len(text) <= target_length:
            return text

        # Score sentences by position and content
        scored_sentences = []
        for i, sentence in enumerate(sentences):
            score = self._score_sentence(sentence, i, len(sentences))
            scored_sentences.append((score, sentence))

        # Sort by score and select top sentences
        scored_sentences.sort(reverse=True)

        selected_sentences = []
        current_length = 0
        for score, sentence in scored_sentences:
            if current_length + len(sentence) <= target_length:
                selected_sentences.append(sentence)
                current_length += len(sentence)
            else:
                break

        # Maintain original order
        result_sentences = []
        for sentence in sentences:
            if sentence in selected_sentences:
                result_sentences.append(sentence)

        return " ".join(result_sentences)

    def _score_sentence(self, sentence: str, position: int, total_sentences: int) -> float:
        """Score a sentence for importance in summarization."""
        score = 0.0

        # Position scoring (first and last sentences are important)
        if position == 0:
            score += 2.0
        elif position == total_sentences - 1:
            score += 1.5
        elif position < total_sentences * 0.3:
            score += 1.0

        # Length scoring (medium length sentences preferred)
        word_count = len(sentence.split())
        if 10 <= word_count <= 25:
            score += 1.0
        elif word_count < 5:
            score -= 1.0

        # Content scoring
        if any(word in sentence.lower() for word in ['important', 'key', 'main', 'primary', 'significant']):
            score += 1.0

        if any(word in sentence.lower() for word in ['conclusion', 'result', 'finding', 'therefore', 'thus']):
            score += 1.5

        return score

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    def _extract_key_sentences(self, text: str, max_sentences: int = 3) -> List[str]:
        """Extract the most important sentences from text."""
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        scored_sentences = [(self._score_sentence(s, i, len(sentences)), s)
                            for i, s in enumerate(sentences)]
        scored_sentences.sort(reverse=True)

        return [s for _, s in scored_sentences[:max_sentences]]

    def _extract_main_topics(self, segments: List[Dict[str, Any]]) -> List[str]:
        """Extract main topics from headings and content."""
        topics = []

        # Extract from headings
        headings = [s.get("heading", "") for s in segments if s.get("heading")]
        topics.extend(headings)

        # Extract from subheadings
        subheadings = [s.get("subheading", "") for s in segments if s.get("subheading")]
        topics.extend(subheadings)

        return list(set(topics))[:10]  # Return unique topics, max 10

    def _classify_document_type(self, segments: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
        """Classify the type of document based on content analysis."""

        # Simple heuristics for document classification
        has_methodology = any('method' in s.get("text", "").lower() for s in segments)
        has_results = any('result' in s.get("text", "").lower() for s in segments)
        has_conclusion = any('conclusion' in s.get("text", "").lower() for s in segments)

        if has_methodology and has_results and has_conclusion:
            return "research_paper"
        elif any('tutorial' in s.get("text", "").lower() for s in segments):
            return "tutorial"
        elif any('manual' in s.get("text", "").lower() for s in segments):
            return "manual"
        elif any('report' in s.get("text", "").lower() for s in segments):
            return "report"
        else:
            return "general_document"

    def _calculate_summary_confidence(self, segments: List[Dict[str, Any]], summary: str) -> float:
        """Calculate confidence score for the summary quality."""
        if not summary:
            return 0.0

        # Simple heuristics for summary quality
        summary_words = set(summary.lower().split())
        total_words = set(" ".join(s.get("text", "") for s in segments).lower().split())

        if not total_words:
            return 0.0

        overlap = len(summary_words.intersection(total_words)) / len(summary_words)
        return min(1.0, overlap)

    # Additional helper methods would be implemented here...
    # (extract_list_items, extract_definitions, etc.)

    def get_document_summary(self, resource_id: str, version: str) -> Optional[str]:
        """Get the overall document summary."""
        results = self.get_results(resource_id, version)
        if results:
            doc_summary = results.get("document_summary", {})
            return doc_summary.get("summary")
        return None

    def get_segment_summary(self, resource_id: str, version: str, segment_id: str) -> Optional[str]:
        """Get summary for a specific segment."""
        results = self.get_results(resource_id, version)
        if not results:
            return None

        segment_summaries = results.get("segment_summaries", [])
        for summary in segment_summaries:
            if summary.get("segment_id") == segment_id:
                return summary.get("summary")

        return None

    def get_key_points(self, resource_id: str, version: str, point_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get key points, optionally filtered by type."""
        results = self.get_results(resource_id, version)
        if not results:
            return []

        key_points = results.get("key_points", [])

        if point_type:
            key_points = [kp for kp in key_points if kp.get("type") == point_type]

        return key_points