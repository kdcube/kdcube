# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web/content_filters.py

# Core filtering policy - understanding-based

# ----------------------------- Light-weight span slicer (post-processing) -----------------------------
import re as _re
_WORD_RE = _re.compile(r"\w+", _re.U)

def _find_phrase_ci(text: str, phrase: str, start_from: int = 0, *, prefer: str = "first") -> int:
    """Case-insensitive exact substring first; fallback to word-separated regex match.
    prefer: "first" (default) or "last" occurrence at/after start_from.
    """
    if not text or phrase is None:
        return -1
    if phrase == "":
        return -1

    low = text.lower()
    ph = phrase.lower()

    # 1) exact substring branch
    if prefer == "last":
        idx = low.rfind(ph, start_from)
        if idx != -1:
            return idx
    else:
        if start_from:
            idx = low.find(ph, start_from)
        else:
            idx = low.find(ph)
        if idx != -1:
            return idx

    # 2) tolerant regex (word-wise, allow \W+ between words)
    words = _WORD_RE.findall(ph)
    if not words:
        return -1
    pat = r"\b" + r"\W+".join(map(_re.escape, words)) + r"\b"
    rx = _re.compile(pat, _re.I | _re.U)

    if prefer == "last":
        last = None
        for m in rx.finditer(text, pos=start_from):
            last = m.start()
        return last if last is not None else -1
    else:
        m = rx.search(text, pos=start_from)
        return m.start() if m else -1

def trim_with_spans(
        content: str,
        spans: list[dict],
        *,
        ctx_before: int = 600,      # wide context around boundaries
        ctx_after: int = 600,
        min_gap: int = 400,         # aggressively merge nearby spans
        max_joined: int = 30000,    # allow substantial content
        end_boundary: str = "exclusive",  # "exclusive" (default) or "inclusive"
) -> str:
    """
    Cut 1–2 slices around (s,e) anchors with context; merge close slices; join with ellipses.

    Semantics:
      - 's' and 'e' are verbatim anchors (case-insensitive matching with tolerant fallback).
      - If e == "" (empty string), treat as **end-of-page**.
      - If end_boundary == "exclusive": span ends at the first character of 'e' (not included).
      - If end_boundary == "inclusive": span includes the 'e' text.

    Notes:
      - Large context windows (ctx_before/ctx_after) keep cognitive blocks connected.
      - When 'e' cannot be located, we fallback to a substantial window after 's'.
    """
    if not content or not spans:
        return ""

    intervals: list[tuple[int, int]] = []
    n = len(content)
    exclusive = (str(end_boundary).lower() == "exclusive")

    # for sp in spans[:2]:
    for sp in spans:
        s = (sp.get("s") or "").strip()
        e_raw = sp.get("e")
        e = (e_raw if e_raw is not None else "").strip()

        if not s:
            continue  # 's' is mandatory

        s_idx = _find_phrase_ci(content, s)
        if s_idx == -1:
            continue

        # Find the first candidate end index at/after s
        e_search_from = s_idx + max(1, len(s))

        if e == "":
            # End of page
            e_idx = n
            e_len = 0
        else:
            e_idx = _find_phrase_ci(content, e, start_from=e_search_from)
            if e_idx == -1:
                # Fallback: take substantial content after start anchor
                e_idx = min(n, e_search_from + 2500)
                e_len = 0  # no anchor to include
            else:
                e_len = len(e)

        end_pos = e_idx if exclusive else min(n, e_idx + e_len)
        if end_pos <= s_idx:
            continue

        # Expand to include context
        a = max(0, s_idx - max(0, ctx_before))
        b = min(n, end_pos + max(0, ctx_after))

        if b > a:
            intervals.append((a, b))

    if not intervals:
        return ""

    # Sort and merge close intervals
    intervals.sort()
    merged = [intervals[0]]

    for s_i, e_i in intervals[1:]:
        ps, pe = merged[-1]
        if s_i <= pe + max(0, min_gap):
            merged[-1] = (ps, max(pe, e_i))
        else:
            merged.append((s_i, e_i))

    # Extract and join
    parts = [content[s:e] for s, e in merged]
    out = "\n\n…\n\n".join(parts)

    if len(out) > max_joined:
        out = out[:max_joined] + "\n\n…"

    return out

