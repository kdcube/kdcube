---
name: pptx-press
description: |
  Teaches agents how to author slide-structured HTML that renders professionally to PPTX
  via write_pptx, with proper sizing, styling, brand and domain-adaptive color schemes,
  SVG diagram integration, citation handling, deck narrative flow, and content budgets
  for business presentations, technical decks, and executive briefings.
version: 3.0.0
category: presentation-creation
tags:
  - pptx
  - slides
  - html
  - svg
  - presentations
  - business
  - executive
  - diagrams
when_to_use:
  - Generating HTML for write_pptx tool
  - Creating professional slide decks
  - Building citation-aware presentations
  - Designing executive briefings
  - Making technical presentations with data and diagrams
  - Including architecture diagrams or data-flow SVGs in slides
  - Building branded presentations with custom color schemes
author: kdcube
created: 2026-01-16
updated: 2026-04-14
namespace: public
import:
  - public.svg-press
---

# PPTX Press — Professional Slide Deck Authoring

## Overview

This skill teaches how to produce HTML that renders cleanly into PPTX slides via
`write_pptx`. The renderer is python-pptx based — it parses your HTML `<section>`
elements into native PowerPoint slides with text boxes, tables, images, and
two-column layouts. It is NOT a browser/Chromium render; only the subset of HTML
and CSS documented here is supported.

## Tool

```
write_pptx(path, content, title?, include_sources_slide?, base_dir?)
```

- `content`: HTML string. One `<section>` per slide.
- `path`: Relative `.pptx` path under OUT_DIR.
- `include_sources_slide`: Append auto-generated Sources slide (default False).
- `base_dir`: Base directory for resolving relative image paths (defaults to OUT_DIR).

---

## Core Principles

### 1. Every Slide is a `<section>`

```html
<section id="slide-1">
  <h1>Slide Title</h1>
  <p class="subtitle">Optional one-line subtitle</p>
  <!-- body content -->
</section>
```

**CRITICAL:** Content outside `<section>` tags is **silently ignored**. This is the
most common authoring mistake.

```html
<!-- BAD: nothing renders -->
<body>
  <h1>Title</h1>
  <p>Content...</p>
</body>

<!-- GOOD: renders as one slide -->
<body>
  <section id="slide-1">
    <h1>Title</h1>
    <p>Content...</p>
  </section>
</body>
```

### 2. Content Budget — Prevent Auto-Scaling

The renderer measures content height and auto-scales down (min ~70%) when it
overflows. Aggressive scaling makes text unreadable. **Budget content to avoid it.**

| Slide type | Budget |
|------------|--------|
| Standard | 1 heading + 6 short bullets OR 2 paragraphs (~25–40 words each) OR 1 callout (~25–40 words) |
| Two-column | Each column: 1 h3 + 3 bullets OR 2 short paragraphs; max ~12 lines/column |
| Table | max 6 columns, max 8 rows; concise cell text |
| Title | max 8 words (one line) |
| Subtitle | max 12 words (one sentence) |

If content exceeds budget → split into multiple slides rather than cramming.

### 3. Professional Styling — Less is More

- Stick to 2–3 colors per deck (primary + accent + neutrals)
- Use primary for headings and key elements
- Use accent sparingly for emphasis
- Keep backgrounds light for readability
- Vary layouts between slides for visual interest (single-column, two-column, table, image)

### 4. Citation Integration

Citations are concise inline `[n]` markers. Full details go in an auto-generated
Sources slide. They should not disrupt reading flow.

---

## Supported HTML Elements

### Headings

```html
<h1>Slide Title</h1>        <!-- required; becomes the slide title -->
<h2>Section Heading</h2>    <!-- body heading, larger -->
<h3>Subsection Heading</h3> <!-- body heading, smaller -->
```

### Paragraphs

```html
<p>Body text with <strong>bold</strong> and <em>italic</em> formatting.</p>
<p class="subtitle">Subtitle text (only in first position after h1)</p>
```

### Lists

```html
<ul>
  <li><strong>Point:</strong> Short explanation</li>
  <li><strong>Another:</strong> Keep bullets concise</li>
</ul>
```

Ordered `<ol>` is also supported.

