---
name: docx-press
description: |
  Teaches agents how to author Markdown that renders cleanly into DOCX via
  write_docx, with heading hierarchy (up to 6 levels), tables, lists,
  blockquotes, code blocks, citations, embedded images, SVG diagrams
  (rendered to PNG first), and document structure patterns for reports,
  proposals, and technical documents.
version: 3.1.0
category: document-creation
tags:
  - docx
  - markdown
  - tables
  - citations
  - images
  - svg
  - headings
  - blockquotes
  - code
when_to_use:
  - Generating Markdown for write_docx
  - Building structured DOCX reports with deep heading hierarchies
  - Including citations and a references section
  - Embedding images and SVG diagrams in documents
  - Creating proposals, whitepapers, executive briefs as DOCX
author: kdcube
created: 2026-01-16
updated: 2026-04-14
namespace: public
import:
  - internal.link-evidence
  - internal.sources-section
  - public.svg-press
---

# DOCX Press — Professional Document Authoring

## Overview

This skill teaches how to produce Markdown that renders cleanly into DOCX via
`write_docx`. The renderer is python-docx based — it parses your Markdown into
native Word elements (paragraphs, headings, tables, code blocks, blockquotes,
images, hyperlinks, and citations). You write standard Markdown — the renderer
handles all styling with a fixed professional palette.

## Tool

```
write_docx(path, content, title?, include_sources_section?)
```

- `content`: Markdown string. The renderer processes headings, lists, tables,
  code fences, blockquotes, images, links, and citation tokens.
- `path`: Relative `.docx` path under OUT_DIR.
- `title`: Optional document title (overrides the first `# Heading`).
- `include_sources_section`: Append a "References" section (default True).

**Important:** This is a Markdown-only tool. Unlike `write_pdf` (which supports
HTML and Mermaid) or `write_pptx` (which uses HTML sections), `write_docx` takes
only Markdown input.

---

## Core Rules

1. Use headings (`#` through `######`) to structure sections with proper hierarchy
2. Prefer short paragraphs and concise lists
3. Use pipe tables with a header row; keep column counts modest (3–5)
4. Images use `![alt text](path/to/image.png)` — local files only
5. Use `> text` for blockquotes (rendered as shaded boxes with left accent)
6. Use triple backticks for code blocks (rendered in styled boxes)
7. URLs are auto-linked: just write `https://example.com`
8. Markdown links for custom text: `[click here](https://example.com)`
9. Avoid HTML tags — keep to Markdown primitives; HTML is not processed

---

## How the Renderer Structures Your Document

Understanding the renderer's logic prevents common mistakes:

1. **First `# Heading`** → becomes the **document title** (large, bold, with
   horizontal rule beneath)
2. **Each `## Heading`** → creates a **major document section** (H1 styled).
   The renderer splits the document by `## ` boundaries.
3. **`###` through `######`** → rendered as progressively smaller, indented
   headings within their parent section
4. **Everything else** (paragraphs, lists, tables, etc.) → rendered as body
   content within the current section

This means:
- Your document should always start with `# Document Title`
- Major sections should use `## Section Name`
- Sub-structure within sections uses `###`, `####`, etc.
- **Do not skip levels** (e.g., `#` followed by `###` with no `##`)

---

## Heading Hierarchy

The renderer supports 6 heading levels with automatic styling:

```markdown
# Document Title (becomes the title — Pt 22, bold)
## Major Section (H1 — Pt 18, bold)
### Subsection (H2 — Pt 16, bold)
#### Sub-subsection (H3 — Pt 14, bold)
##### Detail (H4 — Pt 12.5, bold, indented 0.25in)
###### Fine Detail (H5 — Pt 12, bold, indented 0.5in)
```

Levels 1–3 use Word's built-in heading styles (Heading 1, 2, 3). Levels 4–6
are custom-styled with progressive left indentation (0.25in per level beyond 3).

**Best Practices:**
- Use `##` for major section breaks (executive summary, methodology, findings, etc.)
- Use `###` and `####` for hierarchical content within sections
- Don't skip levels (e.g., `#` followed by `###`)
- Deeper headings (`#####`, `######`) are best for detailed technical content;
  most documents work well with 2–4 levels
