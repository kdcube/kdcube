from pydantic import BaseModel, Field
from typing import List, Optional

class SegmentBoundary(BaseModel):
    """Phrase-based boundary markers for reliable code-based splitting."""
    start_phrase: str = Field(
        description="Distinctive 5-15 word phrase marking segment start. "
                    "Must be unique enough for reliable text search. "
                    "Include punctuation if it aids uniqueness."
    )
    end_phrase: str = Field(
        description="Distinctive 5-15 word phrase marking segment end. "
                    "Must be unique enough for reliable text search. "
                    "Should be the last phrase before next segment starts."
    )
    overlap_with_next: bool = Field(
        default=False,
        description="If True, this segment's end_phrase overlaps with next segment's content "
                    "to preserve context continuity."
    )

class SegmentMetadata(BaseModel):
    """Enhanced metadata for each segment."""
    summary: str = Field(description="1-2 sentence summary of segment content.")
    key_concepts: List[str] = Field(
        description="5-15 contextual keywords as '<key>.<value>'; "
                    "first dot separates; values may contain dots. "
                    "Keys: domain, topic, fact, metric, method, tech, org, role, "
                    "policy, event, condition, risk, concept, etc."
    )
    entities: List[str] = Field(
        description="Named entities (people, orgs, locations, products, dates, "
                    "technical terms) mentioned in this segment. 3-10 items."
    )
    hypothetical_questions: List[str] = Field(
        description="3-5 questions this segment could answer."
    )
    table_summary: Optional[str] = Field(
        default=None,
        description="Only if segment contains a table—summarize insights."
    )

class Segment(BaseModel):
    """Complete segment definition with boundaries and metadata."""
    boundary: SegmentBoundary
    metadata: SegmentMetadata
    segment_type: str = Field(
        default="prose",
        description="Type: 'prose', 'table', 'list', 'code', 'diagram_description'"
    )

class SegmentationRuleset(BaseModel):
    """Complete ruleset produced by LLM segmenter."""
    document_summary: str = Field(
        description="1-3 sentence overview of entire document structure and purpose."
    )
    segments: List[Segment] = Field(
        description="Ordered list of segments with boundaries and metadata."
    )
    segmentation_rationale: str = Field(
        description="Brief explanation of segmentation strategy used "
                    "(e.g., 'Split by major topic shifts', 'One segment per section')."
    )


def build_segmenter_prompt(full_text: str) -> tuple[str, str]:
    """
    Build system and user prompts for LLM segmenter agent.

    Returns:
        (system_prompt, user_prompt)
    """
    system_prompt = """You are an expert document segmenter for semantic retrieval systems.

Your task:
1. Read the ENTIRE document carefully
2. Identify natural semantic boundaries (topic shifts, section breaks, narrative changes)
3. For each segment, choose DISTINCTIVE start and end phrases (5-15 words each)
4. Extract comprehensive metadata for each segment

CRITICAL: Phrase Selection Rules
• Start/end phrases must be UNIQUE enough for reliable text search
• Include punctuation if it helps uniqueness (e.g., "Figure 3. Results from" vs "Results from")
• Avoid generic phrases like "In this section" or "As mentioned above"
• Choose phrases that appear EXACTLY ONCE in the document
• Prefer phrases with proper nouns, numbers, or distinctive vocabulary
• End phrase should be the LAST distinctive phrase before the next segment begins

Overlap Strategy:
• Set overlap_with_next=True when context continuity is critical (e.g., entity spans boundary)
• Typically use 10-20% overlap for dense technical content
• No overlap needed for clean topic breaks

Metadata Extraction:
• Summary: Capture the segment's core purpose in 1-2 sentences
• Key_concepts: Use the format 'category.value' (e.g., 'method.agentic chunking', 'risk.hallucination')
• Entities: Extract ALL named entities (people, orgs, products, dates, technical terms)
• Questions: What would someone search for to find this segment?

Return STRICT JSON matching this schema:
""" + SegmentationRuleset.model_json_schema() + """

No commentary. No markdown fences. Only valid JSON."""

    user_prompt = f"""Document to segment:

---
{full_text[:15000]}  # Truncate if needed for token limits
---

Analyze this document and produce the segmentation ruleset.
Focus on creating segments optimized for semantic retrieval.
Return ONLY the JSON ruleset."""

    return system_prompt, user_prompt


# Usage example
def segment_document_with_llm(full_text: str, llm_client) -> SegmentationRuleset:
    """
    Step 1: Get segmentation ruleset from LLM.

    Args:
        full_text: Complete document text
        llm_client: Your LLM client (OpenAI, Anthropic, etc.)

    Returns:
        SegmentationRuleset with boundaries and metadata
    """
    sys_prompt, usr_prompt = build_segmenter_prompt(full_text)

    response = llm_client.generate(
        system=sys_prompt,
        user=usr_prompt,
        response_format={"type": "json_object"}  # Force JSON mode
    )

    return SegmentationRuleset.model_validate_json(response)


def apply_segmentation_ruleset(full_text: str, ruleset: SegmentationRuleset) -> List[dict]:
    """
    Step 2: Apply the ruleset to split the document in code.

    Args:
        full_text: Complete document text
        ruleset: SegmentationRuleset from LLM

    Returns:
        List of dicts with 'text' and 'metadata' keys
    """
    segments_output = []

    for i, segment in enumerate(ruleset.segments):
        # Find start position
        start_idx = full_text.find(segment.boundary.start_phrase)
        if start_idx == -1:
            # Fallback: try fuzzy matching or log warning
            print(f"Warning: Could not find start phrase for segment {i}")
            continue

        # Find end position
        end_phrase = segment.boundary.end_phrase
        end_idx = full_text.find(end_phrase, start_idx)
        if end_idx == -1:
            print(f"Warning: Could not find end phrase for segment {i}")
            continue

        # Extract segment text (include end phrase)
        end_idx += len(end_phrase)
        segment_text = full_text[start_idx:end_idx]

        segments_output.append({
            'text': segment_text,
            'metadata': segment.metadata.model_dump(),
            'type': segment.segment_type
        })

    return segments_output