### Tables

```html
<table>
  <thead>
    <tr><th>Metric</th><th>Q3</th><th>Q4</th></tr>
  </thead>
  <tbody>
    <tr><td>Revenue</td><td>$2.5M</td><td>$3.1M</td></tr>
  </tbody>
</table>
```

Always provide `<thead>` and `<tbody>`. No merged cells.

### Two-Column Layout

```html
<div class="two-column">
  <div class="column">
    <h3>Left Side</h3>
    <ul><li>Point one</li><li>Point two</li></ul>
  </div>
  <div class="column">
    <h3>Right Side</h3>
    <p>Comparison or complementary content.</p>
  </div>
</div>
```

Columns support: headings, paragraphs, lists, tables, callouts, and images.

### Callout Boxes

The renderer promotes any div with a recognized callout class or `border-left` style
into a callout box (background fill + left accent bar).

**Recognized callout classes:**

| Class | Use |
|-------|-----|
| `highlight-box` | General emphasis / key insight |
| `highlight` | Same as highlight-box |
| `callout` | Generic callout |
| `warning` | Warning or caution |
| `success` | Positive outcome / achievement |
| `phase-box` | Phase or stage in a process |
| `comparison-item` | Item in a comparison layout |

Any div with `border-left` styling also renders as a callout.

```html
<div class="highlight-box">
  <h3>Key Insight</h3>
  <p>Important information that needs visual emphasis.</p>
</div>

<!-- Or with inline styling -->
<div style="background: #e8f2ff; border-left: 4px solid #0066cc;">
  <h3>Note</h3>
  <p>Custom-styled callout.</p>
</div>
```

**Budget:** Max 1–2 callouts per slide. Each should be ~25–40 words.

### Paragraph-Like Divs

These classes render as paragraph elements:

| Class | Use |
|-------|-----|
| `reference-link` | Clickable source / resource link |
| `note` | Brief annotation |
| `description` | Descriptive text block |

```html
<div class="reference-link">
  <strong>World Bank Open Data</strong>
  <a href="https://data.worldbank.org">Economic Indicators Database</a>
</div>
```

### Images

```html
<!-- Relative from OUT_DIR -->
<img src="turn_id/files/revenue_chart.png" width="640" alt="Revenue Chart">

<!-- With explicit dimensions -->
<img src="images/architecture.png" style="width:5in; height:3in;" alt="Architecture">
```

**Rules:**
- MUST use relative file paths from OUT_DIR — **never base64 data URIs**
- Sizing: `width="640"` (pixels, converts to ~6.7in at 96dpi) or `style="width:5in; height:3in;"`
- Supported units: px, pt, in
- Images auto-fit to slide width if too large
- Top-level images appear below title
- Images in columns appear inline with column content

### Inline Formatting

```html
<strong>bold</strong>  <b>bold</b>
<em>italic</em>        <i>italic</i>
<span class="custom-class">styled text</span>
```

---

## Supported CSS (and ONLY These)

The renderer parses a limited CSS subset. Everything else is **silently ignored**.

### What Works

```css
/* Colors — hex only, no rgb()/hsl()/named colors */
color: #333333;
background: #f0f4f8;
background-color: #0066cc;

/* Typography */
font-size: 18pt;    /* also: 1.2em, 16px */
line-height: 1.3;   /* number or percentage */

/* Spacing */
padding: 0.2in;          /* shorthand: all four sides */
padding-left: 0.3in;     /* individual sides */
/* Units: px, pt, in, em/rem */

/* Borders — for accents and underlines only */
border-bottom: 3px solid #0066cc;   /* title underline */
border-left: 4px solid #ff6b35;     /* callout accent bar */

/* Two-column */
.two-column { gap: 0.3in; }
.column { background: #f8f9fa; padding: 0.2in; border-left: 3px solid #0066cc; }

/* Tables */
th { background-color: #0066cc; color: #ffffff; }
tr:nth-child(even) { background-color: #f0f4f8; }
```

### What Does NOT Work (Silently Ignored)

