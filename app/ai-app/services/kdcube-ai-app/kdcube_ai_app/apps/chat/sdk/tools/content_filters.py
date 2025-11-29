# Core filtering policy - understanding-based

# ----------------------------- Light-weight span slicer (post-processing) -----------------------------
import re as _re
_WORD_RE = _re.compile(r"\w+", _re.U)

def _find_phrase_ci(text: str, phrase: str, start_from: int = 0) -> int:
    """Case-insensitive exact substring first; fallback to word-separated regex match."""
    if not text or not phrase:
        return -1
    low = text.lower()
    ph = phrase.lower()

    # Try exact substring match first
    if start_from:
        idx = low.find(ph, start_from)
        if idx != -1:
            return idx
    else:
        idx = low.find(ph)
        if idx != -1:
            return idx

    # Fallback: allow non-word gaps between words (handles formatting differences)
    words = _WORD_RE.findall(ph)
    if not words:
        return -1
    pat = r"\b" + r"\W+".join(map(_re.escape, words)) + r"\b"
    m = _re.compile(pat, _re.I | _re.U).search(text, pos=start_from)
    return m.start() if m else -1


def trim_with_spans(
        content: str,
        spans: list[dict],
        *,
        ctx_before: int = 600,      # increased for wider capture
        ctx_after: int = 600,       # increased for wider capture
        min_gap: int = 400,         # increased to merge nearby spans
        max_joined: int = 30000,    # increased to allow more content
) -> str:
    """
    Cut 1–2 slices around (s,e) anchors with context; merge close slices; join with ellipses.

    Large context windows to capture complete cognitive blocks with surrounding context:
    - ctx_before/after: 600 - wide context around boundaries
    - min_gap: 400 - aggressively merge nearby spans
    - max_joined: 30000 - allow substantial content
    """
    if not content or not spans:
        return ""

    intervals: list[tuple[int, int]] = []

    for sp in spans[:2]:
        s = (sp.get("s") or "").strip()
        e = (sp.get("e") or "").strip()
        if not s or not e:
            continue

        s_idx = _find_phrase_ci(content, s)
        if s_idx == -1:
            continue

        e_search_from = s_idx + max(1, len(s))
        e_idx = _find_phrase_ci(content, e, start_from=e_search_from)

        if e_idx == -1:
            # Fallback: take substantial content after start anchor
            e_idx = min(len(content) - 1, e_search_from + 2500)

        # Expand to include context
        a = max(0, s_idx - max(0, ctx_before))
        b = min(len(content), e_idx + len(e) + max(0, ctx_after))

        if b > a:
            intervals.append((a, b))

    if not intervals:
        return ""

    # Sort and merge close intervals
    intervals.sort()
    merged = [intervals[0]]

    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe + max(0, min_gap):
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))

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
) -> list[dict]:
    """Apply span-based trimming to source rows."""
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
            min_gap=min_gap
        )

        if not pruned:
            continue

        nr = dict(r)
        nr["content_original_length"] = len(original)
        nr["content"] = pruned
        nr["content_length"] = len(pruned)
        nr["seg_spans"] = spans
        survivors.append(nr)

    return survivors