- Spacing above headings decreases at deeper levels (auto-calculated)

---

## Supported Markdown Elements

### Paragraphs

Plain text renders as body paragraphs (Pt 11.5, with compact spacing).
Inline formatting:

```markdown
Use **bold** for important terms and labels.
Use *italic* for emphasis or notes.
Combine for ***very important*** (though rarely needed).
```

### Lists

Bullet and numbered lists are supported with nesting:

```markdown
- First bullet point
- Second bullet point
  - Nested bullet (2-space indent)
    - Deeper nested (4-space indent)

1. First numbered item
2. Second numbered item
   1. Nested numbered item
```

**Nesting rules:**
- Indent with 2 spaces per level for nested lists
- Maximum 4 indent levels (deeper levels are capped)
- Use `-` or `*` for unordered lists
- Use `1.` for ordered lists
- The renderer uses Word's built-in "List Bullet" and "List Number" styles

### Tables

Pipe tables with a header row and separator:

```markdown
| Metric | Q3 2025 | Q4 2025 | Change |
| --- | --- | --- | --- |
| Revenue | $2.5M | $3.2M | +28% |
| Customers | 1,200 | 1,650 | +38% |
| NPS | 62 | 71 | +9 |
```

**Table behavior:**
- Columns are equally distributed across the 6-inch page width
- Headers: bold, centered, with light blue-gray background (#F0F4FC)
- Data cells: left-aligned, normal weight
- No merged cells — every row must have the same number of columns
- The separator row (`| --- | --- |`) is required and must have at least 3 dashes per cell

**Best practices:**
- Keep to 3–5 columns for readability
- Include units in headers (e.g., "Revenue ($M)")
- Use clear, concise headers
- For wider data, consider splitting into multiple tables

### Code Blocks

Fenced code blocks render in a styled box with background shading and border:

````markdown
```python
def calculate_roi(investment, returns):
    return (returns - investment) / investment * 100
```
````

**Behavior:**
- Rendered in a 1x1 table cell with light background (#FAFAFC) and border (#DCE0E6)
- Monospace font (Consolas, Pt 10.5)
- Tight line spacing (1.1)
- Specify language after opening backticks for semantic clarity (though
  syntax highlighting is not applied in the DOCX)

### Blockquotes

Use `>` prefix for quoted or callout text:

```markdown
> This approach reduces infrastructure costs by 40% while maintaining
> five-nines availability across all production workloads.
```

**Behavior:**
- Rendered as a shaded box (#F5F7FA) with a thick left rule (#DCE0E6)
- Italic text in muted color
- Good for: key quotes, important notes, callout-style emphasis
- Multi-line quotes: prefix each line with `>`

### Images

```markdown
![Core Architecture Diagram](diagrams/core-arch.png)
![Network Topology](images/network.png)
```

**Behavior:**
- Images are centered with 6-inch max width (full page width)
- Alt text becomes a caption below the image (italic, centered, muted color)
- Use descriptive alt text — it serves as both accessibility label and visible caption
- Supported formats: PNG, JPEG, GIF, BMP
- Paths are resolved relative to OUT_DIR (the tool's output directory)
- If the image file is not found, a `[Image not found: path]` placeholder appears

### SVG Diagrams

DOCX does not support SVG directly. Render SVGs to PNG first using `write_png`
(see `svg-press` skill), then embed the PNG:

```markdown
![System Architecture: Three-Layer Boundary](diagrams/diagram-1-layers.png)
![Data Flow: Customer-Side Processing](diagrams/diagram-2-data-flow.png)
```

**Rendering SVG to PNG for DOCX:**
```
write_png(
  path="diagrams/diagram-1-layers.png",
  format="html",
  content='<html><body style="margin:0;background:white">' + svg_string + '</body></html>',
  fit="content",
  content_selector="svg",
  width=2400,
  device_scale_factor=3
)
```

Use high resolution (`device_scale_factor=3`, `width=2400`) so the diagram
is crisp when printed or viewed at high zoom.

### Links and URLs

**Plain URLs** are automatically converted to clickable hyperlinks:

```markdown
Visit https://www.example.com for more information.
Additional resources at www.documentation.org and http://api.example.com/docs
```

All three formats are auto-detected:
- `https://example.com` — full URL with protocol
- `http://example.com` — HTTP protocol
- `www.example.com` — auto-converted to `http://www.example.com`

**Markdown links** for custom display text:

```markdown
[Read our documentation](https://docs.example.com)
[Download the report](https://example.com/report.pdf)
```

Links render in the accent color (blue) with the URL as the hyperlink target.

### Citations

Use `[[S:n]]` tokens inline after factual claims:

```markdown
Global EV sales grew 35% YoY in 2024 [[S:1]]. Growth is accelerated
by policy incentives [[S:2]] and falling battery costs [[S:3]].
```

**Behavior:**
- When sources are available, `[[S:n]]` is resolved to the source title as
  a clickable hyperlink (blue accent color)
- When `include_sources_section=True` (default), a "References" section is
  appended with numbered entries: `[n] Title` + URL
- Only include source IDs that exist in the sources pool

---

## Document Styling (Configurable Palette)

The DOCX renderer uses a **`DocxTheme`** that controls palette, type scale, and
mono font. The theme is passed to `render_docx()` via the `theme` keyword
argument. Two pre-built themes are provided:

- **`DEFAULT_THEME`** — neutral professional palette (original behaviour)
- **`KDCUBE_THEME`** — KDCube brand colors (teal/blue family)

You cannot change the palette from Markdown — it is configured at the call site
in the tool module (e.g. `rendering_tools.py`). The agent only needs to know
which palette is currently active so it can describe colors accurately.

**KDCube palette (currently active):**

| Element | Style |
|---------|-------|
| Body text | Pt 11.5, dark navy (#0D1E2C) |
| Headings | Bold, dark navy, sized by level (Pt 18 → Pt 11.5) |
| Links & citations | KDCube blue (#4372C3), clickable |
| Table headers | Bold, centered, pale blue bg (#DDEAFE) |
| Code blocks | Consolas Pt 10.5, light teal bg (#F6FAFA), bordered (#D8ECEB) |
| Blockquotes | Italic, muted (#7A99B0), teal-tinted bg (#EEF8F7), left rule (#D8ECEB) |
| Image captions | Italic, Pt 10, muted (#7A99B0) |
| Muted text | Muted teal-gray (#7A99B0) |

**Default palette (fallback when no theme is passed):**

| Element | Style |
|---------|-------|
| Body text | Pt 11.5, dark gray (#14181F) |
| Headings | Bold, dark gray, sized by level (Pt 18 → Pt 11.5) |
| Links & citations | Blue accent (#1F6FEB), clickable |
| Table headers | Bold, centered, light blue-gray bg (#F0F4FC) |
| Code blocks | Consolas Pt 10.5, light gray bg (#FAFAFC), bordered (#DCE0E6) |
| Blockquotes | Italic, muted gray (#5F6A79), shaded bg (#F5F7FA), left rule (#DCE0E6) |
| Image captions | Italic, Pt 10, muted gray (#5F6A79) |
| Muted text | Gray (#5F6A79) |

Both palettes produce clean, professional output. If you need fully custom
styling beyond what a theme provides, use `write_pdf` with `format='html'`.

---

## Document Structure Patterns

### Pattern 1: Executive Brief (Short)

For concise 2–4 page documents focused on decisions:

```markdown
# Quarterly Business Review

## Executive Summary

Key findings and recommendations in 2-3 paragraphs [[S:1]].

> Our Q4 revenue exceeded targets by 18%, driven primarily by
> enterprise expansion in the APAC region.

## Key Metrics

| Metric | Target | Actual | Status |
| --- | --- | --- | --- |
| Revenue | $2.5M | $3.2M | Exceeded |
| NRR | 110% | 115% | Exceeded |
| CAC Payback | 14mo | 11mo | Exceeded |

## Recommendations

- **Expand APAC sales team** — hire 3 additional AEs by Q2
- **Launch mid-market tier** — capture underserved segment
- **Invest in self-serve** — reduce CAC by 25%

## Next Steps

1. Board presentation: March 15
2. Hiring plan finalized: March 22
3. Pricing model review: April 1
```

### Pattern 2: Technical Report (Detailed)

For in-depth documents with deep heading hierarchy:

```markdown
# Platform Migration Assessment

## Executive Summary

Brief overview with key findings [[S:1]].

## Background and Context

### Current Architecture

Description of existing systems.

#### Database Layer

- PostgreSQL 14 cluster (3 nodes)
- 2.3TB total data volume
- Average query latency: 45ms

#### Application Layer

##### API Services
REST endpoints serving 12M requests/day.

##### Background Workers
Async job processing: ~500K jobs/day.

### Migration Drivers

Why migration is necessary.

## Proposed Architecture

![Target Architecture](diagrams/target-arch.png)

### Component Breakdown

#### Data Pipeline

| Component | Technology | Purpose |
| --- | --- | --- |
| Ingestion | Kafka | Event streaming |
| Transform | Flink | Real-time processing |
| Storage | S3 + Iceberg | Durable data lake |

### Migration Phases

#### Phase 1: Data Layer (Weeks 1-4)

##### Tasks
- Schema migration and validation
- Dual-write implementation
- Performance benchmarking

##### Success Criteria
- Query latency < 50ms at P99
- Zero data loss during migration

#### Phase 2: Application Layer (Weeks 5-10)

##### Tasks
- Service-by-service migration
- Integration testing
- Load testing at 2x production traffic

## Risk Assessment

### Technical Risks

#### Data Consistency

**Likelihood:** Medium | **Impact:** High

> Dual-write period introduces risk of consistency drift between
> old and new data stores. Automated reconciliation jobs will run
> hourly during the transition.

##### Mitigation Strategies
- Automated reconciliation with alerting
- Shadow read comparison for 2 weeks
- Rollback plan with < 4hr RTO

## Recommendations

Summary of actionable recommendations.

## References
```

### Pattern 3: Proposal / Sales Document

For client-facing proposals:

```markdown
# Integration Proposal for Acme Corp

## Executive Summary

Proposed solution overview tailored to Acme's needs.

## Understanding Your Requirements

### Current Challenges

Based on our discovery sessions:

- **Data Silos** — 5 disconnected systems with manual reconciliation
- **Latency** — 4-hour delay between order and fulfillment update
- **Compliance** — Manual audit trail assembly taking 40+ hours/quarter

### Desired Outcomes

- Real-time data synchronization across all systems
- Sub-minute order-to-fulfillment visibility
- Automated compliance reporting

## Proposed Solution

![Solution Architecture](diagrams/acme-integration.png)

### Core Components

#### Real-Time Event Bus

Kafka-based event streaming connecting all five systems.

#### Data Transformation Layer

Automated mapping and validation between system schemas.

### Implementation Timeline

| Phase | Duration | Deliverable |
| --- | --- | --- |
| Discovery | 2 weeks | Requirements document |
| Build | 6 weeks | Core integration |
| Testing | 2 weeks | UAT sign-off |
| Go-Live | 1 week | Production deployment |

## Investment

### Pricing Structure

| Component | One-Time | Monthly |
| --- | --- | --- |
| Platform license | — | $8,500 |
| Implementation | $45,000 | — |
| Support (24/7) | — | $2,500 |

### ROI Projection

> Based on current manual reconciliation costs of $180K/year and
> compliance labor of $120K/year, projected ROI payback period
> is 7 months.

## Next Steps

1. Technical deep-dive session — Week of April 21
2. Security review and data classification — April 28
3. SOW and contract finalization — May 5
4. Kickoff — May 12
```

---

## Formatting Tips

**Lists:**
- Use `-` or `*` for bullets
- Use `1.` for numbered lists
- Indent with 2 spaces per level for nested lists
- Max 4 indent levels

**Emphasis:**
- Use `**bold**` for important terms, labels, key-value pairs
- Use `*italic*` for emphasis, notes, or species names
- Combine for `***very important***` (rarely needed)

**Tables:**
- Keep columns to 3–5 for readability
- Use clear, concise headers with units
- Separator row requires at least 3 dashes: `| --- |`
- Every data row must have the same number of columns as the header

**Code:**
- Use triple backticks with optional language name
- Keep code samples concise and relevant
- Code blocks are best for short examples (< 30 lines)

**Blockquotes:**
- Prefix each line with `> `
- Use for: key insights, important notes, callout-style emphasis, quotes
- Keep blockquotes short (1–3 lines) for visual impact

**Horizontal structure:**
- The DOCX renderer does not support multi-column layouts. All content flows
  in a single column. For multi-column needs, use `write_pdf` with HTML format.
- For side-by-side comparisons, use tables instead of columns.

---

## Common Mistakes to Avoid

### 1. Skipping Heading Levels

```markdown
# Title
### Subsection  ← BAD: skipped ##
```

Always maintain hierarchy: `#` → `##` → `###` → `####`

### 2. Using HTML Tags

```markdown
<div><h4>Title</h4></div>  ← BAD: HTML is not processed
```

Use Markdown headings: `#### Title`

### 3. Over-Nesting Headings

```markdown
###### Six levels deep everywhere  ← BAD: hard to read
```

Most documents work well with 2–4 heading levels. Reserve `#####` and `######`
for genuinely detailed technical content.

### 4. Missing Table Separator

```markdown
| Header 1 | Header 2 |
| Data 1 | Data 2 |     ← BAD: no separator row — won't parse as table
```

Always include the separator: `| --- | --- |`

### 5. Inconsistent Table Column Count

```markdown
| A | B | C |
| --- | --- | --- |
| 1 | 2 |           ← BAD: only 2 columns, header has 3
```

Every row must have the same number of columns (the renderer pads missing cells,
but it's better to be explicit).

### 6. Base64 Images

```markdown
![Image](data:image/png;base64,iVBORw0KG...)  ← BAD: won't work
```

Use file paths: `![Image](diagrams/chart.png)`

### 7. Remote URLs as Image Sources

```markdown
![Logo](https://example.com/logo.png)  ← BAD: not supported
```

Download images first, save to OUT_DIR, then reference by local path.

### 8. SVG Images Directly

```markdown
![Diagram](diagrams/arch.svg)  ← BAD: DOCX doesn't support SVG
```

Render SVG to PNG with `write_png` first, then embed the PNG.

### 9. Expecting Inline Custom Colors or Fonts

The DOCX renderer applies its palette globally via a `DocxTheme` — you cannot
override colors per-element from Markdown. Writing
`**<span style="color:red">Warning</span>**` won't produce red text. If you
need per-element custom styling, use `write_pdf` with HTML.

### 10. Starting Without `#` Title

```markdown
## Introduction   ← The renderer treats this as the first section
Some text...      ← No document title is set
```

Always start with `# Document Title` so the renderer generates a proper
title block with horizontal rule.

---

## Remember

- **Markdown only** — no HTML, no CSS, no custom fonts or colors
- **Start with `# Title`** — becomes the document title with styled rule
- **`## Headings` create sections** — the document is split at `##` boundaries
- **6 heading levels** — but 2–4 is typical for most documents
- **Pipe tables require separator row** — `| --- | --- |` with 3+ dashes each
- **Images: local file paths only** — no base64, no URLs, no SVG
- **SVG → PNG first** — use `write_png` with high resolution, then embed
- **Blockquotes with `>`** — render as styled callout boxes
- **Code blocks in triple backticks** — render in bordered boxes with monospace
- **Citations: `[[S:n]]`** — resolved to hyperlinked titles; References section auto-appended
- **Theme-driven palette** — currently KDCube brand colors; no inline overrides from Markdown
- **Single column only** — for multi-column layouts, use `write_pdf` with HTML