- `min-height`, `100vh`, `max-height`
- `display: flex`, `display: grid` (except implicit two-column)
- `position: absolute/fixed/sticky`
- `box-shadow`, `border-radius`, gradients
- `transform`, `transition`, `animation`
- `opacity` (partial only)
- `page-break-*` properties
- `* { ... }` global resets
- Large wrapper divs with generic styling
- `rgb()`, `hsl()`, named colors — **hex only**

---

## Color Schemes

### Option A: Brand Colors (KDCube)

When building KDCube-branded presentations:

```css
:root {
  --primary: #2B4B8A;      /* Blue-dark — headings, table headers */
  --accent: #01BEB2;       /* Teal — highlights, callout borders */
  --text: #0D1E2C;         /* Dark text */
  --bg-light: #F6FAFA;     /* Slide background tint */
  --bg-callout: #E5FAF8;   /* Teal wash for callouts */
  --bg-alt: #EEF8F7;       /* Alternate table rows */
  --gold: #F0BC2E;         /* Warm accents, badges */
  --purple: #6B63FE;       /* Integration / tech highlights */
}

h1 { color: var(--primary); border-bottom: 3px solid var(--accent); }
h2 { color: var(--primary); }
.highlight-box { background: var(--bg-callout); border-left: 4px solid var(--accent); }
th { background-color: var(--primary); color: #ffffff; }
tr:nth-child(even) { background-color: var(--bg-alt); }
```

**Semantic usage:**

| Role | Token | Hex |
|------|-------|-----|
| Headings (h1, h2) | `--primary` | #2B4B8A |
| Title underline | `--accent` | #01BEB2 |
| Table header bg | `--primary` | #2B4B8A |
| Table alt rows | `--bg-alt` | #EEF8F7 |
| Callout background | `--bg-callout` | #E5FAF8 |
| Callout border | `--accent` | #01BEB2 |
| Body text | `--text` | #0D1E2C |
| Warm emphasis | `--gold` | #F0BC2E |
| Tech / integration | `--purple` | #6B63FE |

### Option B: Domain-Adaptive Color Schemes

When no brand colors are provided, choose based on document domain:

```css
/* Business / Corporate */
:root {
  --primary: #0066cc;      /* IBM Blue */
  --accent: #00a86b;       /* Success Green */
  --text: #333333;
  --bg-light: #f0f4f8;
  --bg-callout: #e8f2ff;
}

/* Tech / Engineering */
:root {
  --primary: #1e3a8a;      /* Deep Blue */
  --accent: #7c3aed;       /* Purple */
  --text: #1f2937;
  --bg-light: #f8fafc;
  --bg-callout: #ede9fe;
}

/* Financial */
:root {
  --primary: #1e40af;      /* Navy */
  --accent: #059669;       /* Emerald */
  --text: #111827;
  --bg-light: #f0f9ff;
  --bg-callout: #dbeafe;
}

/* Healthcare / Medical */
:root {
  --primary: #0d9488;      /* Teal */
  --accent: #0ea5e9;       /* Sky Blue */
  --text: #1f2937;
  --bg-light: #f0fdfa;
  --bg-callout: #ccfbf1;
}

/* Executive / Premium */
:root {
  --primary: #1f2937;      /* Charcoal */
  --accent: #991b1b;       /* Burgundy */
  --text: #111827;
  --bg-light: #fef3c7;     /* Cream */
  --bg-callout: #fef3c7;
}
```

---

## SVG Diagram Slides

Architecture diagrams, data flows, and system overviews often start as SVGs
(built with `svg-press` skill). The PPTX renderer works with **raster images** —
SVGs must be converted to PNG before embedding.

### Workflow

1. **Build the SVG** using `svg-press` skill
2. **Render to high-res PNG** using `write_png`:
   ```
   write_png(path="diagram-arch.png", content=svg_content, format="html",
             width=2400, device_scale_factor=3, fit="content",
             content_selector="svg")
   ```
3. **Embed the PNG** in a slide:
   ```html
   <section id="slide-arch">
     <h1>System Architecture</h1>
     <p class="subtitle">Three-layer processing boundary</p>
     <img src="diagram-arch.png" style="width:9in; height:5.5in;">
   </section>
   ```

### Sizing Guidance