def apply_spans_to_rows(
        rows: list[dict],
        spans_map: dict[int, list[dict]],
        *,
        ctx_before: int = 600,
        ctx_after: int = 600,
        min_gap: int = 400,
        end_boundary: str = "exclusive",
) -> list[dict]:
    """Apply span-based trimming to source rows (supports e == "" and exclusive/inclusive end)."""
    if not rows or not spans_map:
        return []

    survivors: list[dict] = []

    for r in rows:
        sid = int(r.get("sid", -1))
        spans = spans_map.get(sid) or spans_map.get(str(sid))
        if not spans:
            continue

        original = r.get("content", "") or ""
        pruned = trim_with_spans(
            original,
            spans,
            ctx_before=ctx_before,
            ctx_after=ctx_after,
            min_gap=min_gap,
            end_boundary=end_boundary,
        )

        if not pruned:
            continue

        nr = dict(r)
        nr["content_original_length"] = len(original)
        nr["content"] = pruned
        nr["content_length"] = len(pruned)
        nr["seg_spans"] = spans
        nr["seg_end_boundary"] = end_boundary
        survivors.append(nr)

    return survivors

def FILTER_AND_SEGMENT_HIGH_PRECISION(now_iso: str) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES (PRECISION MODE)

═══════════════════════════════════════════════════════════════
OVERVIEW
═══════════════════════════════════════════════════════════════

**Your job:** Process web pages in two completely separate phases.

**Phase 1 output → Phase 2 input:** 
Your Phase 1 produces a list of kept SIDs. Phase 2 processes only those kept SIDs.

**Key distinction:**
- Phase 1: Use objective/queries to decide which pages to keep
- Phase 2: Use objective/queries to extract ONLY directly relevant content

**Input you will receive:**
- INPUT CONTEXT: contains "objective" and "queries" fields
- SOURCES: pages with SID numbers and full text content