# def FILTER_AND_SEGMENT_GUIDE(now_iso: str) -> str:
#     return f"""
# GENERAL PURPOSE FILTER + SEGMENT (OBJECTIVE-AWARE; SEMANTIC DEDUP)
#
# INPUTS
# - objective: what we’re trying to achieve (may be empty)
# - queries: search queries used to find sources
# - sources: list of {{sid, content, published_time_iso?, modified_time_iso?}}
#
# GOAL
# 1) FILTER: keep the minimal set of sources that truly contain useful, substantive information for the objective.
# 2) SEGMENT: for each kept source, return **1–2 spans** that cover the page’s main informative content (“fruit”), excluding site chrome/boilerplate (“envelope”).
# 3) **SEMANTIC DEDUP (content-only):** if two sources cover the same useful content, keep only one unless each adds **substantial, non-overlapping** useful content.
#
# ----------------------------------------------------------------
# FILTER (objective-aware, no URL heuristics)
# - Relevance: keep pages with substantive information (not just navigation, promos, link hubs).
# - Substance: prefer coherent sections with paragraphs/lists/tables/code/Q&A/specs/instructions.
# - Freshness: use only as a tie-breaker when content is otherwise indistinguishable.
# - Safeguard: if at least one page has substance, keep at least one SID.
# TODAY: {now_iso}
#
# ----------------------------------------------------------------
# SEGMENT (objective-aware; wide coverage; max 2 spans)
# INTENT
# - Capture the **majority of objective-relevant content** in **one large span** when possible.
# - Use a **second span** only if a substantial non-informative block splits two large relevant clusters.
#
# INCLUDE (FRUIT)
# - Coherent informative regions: headings + paragraphs, lists, tables, code/config blocks, figures with captions, Q&A.
#
# EXCLUDE (ENVELOPE)
# - Site-wide nav, hero/marketing banners, promos/tiles, carousels, social/share bars, cookie/legal notices, footers, logo walls, repetitive link grids, pure link hubs.
#
# COVERAGE
# - Combined span(s) should contain **≥ ~70% of all relevant content** on the page.
# - **Anti-tiny rule:** each span encloses **≥ 1,000 characters or ≥ 3 full blocks (paragraphs/rows/items)** unless the page is truly short.
# - Keep whole structures intact (no mid-table/list/code cuts).
#
# SPAN COUNT & ORDER
# - Default **1 span**. Use **2** only for separated large clusters.
# - Spans must be **non-overlapping** and in **document order**.
#
# ANCHORS
# - Each span uses 's' (start) and 'e' (end), both:
#   • **Verbatim** substrings from the page text (not URLs or image filenames)
#   • **Compact & distinctive** (~3–12 words); **unique** on the page (lengthen to ensure uniqueness)
#   • **Ordered**: 's' before 'e'
#   • **Non-ENVELOPE** (don’t anchor in nav/footer/legal/cookie/social/promotional blocks)
#
# CHOOSING 's' (START)
# - The first heading or sentence that begins the **first relevant cluster** (skip envelope above).
#
# CHOOSING 'e' (END)
# - Immediately after the **last relevant block** included for that cluster.
# - Do **not** end on a heading that introduces still-relevant content; include it and move 'e' after it.
# - When unsure, **bias toward inclusion** to avoid amputating useful content.
#
# ROBUSTNESS
# - Small amounts of envelope may remain if needed to keep the body intact.
# - Short decorative separators between relevant sections should remain **inside** the span.
#
# ----------------------------------------------------------------
# SEMANTIC DEDUP (content-only; performed **after** proposing spans)
# GOAL
# - Keep the **minimum** number of SIDs whose span-enclosed content **together** covers the useful material.
# - Dedup is based **solely on the text enclosed by the proposed spans** (normalized), not on URLs/titles.
#
# NORMALIZATION (for overlap checks)
# - From each SID, concatenate text inside its proposed span(s).
# - Lowercase; collapse whitespace; remove obvious boilerplate tokens that slipped in (e.g., repeated nav/cta phrases). Keep numbers and units.
#
# OVERLAP METRICS
# - Compute:
#   • Overlap ratio O = overlap_chars / min(chars_A, chars_B)
#   • Jaccard on token n-grams (e.g., 5-grams) over span text
#   • Structural overlap (same headings/table rows/FAQ questions detected by text match)
# - Define **near-duplicate** if any strong signal holds (suggested thresholds):
#   • O ≥ 0.90  OR  5-gram Jaccard ≥ 0.85  OR  structural overlap ≥ 0.90.
#
# UNIQUE CONTRIBUTION
# - For each source, compute unique_A = (chars in A not in B) / union_chars; similarly unique_B.
# - Treat “unique” as **substantial** if unique_X ≥ 0.15 **or** it introduces **new informative structures** (new headings/sections, new table rows/parameters, new Q&A items, new constraints/notes) not present in the other.
#
# KEEP/DROP RULES
# - If A and B are near-duplicates **and neither** has substantial unique contribution:
#   → **Keep one**; **drop the other** (do not output spans for the dropped SID).
# - If both have substantial unique contribution (each adds useful, non-overlapping content):
#   → **Keep both**.
# - If only one adds substantial unique contribution:
#   → **Keep that one**, drop the other.
#
# TIE-BREAKER WHEN DROPPING ONE (content-only)
# - Prefer the candidate whose spans cover **more of the union** of relevant content (greater coverage length + more distinct headings/rows/items).
# - If still tied, prefer the one with clearer organization (fewer envelope tokens inside).
# - If still tied, either is acceptable.
#
# CLUSTERING
# - Apply the above pairwise to form duplicate clusters. From each cluster, keep the minimal subset that covers all unique contributions; drop the rest.
#
# POST-HOC COLLISION GUARD
# - If two kept SIDs end up with **identical ('s','e') anchors** or **≥ 95% identical span text**, re-apply the KEEP/DROP RULES and remove redundancies.
#
# ----------------------------------------------------------------
# VALIDATION
# - Only **kept** SIDs are in the output.
# - Each kept SID has **1–2 spans** meeting coverage and anti-tiny rules; whole structures enclosed.
# - 's'/'e' are **verbatim**, **compact** (not more than 4-5 words! Choose anchor phrases that has no complex punctuation / symbols), **unique**, **ordered**, **non-ENVELOPE**.
# - Span(s) capture the **majority** of relevant material on that page.
#
# OUTPUT CONTRACT
# - Return ONLY a JSON object mapping kept SID → array of 1–2 spans:
#   {{ "<sid>": [ {{ "s": "...", "e": "..." }}, ... ], ... }}
# - No commentary or extra keys.
# """