| Use case | Recommended size |
|----------|-----------------|
| Full-slide diagram (no text) | `width:9in; height:5.5in` |
| Diagram with text above/below | `width:8in; height:4in` |
| Diagram in two-column (one side) | `width:4in; height:3in` |
| Small inline diagram | `width:3in; height:2in` |

### Tips

- Render SVGs at `device_scale_factor=3` for crisp output on high-DPI screens
- Use `width=2400` or higher viewport for complex diagrams
- If the diagram has fine text, increase `mermaid_font_size_px` or `mermaid_scale`
- Always visually inspect the PNG before embedding — verify labels are readable

---

## Citations

### Inline Citation Format

```html
<p>
  Global EV sales grew ~35% YoY in 2024
  <sup class="cite" data-sids="1,3">[[S:1,3]]</sup>.
</p>
```

**Rules:**
- Place immediately after the factual claim
- `data-sids` contains numeric source IDs (comma-separated or range like `2-4`)
- Inner text `[[S:...]]` must mirror `data-sids`
- Renders as concise `[1] · [3]` with hyperlinks in the PPTX

### Alternative: Footnotes Block

```html
<div class="footnotes">
  <p>Sources: [[S:1]], [[S:3]], [[S:5]]</p>
</div>
```

### Sources Slide

When sources are provided and `include_sources_slide=True`, a final "Sources" slide
is auto-generated showing `[n] Title (domain)` with clickable hyperlinks. Do NOT
create a sources slide manually — it duplicates.

### When to Use Citations vs Reference Links

| Use | Element |
|-----|---------|
| Citing specific facts inline | `<sup class="cite" data-sids="...">` |
| Listing authoritative sources, data sources, documentation | `<div class="reference-link">` |
| Supporting claims with evidence | `<sup class="cite">` |
| Building a bibliography/resource section | `<div class="reference-link">` |

---

## Deck Narrative Structure

A well-structured deck tells a story. Use these patterns to guide slide ordering:

### Executive Briefing (6–10 slides)

1. **Title slide** — deck title, subtitle, date
2. **Executive summary** — 3–4 key takeaways (callout or bullets)
3. **Context / background** — why this matters now
4. **Analysis slides** (2–4) — data, comparisons, diagrams
5. **Recommendations** — what to do next (callout for emphasis)
6. **Next steps / timeline** — concrete actions

### Technical Presentation (8–15 slides)

1. **Title slide**
2. **Problem statement** — what we're solving
3. **Architecture / approach** — SVG diagram slide
4. **Detail slides** (3–6) — components, data flow, each with table or diagram
5. **Comparison** — two-column: current vs proposed, or option A vs B
6. **Results / metrics** — table with key numbers
7. **Risks / mitigations** — two-column or callout-based
8. **Recommendation + timeline**

### Sales / Customer Deck (5–8 slides)

1. **Title slide** with customer name
2. **Understanding your challenge** — shows you listened
3. **Our approach** — architecture/diagram slide
4. **Key capabilities** (1–2 slides) — two-column feature comparisons
5. **Case study / proof points** — metrics + callout
6. **Next steps / proposal**

### General Tips

- **Vary layouts:** Don't repeat the same slide structure 5 times in a row. Alternate between single-column bullets, two-column comparisons, tables, and diagram slides.
- **One idea per slide:** If you're explaining two different things, split into two slides.
- **Callouts for emphasis:** Reserve callout boxes for the 1–2 most important points — not every slide.
- **Diagrams for complexity:** When text alone can't convey a system or process, use an SVG diagram slide.

---

## Complete Slide Templates

### Template 1: Title Slide

```html
<section id="slide-title">
  <h1>Q4 2025 Performance Review</h1>
  <p class="subtitle">Strategic Priorities and Market Position</p>
</section>
```

### Template 2: Executive Summary

```html
<section id="slide-summary">
  <h1>Executive Summary</h1>
  <p class="subtitle">Q4 2025 Performance Highlights</p>

  <h2>Key Achievements</h2>
  <ul>
    <li><strong>Revenue:</strong> $3.2M (+28% YoY)</li>
    <li><strong>Growth:</strong> 450 new customers</li>
    <li><strong>Efficiency:</strong> 35% cost reduction</li>
  </ul>

  <div class="highlight-box">
    <h3>Strategic Priority</h3>
    <p>Expand into EMEA market Q1 2026 with localized offerings.</p>
  </div>
</section>
```