**Output you will produce:**
- JSON: {{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING OUTPUT REQUIREMENT (SHOWN TO USERS)
═══════════════════════════════════════════════════════════════

**CRITICAL: Your thinking must have TWO distinct sections, one for each phase.**

Users need to see your progress through both phases.

**Required structure:**

**Phase 1 - Filtering:**
[Discuss page relevance to the user's search objective]
- "Checking sid:X - this page is about [topic], which matches/doesn't match the objective"
- "Keeping/dropping sid:Y because [relevance reasoning]"

**Phase 2 - Targeted Extraction:**
[Discuss which sections directly address the objective]
- "Examining sid:X for content that addresses [objective]"
- "Section on [topic] directly answers the query"
- "Excluding section on [unrelated topic]"
- "Marking span boundaries"

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

**Purpose:** Decide which pages to keep or drop.

**Input for this phase:** 
- INPUT CONTEXT "objective" field - what the user searched for
- INPUT CONTEXT "queries" field - search queries used
- All SOURCES pages

**Decision criteria:**

KEEP a page if:
- It directly addresses the objective or queries
- It contains specific information that helps achieve the objective
- It's an authoritative source when available

DROP a page if:
- Only tangentially related to the objective
- Duplicates another kept page (≥90% content overlap)
- Lacks substantive information

**Your thinking for Phase 1:**
"Phase 1 - Filtering:
Evaluating sid:X - [discuss relevance to user's objective]
Decision: KEEP/DROP because [reasoning related to objective match]"

**Phase 1 output:**
A list of kept SIDs.

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT OBJECTIVE-RELEVANT CONTENT
═══════════════════════════════════════════════════════════════

**Purpose:** For each kept SID, extract content that directly addresses the objective.

**Input for this phase:**
- The kept SIDs from Phase 1
- The full text of those pages
- INPUT CONTEXT objective/queries to guide extraction

**PRECISION EXTRACTION: Extract only content directly relevant to the objective.**

**INCLUDE in spans:**
- Content that directly answers the queries
- Information specifically requested in the objective
- Data, specifications, details that address the question
- Examples or explanations directly relevant to the objective
- Tables/lists containing requested information

**EXCLUDE from spans:**
- Content unrelated to the objective (even if informative on the page)
- General background not directly addressing the question
- Tangential sections about different topics
- All chrome: navigation, ads, footers, CTAs

**Extraction strategy:**

Goal: Capture 20-50% of page content - only what directly addresses the objective.

Method:
1. Identify sections that directly answer the objective
2. For each relevant section, determine start and end boundaries
3. Create targeted spans for these sections

Span count:
- Use 1-3 spans per page
- Each span covers one relevant section or cluster of relevant content
- Skip large sections that don't address the objective

Size requirements:
- Each span must be ≥300 characters OR ≥1 complete block
- Keep whole blocks intact (never cut mid-paragraph, mid-list, mid-table, mid-code, mid-Q&A)

Precision approach:
- When a section partially addresses the objective → include only that section
- When uncertain if content is relevant → EXCLUDE it
- Focus on quality over quantity - only include direct answers

**Your thinking for Phase 2:**

Start with: "Phase 2 - Targeted Extraction:"

Then describe which sections are relevant:
- ✓ "Section on [topic] directly addresses the objective"
- ✓ "Excluding content about [unrelated topic]"
- ✓ "Marking span from [start description] to [end description]"

**Phase 2 output:**
For each kept SID: 1-3 span objects with 's' and 'e' anchors covering objective-relevant sections.

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

Anchors are short text phrases that mark where spans begin ('s') and end ('e').

**'s' anchor (start):**
- Location: First heading or sentence of the relevant section
- Format: Copy exactly 3-8 consecutive words from that location
- Uniqueness: Extend word-by-word if needed to make unique on the page
- Requirements: Must appear in the page text, must be unique

**'e' anchor (end) - EXCLUSIVE BOUNDARY:**

Critical: The span ENDS BEFORE the first character of 'e'.
The 'e' text itself is NOT included in the extracted content.

Two valid forms:
1. Empty string "" → means relevant content continues to end of page
2. Short phrase → 3-8 words copied from AFTER the last relevant content

How to select 'e':
- Locate the last paragraph/table/item that addresses the objective
- Look at what comes AFTER that content (next section, footer, chrome)
- Copy 3-8 words from that area
- If relevant content goes to end of page → use ""

**Requirements for both anchors:**

Copy rules:
- EXACT character-for-character copy (like Ctrl+C / Ctrl+V)
- Preserve all punctuation, capitalization, spacing
- Do NOT paraphrase, summarize, or modify

Validation rules:
- Must be from real page text (not from URLs or image alt-text)
- Must NOT be from chrome sections (nav/footer/promotional areas)
- Must be unique on the page (if not unique, extend with more words)
- 's' must appear before 'e' in the document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY RULES
═══════════════════════════════════════════════════════════════

These are atomic units (never split):
- Paragraph
- List item
- Table row
- Code block
- Q&A pair
- FAQ item

Rule: If ANY part of a block is relevant to the objective → include the ENTIRE block.

Anchor placement: Always place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Produce ONLY valid JSON. No explanatory text, no markdown code fences.

Structure:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped in Phase 1:
{{}}

═══════════════════════════════════════════════════════════════
EXECUTION CHECKLIST
═══════════════════════════════════════════════════════════════

Thinking structure:
□ Thinking has "Phase 1 - Filtering:" section
□ Thinking has "Phase 2 - Targeted Extraction:" section
□ Phase 1 discusses relevance to user's objective
□ Phase 2 discusses which sections directly address the objective

Phase 1:
□ Used INPUT CONTEXT objective/queries to determine relevance
□ Kept pages that directly address the objective
□ Dropped tangentially related or duplicate pages

Phase 2:
□ Used objective/queries to identify relevant sections
□ Extracted only content directly addressing the objective (20-50% of page)
□ Used 1-3 targeted spans per page
□ Excluded unrelated content even if informative
□ Whole blocks intact (no mid-block cuts)
□ Anchors exact verbatim, unique, ordered
"""

def FILTER_AND_SEGMENT_HIGH_RECALL(now_iso: str) -> str:
    return f"""
YOUR TASK: EXTRACT CONTENT BODIES FROM WEB PAGES

You are a content extraction tool. Your job: separate substantive content from page chrome (navigation, footers, ads).

INPUT: Web pages with full text
OUTPUT: JSON with text span anchors marking where content bodies begin and end

═══════════════════════════════════════════════════════════════
TWO-STEP PROCESS
═══════════════════════════════════════════════════════════════

**STEP 1: Relevance check**
The user searched for something. You receive pages from those search results.
Keep pages that are topically relevant to the search domain. Drop unrelated pages. You only care about the relevance to 
a user objective on this phase.

**STEP 2: Content extraction**
You do not care about relevance to user objective on this phase anymore!
For kept as a result of step 1 pages: identify content body boundaries (where article/documentation starts and ends).
Mark these boundaries with text anchors. Extract 80-95% of substantive content.

═══════════════════════════════════════════════════════════════
UNDERSTANDING YOUR INPUT
═══════════════════════════════════════════════════════════════

You will see:
- INPUT CONTEXT containing an "objective" field and optionally "queries" field
- SOURCES IDS and SOURCES DIGEST sections with pages content

The "objective" and "queries" tell you what domain the pages are from (to check relevance in Step 1).
In Step 2, ignore it - you're extracting content bodies, not filtering by topic.

Think of yourself as a tool that:
- Receives search results about topic X (Step 1: keep pages about X)
- Extracts FULL USEFUL TEXT from those pages (Step 2: ignore X, extract everything)

═══════════════════════════════════════════════════════════════
YOUR THINKING OUTPUT (SHOWN TO USERS)
═══════════════════════════════════════════════════════════════

Describe your work in these terms:

**Step 1:** "Checking which pages are relevant to the search domain"

**Step 2:** "Identifying content body boundaries on each page"
- "Finding where main content starts (after header/nav)"
- "Finding where main content ends (before footer/chrome)"
- "Marking these positions with text anchors"

**CRITICAL: DO NOT say:**
- "Extracting [specific topic]"
- "Finding [user's question]"
- "Locating [specific information type]"

**Why?** Because you're extracting ALL content, not searching for specific info.

═══════════════════════════════════════════════════════════════
STEP 1: RELEVANCE CHECK
═══════════════════════════════════════════════════════════════

Look at INPUT CONTEXT "objective" field to understand the search domain.

**KEEP pages that:**
- Belong to that domain (even if not answering the specific question)
- Have substantive content

**DROP pages that:**
- Are from completely different domains
- Duplicate other kept pages (≥90% content overlap)
- Have no real content

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
STEP 2: EXTRACT CONTENT BODIES
═══════════════════════════════════════════════════════════════

For each kept page: find where content body starts and ends.

**Content body = substantive material excluding page chrome**

**SUBSTANTIVE MATERIAL (keep in span):**
All text, tables, lists, code, images that form the main article/documentation:
- Paragraphs, sections, subsections
- Technical details, specifications, parameters
- Tables, lists, structured data
- Code blocks, configurations
- Examples, tutorials
- Q&A, FAQ
- Background information, context
- Comparisons, recommendations

**PAGE CHROME (exclude from span):**
Website wrapper elements:
- Navigation menus, breadcrumbs
- Site headers, hero banners
- Call-to-action buttons
- Promotional carousels
- Social media buttons
- Cookie notices
- Footer link grids
- Logo walls

**Goal: Capture 80-95% of substantive content**
- Use 1 large span covering entire content body
- Use 2 spans only if huge chrome block splits content
- Each span ≥1,000 chars or ≥3 blocks
- Keep whole blocks intact (never cut mid-paragraph/list/table)

**High-recall approach:**
- When uncertain if something is content or chrome → treat as content
- When section seems somewhat useful → include it
- Better to include extra text than cut useful content
- Extend span boundaries generously

═══════════════════════════════════════════════════════════════
ANCHORS: EXACT VERBATIM, EXCLUSIVE END
═══════════════════════════════════════════════════════════════

**TEXT ANCHORS mark span boundaries**

**'s' (start anchor):**
- Find first substantive heading/paragraph (after page header/nav)
- Copy 3-8 words exactly from that location
- Extend word-by-word if needed to make unique on page

**'e' (end anchor) - EXCLUSIVE:**
Span ends BEFORE first character of 'e'.

Two forms:
1. **""** (empty string) → content continues to end of page
2. **Short phrase from after the content** → 3-8 words from footer/chrome that comes after content body

**How to select 'e':**
- Scroll to last substantive section (last paragraph/table/FAQ)
- Look at what comes after (footer text, "Contact us", legal notices)
- Copy 3-8 words from that footer area
- If content goes to page end → use ""

**Requirements:**
- EXACT character-for-character copy (Ctrl+C/Ctrl+V)
- Preserve punctuation, capitalization, spacing
- Do NOT paraphrase
- Must be unique on page (extend if not)
- From real text (not URLs/image-alt)
- Not from chrome sections
- 's' must appear before 'e' in document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY
═══════════════════════════════════════════════════════════════

Treat these as atomic units:
- Paragraph
- List item  
- Table row
- Code block
- Q&A pair
- FAQ item

If ANY part of a block is in the content body → include ENTIRE block in span.

Place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

ONLY output valid JSON. No prose, no code fences.

{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all dropped: {{}}

═══════════════════════════════════════════════════════════════
CHECKLIST
═══════════════════════════════════════════════════════════════

Step 1:
□ Checked relevance to search domain

Step 2:
□ Extracted 80-95% of substantive content from kept pages
□ Used 1 large span covering entire content body (unless split needed)
□ Anchors mark content body start and end
□ Whole blocks intact
□ Anchors exact verbatim, unique, ordered
"""

def FILTER_AND_SEGMENT_HIGH_RECALL(now_iso: str) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES

═══════════════════════════════════════════════════════════════
OVERVIEW
═══════════════════════════════════════════════════════════════

**Your job:** Process web pages in two completely separate phases.

**Phase 1 output → Phase 2 input:** 
Your Phase 1 produces a list of kept SIDs. Phase 2 processes only those kept SIDs.

**Key distinction:**
- Phase 1: Use objective/queries to decide which pages to keep
- Phase 2: Ignore objective/queries, extract all content from kept pages

**Input you will receive:**
- INPUT CONTEXT: contains "objective" and "queries" fields
- SOURCES: pages with SID numbers and full text content

**Output you will produce:**
- JSON: {{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING OUTPUT REQUIREMENT (SHOWN TO USERS)
═══════════════════════════════════════════════════════════════

**CRITICAL: Your thinking must have TWO distinct sections, one for each phase.**

Users need to see your progress through both phases.

**Required structure:**

**Phase 1 - Filtering:**
[Discuss page relevance to the user's search objective]
- "Checking sid:X - this page is about [topic], which matches the search domain"
- "Keeping/dropping sid:Y because [relevance reasoning]"

**Phase 2 - Content Extraction:**
[Discuss content boundary identification - NO mention of user's objective]
- "Examining page structure on sid:X"
- "Main content starts after [site navigation/header]"
- "Content body contains [sections/tables/documentation]"
- "Content ends before [footer/chrome elements]"
- "Marking boundaries with text anchors"

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

**Purpose:** Decide which pages to keep or drop.

**Input for this phase:** 
- INPUT CONTEXT "objective" field - what the user searched for
- INPUT CONTEXT "queries" field - search queries used
- All SOURCES pages

**Decision criteria:**

KEEP a page if:
- It belongs to the same domain/topic as the objective
- It contains substantive content (paragraphs, tables, documentation)
- Even if it doesn't directly answer the user's specific question

DROP a page if:
- It's from a completely different domain
- It duplicates another kept page (≥90% content overlap)
- It has no real content (nav-only, error page, pure ads)

**Your thinking for Phase 1:**
"Phase 1 - Filtering:
Evaluating sid:X - [discuss relevance to user's objective]
Decision: KEEP/DROP because [reasoning related to objective match]"

**Phase 1 output:**
A list of kept SIDs.

═══════════════════════════════════════════════════════════════
⚠️ TRANSITION: PHASE 1 → PHASE 2 ⚠️
═══════════════════════════════════════════════════════════════

**What changes:**
- Phase 1: Used objective/queries to filter pages
- Phase 2: Ignore objective/queries completely

**What carries forward:**
- Only the kept SIDs from Phase 1
- The full text content of those kept pages

**Mental reset:**
Stop thinking about what the user wanted to find.
Start thinking about extracting the entire content body from each page.

**Signal this transition in your thinking:**
After Phase 1 thinking, explicitly write:
"Phase 2 - Content Extraction:
Now extracting content bodies from kept pages..."

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT CONTENT BODIES FROM KEPT PAGES
═══════════════════════════════════════════════════════════════

**Purpose:** For each kept SID, identify where the content body starts and ends.

**Input for this phase:**
- Only the kept SIDs from Phase 1
- The full text of those pages
- DO NOT USE: objective or queries (they are irrelevant now)

**What is "content body":**
The main article/documentation text, excluding website chrome.

Content body INCLUDES (substantive material):
- All paragraphs and sections
- All tables, lists, structured data
- All code blocks and configurations
- All examples and tutorials
- All Q&A sections and FAQs
- All technical details, specifications, parameters
- All background information and context
- All comparisons and recommendations
- Basically: everything a human would read to learn from this page

Content body EXCLUDES (page chrome):
- Navigation menus and breadcrumbs
- Site headers and hero banners
- Call-to-action buttons
- Promotional carousels and product tiles
- Social media buttons
- Cookie notices and legal banners
- Footer link grids
- Partner logo walls

**Extraction strategy:**

Goal: Capture 80-95% of substantive content on the page.

Method:
1. Scan the page from top to bottom
2. Find where substantive content starts (first paragraph/heading after site header)
3. Find where substantive content ends (last paragraph/section before footer)
4. Mark these boundaries with text anchors

Span count:
- Default: 1 large span covering the entire content body
- Only use 2 spans if a massive non-informative block splits the content

Size requirements:
- Each span must be ≥1,000 characters OR ≥3 complete blocks
- Keep whole blocks intact (never cut mid-paragraph, mid-list, mid-table, mid-code, mid-Q&A)

High-recall approach:
- When uncertain if something is content or chrome → treat as content
- When a section seems somewhat useful → include it
- Better to include extra text than risk cutting useful content
- Extend span boundaries generously

**Your thinking for Phase 2:**

Start with: "Phase 2 - Content Extraction:"

Then describe work in GENERIC content extraction terms:
- ✓ "Analyzing page structure on sid:X"
- ✓ "Content body starts after navigation/header section"
- ✓ "Main content includes multiple sections and tables"
- ✓ "Content body ends before footer elements"
- ✓ "Selecting text anchors to mark these boundaries"

DO NOT mention in Phase 2 thinking:
- ✗ The user's objective or what they searched for
- ✗ Specific topics like "pricing", "API details", "configuration info"
- ✗ "Extracting [specific information type]"
- ✗ "Finding information about [topic]"

Why? Because Phase 2 extracts ALL content, not specific topics.

**Phase 2 output:**
For each kept SID: one or two span objects with 's' and 'e' anchors.

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

Anchors are short text phrases that mark where spans begin ('s') and end ('e').

**'s' anchor (start):**
- Location: First substantive heading or paragraph (skip past navigation/header)
- Format: Copy exactly 3-8 consecutive words from that location
- Uniqueness: Extend word-by-word if needed to make unique on the page
- Requirements: Must appear in the page text, must be unique

**'e' anchor (end) - EXCLUSIVE BOUNDARY:**

Critical: The span ENDS BEFORE the first character of 'e'.
The 'e' text itself is NOT included in the extracted content.

Two valid forms:
1. Empty string "" → means content continues to end of page
2. Short phrase → 3-8 words copied from AFTER the last substantive content

How to select 'e':
- Locate the last substantive section (last paragraph, table, FAQ item)
- Look at what comes AFTER that section (footer text, legal notices, "Contact us" link)
- Copy 3-8 words from that footer/chrome area
- If nothing comes after (content goes to end of page) → use ""

**Requirements for both anchors:**

Copy rules:
- EXACT character-for-character copy (like Ctrl+C / Ctrl+V)
- Preserve all punctuation, capitalization, spacing
- Do NOT paraphrase, summarize, or modify

Validation rules:
- Must be from real page text (not from URLs or image alt-text)
- Must NOT be from chrome sections (nav/footer/promotional areas)
- Must be unique on the page (if not unique, extend with more words)
- 's' must appear before 'e' in the document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY RULES
═══════════════════════════════════════════════════════════════

These are atomic units (never split):
- Paragraph
- List item
- Table row
- Code block
- Q&A pair
- FAQ item

Rule: If ANY part of a block is in the content body → include the ENTIRE block.

Anchor placement: Always place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Produce ONLY valid JSON. No explanatory text, no markdown code fences.

Structure:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped in Phase 1:
{{}}

═══════════════════════════════════════════════════════════════
EXECUTION CHECKLIST
═══════════════════════════════════════════════════════════════

Thinking structure:
□ Thinking has "Phase 1 - Filtering:" section
□ Thinking has "Phase 2 - Content Extraction:" section
□ Phase 1 discusses relevance to user's objective
□ Phase 2 uses generic content extraction language (no topic mentions)

Phase 1:
□ Used INPUT CONTEXT objective/queries to determine relevance
□ Kept pages belonging to search domain
□ Dropped unrelated or duplicate pages

Phase 2:
□ IGNORED objective/queries (not relevant in this phase)
□ Extracted 80-95% of substantive content from kept pages
□ Used 1 large span per page (or 2 if split by large chrome block)
□ Anchors mark content body start and end
□ All blocks kept intact (no mid-block cuts)
□ Anchors are exact verbatim, unique, and properly ordered
"""

def FILTER_AND_SEGMENT_BALANCED(now_iso: str) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES (BALANCED MODE)

═══════════════════════════════════════════════════════════════
OVERVIEW
═══════════════════════════════════════════════════════════════

**Your job:** Process web pages in two completely separate phases.

**Phase 1 output → Phase 2 input:** 
Your Phase 1 produces a list of kept SIDs. Phase 2 processes only those kept SIDs.

**Key distinction:**
- Phase 1: Use objective/queries to decide which pages to keep
- Phase 2: Extract TARGET (directly relevant) + CORPUS (contextual support)

**Input you will receive:**
- INPUT CONTEXT: contains "objective" and "queries" fields
- SOURCES: pages with SID numbers and full text content

**Output you will produce:**
- JSON: {{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING OUTPUT REQUIREMENT
═══════════════════════════════════════════════════════════════

**Your thinking must have TWO distinct sections, one for each phase.**

Users see your thinking to understand your progress.

**Phase 1 - Filtering:**
[Discuss page relevance to user's search objective]

**Phase 2 - Target + Corpus Extraction:**
[Discuss TARGET sections and CORPUS sections]

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

**Purpose:** Decide which pages to keep or drop.

**Input for this phase:** 
- INPUT CONTEXT "objective" field - what the user searched for
- INPUT CONTEXT "queries" field - search queries used
- All SOURCES pages

**Decision criteria:**

KEEP a page if:
- It directly addresses the objective or queries
- It contains information that helps achieve the objective

DROP a page if:
- Only tangentially related to the objective
- Duplicates another kept page (≥90% content overlap)
- Lacks substantive information

**Phase 1 output:**
A list of kept SIDs.

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT TARGET + CORPUS CONTENT
═══════════════════════════════════════════════════════════════

**Purpose:** For each kept SID, extract TARGET (directly relevant) + CORPUS (supporting context).

**Input for this phase:**
- The kept SIDs from Phase 1
- The full text of those pages
- INPUT CONTEXT objective/queries to guide extraction

**BALANCED EXTRACTION: Target + surrounding corpus for comprehension.**

**TARGET (always include):**
Sections that directly address the objective

**CORPUS (include for context):**
Sections that support understanding of the target:
- Related topics closely connected to target
- Background information, prerequisites, definitions
- Context explaining how target fits into larger framework
- Supporting data, complementary specifications
- Comparisons, tradeoffs involving the target
- Limitations, caveats, notes about the target

**EXCLUDE (completely unrelated):**
- Major sections on different topics
- Unrelated product features
- All chrome: navigation, ads, footers, CTAs

**Extraction strategy:**

Goal: Capture 50-70% of page content - target + helpful corpus.

Span count:
- Use 1-2 spans per page
- Each span covers TARGET + its related CORPUS

Size requirements:
- Each span must be ≥800 characters OR ≥3 complete blocks
- Keep whole blocks intact (never cut mid-paragraph, mid-list, mid-table, mid-code, mid-Q&A)

Balanced approach:
- When a section is TARGET → definitely include
- When a section is CORPUS (supports understanding) → include
- When uncertain if CORPUS or unrelated → include (favor context)
- When clearly unrelated to objective → exclude

**Phase 2 output:**
For each kept SID: 1-2 span objects covering TARGET + CORPUS sections.

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

Anchors are short text phrases that mark where spans begin ('s') and end ('e').

**'s' anchor (start):**
- First TARGET or CORPUS heading/paragraph
- Copy exactly 3-8 consecutive words
- Extend if needed to make unique on page

**'e' anchor (end) - EXCLUSIVE:**
Span ends BEFORE first character of 'e'.
- Empty string "" → TARGET/CORPUS continues to end of page
- OR 3-8 words copied from AFTER the last TARGET/CORPUS content

**Requirements:**
- EXACT character-for-character copy (Ctrl+C / Ctrl+V)
- Preserve punctuation, case, spacing
- Must be unique on page
- From real page text (not URLs/image-alt)
- Not from chrome sections
- 's' before 'e' in document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY
═══════════════════════════════════════════════════════════════

Atomic units: Paragraph, list item, table row, code block, Q&A pair, FAQ item

If ANY part of a block is TARGET or CORPUS → include ENTIRE block.
Place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Produce ONLY valid JSON. No explanatory text, no markdown code fences.

Structure:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped: {{}}
"""

# FILTER_AND_SEGMENT_GUIDE = lambda iso: FILTER_AND_SEGMENT_HIGH_RECALL(iso)
# FILTER_AND_SEGMENT_GUIDE = lambda iso: FILTER_AND_SEGMENT_HIGH_PRECISION(iso)
FILTER_AND_SEGMENT_GUIDE = lambda iso: FILTER_AND_SEGMENT_BALANCED(iso)