def FILTER_AND_SEGMENT_GUIDE(now_iso: str) -> str:
    return f"""
GENERAL PURPOSE FILTER + SEGMENT (OBJECTIVE-AWARE; SEMANTIC DEDUP)

INPUTS
- objective: what we're trying to achieve (may be empty)
- queries: search queries used to find sources
- sources: list of {{sid, content, published_time_iso?, modified_time_iso?}}

GOAL
1) FILTER: keep the minimal set of sources that truly contain useful, substantive information for the objective.
2) SEGMENT: for each kept source, return **1–2 spans** that cover the page's main informative content ("fruit"), excluding site chrome/boilerplate ("envelope").
3) **SEMANTIC DEDUP (content-only):** if two sources cover the same useful content, keep only one unless each adds **substantial, non-overlapping** useful content.

----------------------------------------------------------------
FILTER (objective-aware, no URL heuristics)
- Relevance: keep pages with substantive information (not just navigation, promos, link hubs).
- Substance: prefer coherent sections with paragraphs/lists/tables/code/Q&A/specs/instructions.
- Freshness: use only as a tie-breaker when content is otherwise indistinguishable.
- Safeguard: if at least one page has substance, keep at least one SID.
TODAY: {now_iso}

----------------------------------------------------------------
SEGMENT (objective-aware; wide coverage; max 2 spans)
INTENT
- Capture the **majority of objective-relevant content** in **one large span** when possible.
- Use a **second span** only if a substantial non-informative block splits two large relevant clusters.

INCLUDE (FRUIT)
- Coherent informative regions: headings + paragraphs, lists, tables, code/config blocks, figures with captions, Q&A.

EXCLUDE (ENVELOPE)
- Site-wide nav, hero/marketing banners, promos/tiles, carousels, social/share bars, cookie/legal notices, footers, logo walls, repetitive link grids, pure link hubs.

COVERAGE
- Combined span(s) should contain **≥ ~70% of all relevant content** on the page.
- **Anti-tiny rule:** each span encloses **≥ 1,000 characters or ≥ 3 full blocks (paragraphs/rows/items)** unless the page is truly short.
- Keep whole structures intact (no mid-table/list/code cuts).

SPAN COUNT & ORDER
- Default **1 span**. Use **2** only for separated large clusters.
- Spans must be **non-overlapping** and in **document order**.

----------------------------------------------------------------
ANCHORS (CRITICAL: EXACT VERBATIM COPY REQUIRED)

**FUNDAMENTAL RULE:** 
Anchor text MUST be **EXACT CHARACTER-FOR-CHARACTER COPY** from the source content.
- Copy the text EXACTLY as it appears, including ALL punctuation, symbols, capitalization, and spacing
- DO NOT paraphrase, simplify, or "clean up" the anchor text
- DO NOT remove quotes, apostrophes, hyphens, parentheses, or any other characters
- DO NOT change capitalization
- Think of it as Ctrl+C / Ctrl+V - an exact copy

**WHAT THIS MEANS:**
✓ CORRECT: "Section 2.1: Overview" (if source has "Section 2.1: Overview")
✗ WRONG: "Section 2.1 Overview" (removed the colon)

✓ CORRECT: "What's the difference between" (if source has "What's the difference between")
✗ WRONG: "What is the difference between" (changed apostrophe)

✓ CORRECT: "API Reference (v2.0)" (if source has "API Reference (v2.0)")
✗ WRONG: "API Reference v2.0" (removed parentheses)

✓ CORRECT: "Step #3 - Configure settings" (if source has "Step #3 - Configure settings")
✗ WRONG: "Step 3 Configure settings" (removed symbols)

**Each span uses 's' (start) and 'e' (end), both MUST be:**
  • **VERBATIM EXACT COPY** - character-for-character identical to source text, preserving ALL punctuation and symbols
  • **Compact & distinctive** - aim for 3-8 words (shorter is better if unique)
  • **Unique on the page** - if not unique, extend the phrase until it becomes unique
  • **Ordered** - 's' must appear before 'e' in the document
  • **Non-ENVELOPE** - don't anchor in nav/footer/legal/cookie/social/promotional blocks
  • **From actual content text** - NOT from URLs, image filenames, or alt text

**CHOOSING 's' (START):**
- Locate the first heading or sentence that begins the **first relevant cluster** (skip envelope above)
- Copy EXACTLY 3-8 consecutive words from that point
- Include ALL punctuation/symbols exactly as they appear
- If the phrase is not unique on the page, extend it word-by-word until unique

**CHOOSING 'e' (END):**
- Locate the point immediately after the **last relevant block** to include
- Copy EXACTLY the last 3-8 words of that block (or first 3-8 words of the next sentence/heading)
- Include ALL punctuation/symbols exactly as they appear
- Do **not** end on a heading that introduces still-relevant content; include that content and move 'e' after it
- When unsure, **bias toward inclusion** to avoid amputating useful content

**VERIFICATION CHECKLIST (before outputting):**
For each anchor ('s' and 'e'), verify:
1. Can I find this EXACT string (including all punctuation) in the source with Ctrl+F? → Must be YES
2. Does it include ALL symbols, punctuation, capitalization from the original? → Must be YES
3. Is it unique on this page? → Must be YES (if no, extend the phrase)
4. Is it 3-8 words? → Preferred (can be slightly longer if needed for uniqueness)
5. Does it avoid envelope sections (nav/footer/legal/social)? → Must be YES

**ROBUSTNESS:**
- Small amounts of envelope may remain if needed to keep the body intact
- Short decorative separators between relevant sections should remain **inside** the span

----------------------------------------------------------------
SEMANTIC DEDUP (content-only; performed **after** proposing spans)
GOAL
- Keep the **minimum** number of SIDs whose span-enclosed content **together** covers the useful material.  
- Dedup is based **solely on the text enclosed by the proposed spans** (normalized), not on URLs/titles.

NORMALIZATION (for overlap checks)
- From each SID, concatenate text inside its proposed span(s).
- Lowercase; collapse whitespace; remove obvious boilerplate tokens that slipped in (e.g., repeated nav/cta phrases). Keep numbers and units.

OVERLAP METRICS
- Compute:
  • Overlap ratio O = overlap_chars / min(chars_A, chars_B)  
  • Jaccard on token n-grams (e.g., 5-grams) over span text  
  • Structural overlap (same headings/table rows/FAQ questions detected by text match)
- Define **near-duplicate** if any strong signal holds (suggested thresholds):
  • O ≥ 0.90  OR  5-gram Jaccard ≥ 0.85  OR  structural overlap ≥ 0.90.

UNIQUE CONTRIBUTION
- For each source, compute unique_A = (chars in A not in B) / union_chars; similarly unique_B.
- Treat "unique" as **substantial** if unique_X ≥ 0.15 **or** it introduces **new informative structures** (new headings/sections, new table rows/parameters, new Q&A items, new constraints/notes) not present in the other.

KEEP/DROP RULES
- If A and B are near-duplicates **and neither** has substantial unique contribution:
  → **Keep one**; **drop the other** (do not output spans for the dropped SID).
- If both have substantial unique contribution (each adds useful, non-overlapping content):
  → **Keep both**.
- If only one adds substantial unique contribution:
  → **Keep that one**, drop the other.

TIE-BREAKER WHEN DROPPING ONE (content-only)
- Prefer the candidate whose spans cover **more of the union** of relevant content (greater coverage length + more distinct headings/rows/items).  
- If still tied, prefer the one with clearer organization (fewer envelope tokens inside).  
- If still tied, either is acceptable.

CLUSTERING
- Apply the above pairwise to form duplicate clusters. From each cluster, keep the minimal subset that covers all unique contributions; drop the rest.

POST-HOC COLLISION GUARD
- If two kept SIDs end up with **identical ('s','e') anchors** or **≥ 95% identical span text**, re-apply the KEEP/DROP RULES and remove redundancies.

----------------------------------------------------------------
VALIDATION
- Only **kept** SIDs are in the output.
- Each kept SID has **1–2 spans** meeting coverage and anti-tiny rules; whole structures enclosed.
- **CRITICAL:** 's' and 'e' are **EXACT VERBATIM COPIES** from source (preserving ALL punctuation/symbols), **compact** (3-8 words preferred), **unique**, **ordered**, **non-ENVELOPE**.
- Span(s) capture the **majority** of relevant material on that page.

OUTPUT PROTOCOL
**THINKING (user-visible):** 1-2 short sentences summarizing observations and decisions.

**MAIN OUTPUT:** ONLY valid JSON. No text before/after.
- If sources kept:  a JSON object mapping kept SID → array of 1–2 spans
  {{ "<sid>": [ {{ "s": "...", "e": "..." }}, ... ], ... }}
- If all dropped: {{}}  

No extra keys and never return free text explanations in main output. Empty object {{}} if no results.
"""