### Template 3: Two-Column Comparison

```html
<section id="slide-compare">
  <h1>Market Analysis</h1>

  <div class="two-column">
    <div class="column">
      <h3>Opportunities</h3>
      <ul>
        <li>Growing EV demand (+40%)</li>
        <li>Policy tailwinds in EU</li>
        <li>Tech partnerships</li>
      </ul>
    </div>
    <div class="column">
      <h3>Challenges</h3>
      <ul>
        <li>Supply chain volatility</li>
        <li>Intense competition</li>
        <li>Regulatory uncertainty</li>
      </ul>
    </div>
  </div>
</section>
```

### Template 4: Data Table

```html
<section id="slide-metrics">
  <h1>Quarterly Metrics</h1>

  <table>
    <thead>
      <tr><th>Metric</th><th>Q3 2025</th><th>Q4 2025</th><th>Change</th></tr>
    </thead>
    <tbody>
      <tr><td>Revenue</td><td>$2.5M</td><td>$3.2M</td><td>+28%</td></tr>
      <tr><td>Customers</td><td>1,200</td><td>1,650</td><td>+38%</td></tr>
      <tr><td>NPS</td><td>62</td><td>71</td><td>+9</td></tr>
    </tbody>
  </table>
</section>
```

### Template 5: Architecture Diagram (Full Slide)

```html
<section id="slide-arch">
  <h1>System Architecture</h1>
  <p class="subtitle">Three-layer processing boundary</p>
  <img src="diagram-arch.png" style="width:9in; height:5.5in;">
</section>
```

### Template 6: Diagram + Explanation (Split)

```html
<section id="slide-arch-detail">
  <h1>Data Pipeline</h1>

  <div class="two-column">
    <div class="column">
      <img src="pipeline-diagram.png" style="width:4in; height:3in;">
    </div>
    <div class="column">
      <h3>Key Components</h3>
      <ul>
        <li><strong>Ingestion:</strong> Kafka streams</li>
        <li><strong>Processing:</strong> Flink transforms</li>
        <li><strong>Storage:</strong> S3 + Iceberg</li>
      </ul>
    </div>
  </div>
</section>
```

### Template 7: Citation-Heavy Slide

```html
<section id="slide-trends">
  <h1>Market Trends</h1>

  <h2>Electric Vehicle Adoption</h2>
  <p>
    Global EV sales reached 14M units in 2024, representing ~18% of total
    auto sales <sup class="cite" data-sids="1">[[S:1]]</sup>. Growth is
    accelerated by policy incentives and falling battery costs
    <sup class="cite" data-sids="2,3">[[S:2,3]]</sup>.
  </p>
</section>
```

### Template 8: Reference Links in Columns

```html
<section id="slide-sources">
  <h1>Research Methodology</h1>
  <p class="subtitle">Data sources and analysis framework</p>

  <div class="two-column">
    <div class="column">
      <h3>Primary Data Sources</h3>
      <div class="reference-link">
        <strong>World Bank Open Data</strong>
        <a href="https://data.worldbank.org">Economic Indicators</a>
      </div>
      <div class="reference-link">
        <strong>OECD Statistics</strong>
        <a href="https://stats.oecd.org">International Comparisons</a>
      </div>
    </div>
    <div class="column">
      <h3>Analysis Timeline</h3>
      <ul>
        <li><strong>Weeks 1–3:</strong> Data collection</li>
        <li><strong>Weeks 4–6:</strong> Processing & models</li>
        <li><strong>Weeks 7–9:</strong> Peer review</li>
      </ul>
    </div>
  </div>
</section>
```

### Template 9: Multi-Callout Process

```html
<section id="slide-process">
  <h1>Implementation Phases</h1>

  <div class="two-column">
    <div class="column">
      <div class="phase-box" style="background: #dbeafe; border-left: 4px solid #1e40af;">
        <h3>Phase 1: Discovery</h3>
        <p>Requirements gathering, stakeholder interviews (Weeks 1–3)</p>
      </div>
    </div>
    <div class="column">
      <div class="phase-box" style="background: #d1fae5; border-left: 4px solid #059669;">
        <h3>Phase 2: Build</h3>
        <p>Core development, integration testing (Weeks 4–8)</p>
      </div>
    </div>
  </div>
</section>
```

---

## Visual Hierarchy

```css
/* Recommended type scale */
h1 { font-size: 36pt; color: var(--primary); }     /* slide title */
h2 { font-size: 28pt; color: var(--primary); }     /* section heading */
h3 { font-size: 22pt; color: var(--text); }         /* subsection */
p  { font-size: 18pt; line-height: 1.3; }           /* body text */
li { font-size: 18pt; }                              /* list items */
```

Do not deviate dramatically from this scale. Smaller than 14pt becomes hard to
read in presentations.

---

## Common Mistakes to Avoid

### 1. Missing `<section>` Tags (CRITICAL)

Content outside `<section>` is silently dropped. This is the #1 cause of
"empty deck" bugs.

### 2. Content Overload

12 bullets on one slide triggers 70% scaling → unreadable. Split into 2–3 slides
with 4–6 bullets each.

### 3. Overstuffed Two-Column

Max 3–4 bullets per column, not 8. Keep columns balanced in height.

### 4. Unsupported CSS

`box-shadow`, `border-radius`, gradients, flex, grid — all silently ignored.
Use only the documented subset.

### 5. Base64 Images

`<img src="data:image/png;base64,...">` is rejected. Always use file paths.

### 6. HTTP/HTTPS Images

Remote URLs are not supported. Download images first, then reference by path.

### 7. Long Titles

`<h1>Comprehensive Analysis of Q4 2025 Financial Performance and Strategic Market Positioning</h1>`
wraps to 3 lines. Keep to 8 words max. Use subtitle for details.

### 8. Manual Sources Slide

The renderer auto-generates a Sources slide when `include_sources_slide=True`.
Don't create one manually — you'll get duplicates.

### 9. SVG Directly in Slides

The PPTX renderer only supports raster images (PNG, JPG). Render SVGs to PNG
first with `write_png`, then embed the PNG.

### 10. Named/RGB Colors

`color: red;` and `color: rgb(255,0,0);` don't work. Use hex: `color: #ff0000;`.

---

## Render-Review Workflow

After generating the deck:

1. **Render:** `write_pptx(path="deck.pptx", content=html_content)`
2. **Check slide count:** Does it match expected number of sections?
3. **Inspect visually** (if possible): Look for scaling, missing content, blank slides
4. **Common fixes:**
   - Blank slide → content was outside `<section>` or section was empty
   - Tiny text → too much content; split the slide
   - Missing image → wrong path; verify file exists at OUT_DIR/path
   - No colors → CSS variable not defined or unsupported color syntax
   - Missing diagram → SVG was embedded directly; render to PNG first

---

## Performance Tips

1. **Keep HTML compact** — remove unnecessary whitespace and comments
2. **Minimize CSS** — define colors as `:root` variables, reuse classes
3. **Don't over-split** — a 3-bullet slide is thin; combine related content
4. **Err on less content** — if unsure whether it fits, use fewer bullets/rows
5. **One `<style>` block** — put all CSS in one `<style>` in `<head>`, not inline everywhere

---

## Remember

- **CRITICAL:** Wrap ALL content in `<section>` tags — one section per slide
- Content auto-scales down to 70% minimum — budget to avoid this
- **Hex colors only** — no named colors, no rgb()
- Only documented CSS properties work — everything else is silently ignored
- Callout classes: `highlight-box`, `callout`, `warning`, `success`, `phase-box`, `comparison-item`
- Paragraph-like classes: `reference-link`, `note`, `description`
- For SVG diagrams: render to PNG first via `write_png`, then embed PNG
- Citations: `<sup class="cite" data-sids="1">[[S:1]]</sup>` inline
- Sources slide is auto-generated — don't create manually
- Two-column: style each column individually; gap via `.two-column { gap: 0.3in; }`
- Vary slide layouts throughout the deck for visual interest
