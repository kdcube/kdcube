---
name: pdf-press
description: |
  Teaches agents how to generate Markdown, HTML (with embedded SVG), and Mermaid
  content that renders beautifully to multi-page PDF via write_pdf, with proper
  page breaks, compact professional layouts, brand and domain-adaptive color
  schemes, multi-column support for scientific papers, magazine-style editorial
  documents, and iterative render-review-fix workflow.
version: 3.0.0
category: document-creation
tags:
  - pdf
  - html
  - markdown
  - mermaid
  - svg
  - css
  - layout
  - typography
  - print-design
  - multi-column
  - magazine
when_to_use:
  - Creating multi-page professional documents (reports, proposals, whitepapers)
  - Building scientific papers with two-column layouts
  - Designing magazine-style or editorial documents with varied column counts
  - Rendering HTML with embedded SVG diagrams to PDF
  - Ensuring content doesn't split awkwardly across page boundaries
  - Generating PDFs with domain-adaptive or brand color schemes
  - Working with write_pdf tool in any format (markdown, html, mermaid)
  - Creating brochures, one-pagers, or marketing collateral with rich visuals
author: kdcube
created: 2026-04-14
namespace: public
import:
  - internal.link-evidence
  - internal.sources-section
  - public.svg-press
---

# PDF Press — Professional Document Authoring

## Overview

This skill teaches an agent how to generate content that renders to polished
multi-page PDF using `write_pdf` (Playwright + headless Chromium). It covers
all three supported input formats, professional layout patterns, page-break
control, compact spacing, brand and domain-adaptive color schemes,
multi-column layouts for scientific papers, magazine-style editorial documents,
and SVG diagram embedding.

## Tools

| Tool | Use |
|------|-----|
| `write_pdf(path, content, format, title, landscape)` | Render to PDF. `format` is `'markdown'` (default), `'html'`, or `'mermaid'`. |
| `write_png(path, content, format)` | Render pages or SVGs to PNG for visual inspection. |

## Choosing a Format

Pick the right format for the job:

| Format | When to use | Trade-offs |
|--------|-------------|------------|
| **markdown** | Reports, memos, documentation, any text-heavy document where layout is standard single-column. Supports GFM tables, lists, code blocks, images, math. | Fastest to write. Professional CSS applied automatically. No custom multi-column or SVG layout control. |
| **html** | Multi-column papers, magazine layouts, brochures, documents with embedded SVG diagrams, custom typography, branded collateral, anything needing precise visual control. | Full CSS power. You control every pixel. Must handle `@page`, break rules, spacing yourself. |
| **mermaid** | Standalone diagrams (flowcharts, sequence, ER, Gantt, etc.) rendered as a full-page PDF. | Single-purpose — one diagram per PDF. For diagrams inside a document, embed as SVG in HTML mode instead. |

### Format: Markdown

Use `write_pdf(path, content, format='markdown')` — this is the **default**.

The renderer applies a clean professional stylesheet automatically (Arial,
10pt body, proper heading hierarchy, table borders, code blocks). You write
standard GitHub-Flavored Markdown.

**Best practices for Markdown mode:**

- Write standard GFM: `#` headings, `**bold**`, tables, lists, code fences
- Use `![alt](relative/path.png)` for images (relative to OUT_DIR)
- Do NOT embed base64 data URIs — use file paths
- Citations: `[[S:1,3]]` tokens are resolved automatically when sources exist
- Set `include_sources_section=True` (default) to append a references section
- Set `landscape=True` for wide tables or landscape documents
- The auto-applied CSS handles page margins (25mm top, 20mm sides, 30mm bottom),
  header sizes, table styling, and code formatting
- You do NOT need `@page` rules or break-inside — the stylesheet handles it
- For documents where you need multi-column, SVG, or custom layout → switch to HTML

**Markdown example:**
```markdown
# Quarterly Performance Report

**Author:** Analytics Team · **Date:** 2026-04-14 · **Classification:** Internal

## Executive Summary

Revenue grew 18% year-over-year driven by enterprise expansion...

## Key Metrics

| Metric | Q1 2026 | Q4 2025 | Change |
|--------|---------|---------|--------|
| ARR    | $3.2M   | $2.7M   | +18.5% |
| NRR    | 115%    | 112%    | +3pp   |

## Analysis

### Growth Drivers

Enterprise segment contributed 72% of net new ARR...

![Revenue Trend](turn_42/files/revenue-chart.png)

*Figure 1: Monthly recurring revenue trend over the past 6 quarters.*

## Recommendations

1. Expand enterprise sales team by 3 AEs
2. Launch mid-market pricing tier in Q2
3. Invest in self-serve onboarding to reduce CAC
```

### Format: HTML

Use `write_pdf(path, content, format='html')` for full layout control.

You provide a complete HTML document (or fragment — it will be wrapped). The
renderer executes JavaScript, so Chart.js, D3, etc. work. You must handle all
CSS including `@page`, typography, spacing, and break rules.

See the rest of this skill for comprehensive HTML authoring guidance.

### Format: Mermaid

Use `write_pdf(path, content, format='mermaid')` for standalone diagrams.

Pass raw Mermaid text (no ``` fences). The renderer wraps it in an HTML page
with Mermaid.js and renders to PDF.

```
graph TD
    A[Data Ingestion] --> B[Processing]
    B --> C[Storage]
    C --> D[Analytics]
```

For diagrams embedded within a larger document, create the SVG separately and
reference it in HTML mode via `<img src="diagram.svg">`.

---

## Core Principles (HTML Mode)

### 1. Page-Aware Layout

Always consider the printable page height when structuring content:

- A4 portrait: ~257mm usable height (with 20mm margins)
- A4 landscape: ~177mm usable height
- Wrap logical units in `break-inside: avoid` containers
- Keep individual unbreakable sections under 220mm (portrait) / 150mm (landscape)
- Split large content into multiple breakable sections

### 2. Content Grouping > Automatic Page Breaks

Do NOT put `page-break-after: always` on every section class. Instead:

- Use a plain `.page` class with no automatic breaks
- Insert explicit `<div class="page-break"></div>` only where you want a forced break
- A diagram and its accompanying table/explanation MUST be in the same section — never separated by a page break
- After composing, estimate content height per page (~250mm usable on A4). If a section is ~110% of a page, the overflow creates a near-empty next page — restructure by merging or tightening

```css
.page { max-width: 700px; margin: 0 auto; padding: 0 10px; }
.page-break { page-break-after: always; }
```

### 3. Compact Professional Spacing

Budget vertical space — 80% of PDF failures are spacing-related.

| Element | Compact (target) | Wasteful (avoid) |
|---------|-------------------|-------------------|
| Title area top padding | 8–15px | 40px+ |
| Card/callout padding | 8–14px | 20–40px |
| Card/callout margins | 7–12px | 16px+ |
| Section margins | 12–16px | 24–30px |
| Table cell padding | 4–7px | 8–10px |
| Body font | 10–10.5pt, line-height 1.5 | 12pt+, line-height 1.8 |
| Table font | 8.5–9.5pt, line-height 1.3 | 11pt+ |
| Paragraph margins | 6px | 12px+ |

**Vertical space budget for A4 portrait (257mm printable):**
- Compact header: 30–40mm
- Executive summary: 60–80mm
- Remaining for content: 137–167mm
- Each section: ~40–80mm (fits 2–4 per page)
- Table row: 5–6mm (max 12–15 rows before splitting)

### 4. Semantic Break Points

Let the browser break naturally between sections, not within them:

- Each section = one cohesive idea
- Always wrap `<h2>` + content in the same `break-inside: avoid` container
- This prevents heading splits like "Simulation &" / "Optimization" across pages

```html
<!-- BAD: Heading can split from content -->
<h2>Scenario 3: AI-Driven Simulation & Optimization</h2>
<p>Description...</p>

<!-- GOOD: Heading and content stay together -->
<section style="break-inside: avoid;">
  <h2>Scenario 3: AI-Driven Simulation & Optimization</h2>
  <p>Description...</p>
</section>
```

### 5. SVG Diagram Embedding

Reference SVGs as `<img>` tags. SVGs are self-contained (no external deps, no JS). They scale to the container width; the SVG viewBox aspect ratio determines rendered height.

```html
<div class="diagram">
  <img src="diagram-1-layers.svg" alt="Architecture layers">
</div>
```

```css
.diagram { width: 100%; margin: 12px 0 16px; }
.diagram img { width: 100%; height: auto; }
```

Build SVGs using the `svg-press` skill. Keep diagram + its explanation table in the same section — never separate them with a page break.

### 6. Images — Use File Paths, NEVER Base64

- HTML mode: `<img src="turn_id/files/chart.png" alt="Chart">`
- Paths are relative to OUT_DIR (the tool's output directory)
- Base64 data URIs crash headless Chromium on multi-page PDFs
- Wrap images in figures with `break-inside: avoid`:

```html
<figure style="break-inside: avoid; margin: 12px 0; text-align: center;">
  <img src="files/chart.png" alt="Revenue Chart"
       style="max-width: 100%; height: auto; display: block; margin: 0 auto;">
  <figcaption style="font-size: 8.5pt; color: var(--text-muted); margin-top: 4px;">
    Figure 1: Quarterly revenue trends
  </figcaption>
</figure>
```

---

## Color Schemes

### Option A: Brand Color Integration (KDCube)

When building documents for KDCube or when brand tokens are provided, use these:

```css
:root {
  /* Backgrounds */
  --bg:        #F6FAFA;      /* page background — barely-there mint */
  --surface:   #FFFFFF;      /* card / panel */
  --surface-2: #EEF8F7;      /* alternate card — light teal wash */
  --surface-3: #FDF9EE;      /* alternate card — light gold wash */

  /* Brand teal */
  --teal:      #01BEB2;
  --teal-dark: #009C92;
  --teal-pale: #C6F3F1;

  /* Brand blue */
  --blue:      #4372C3;
  --blue-dark: #2B4B8A;
  --blue-pale: #DDEAFE;

  /* Brand purple */
  --purple:    #6B63FE;
  --purple-pale: #EBEBFF;

  /* Sunflower gold */
  --gold:      #F0BC2E;
  --gold-dark: #C89A10;
  --gold-pale: #FFF8DC;

  /* Meadow green */
  --green:     #52B044;
  --green-dark: #3A8030;
  --green-pale: #E8F7E5;

  /* Sky cerulean */
  --sky:       #38B8C8;
  --sky-pale:  #E0F5F8;

  /* Text */
  --text:      #0D1E2C;
  --text-2:    #3A5672;
  --text-muted: #7A99B0;

  /* Borders */
  --border:    #D8ECEB;
}
```

**Semantic assignments for brand mode:**

| Role | Token | Hex |
|------|-------|-----|
| Headings (h1, h2) | `--blue-dark` | #2B4B8A |
| Subtitle / h2 underline | `--teal-dark` / `--teal-pale` | #009C92 / #C6F3F1 |
| Table header background | `--blue-pale` | #DDEAFE |
| Table header text | `--blue-dark` | #2B4B8A |
| Table alternate row | `--surface-2` | #EEF8F7 |
| Body text | `--text-2` | #3A5672 |
| Muted / captions | `--text-muted` | #7A99B0 |
| Callout: positive | `--green-pale` bg + `--green` border |
| Callout: info | `--blue-pale` bg + `--blue` border |
| Callout: caution | `--gold-pale` bg + `--gold` border |
| Accent bar gradient | `--teal-pale` → `--teal` → `--blue` |
| Badge: primary | `--teal-pale` bg + `--teal-dark` text |
| Badge: secondary | `--blue-pale` bg + `--blue-dark` text |

### Option B: Domain-Adaptive Color Schemes

When no brand colors are specified, choose a palette matching the document domain:

```css
/* Tech / Engineering */
:root { --primary: #1e3a8a; --accent: #3b82f6; --text: #1f2937; --bg-alt: #f8fafc; --border: #cbd5e1; }

/* Business / Finance */
:root { --primary: #1e40af; --accent: #059669; --text: #1f2937; --bg-alt: #f0f9ff; --border: #bae6fd; }

/* Medical / Health */
:root { --primary: #0d9488; --accent: #0ea5e9; --text: #1f2937; --bg-alt: #f0fdfa; --border: #99f6e4; }

/* Creative / Design */
:root { --primary: #7c3aed; --accent: #f97316; --text: #1f2937; --bg-alt: #faf5ff; --border: #e9d5ff; }

/* Legal / Formal */
:root { --primary: #1f2937; --accent: #991b1b; --text: #1f2937; --bg-alt: #fef3c7; --border: #d1d5db; }

/* Academic / Research */
:root { --primary: #1e3a8a; --accent: #f59e0b; --text: #1f2937; --bg-alt: #f8f9fa; --border: #e5e7eb; }
```

---

## Document Structure Templates (HTML Mode)

### Template A: Single-Column Technical Report

The most common pattern. Compact header, card-based highlights, breakable sections.

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@page { size: A4 portrait; margin: 20mm; }

:root {
  --primary: #1e3a8a;
  --text: #1f2937;
  --bg-alt: #f8f9fa;
  --border: #e5e7eb;
  --text-muted: #6b7280;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 10pt;
  line-height: 1.5;
  color: var(--text);
}

h1 {
  font-size: 18pt;
  color: var(--primary);
  margin: 0 0 6px;
  padding-bottom: 6px;
  border-bottom: 2px solid var(--primary);
}
h2 { font-size: 14pt; color: var(--primary); margin: 16px 0 8px; }
h3 { font-size: 11pt; font-weight: 600; margin: 12px 0 6px; }
p { margin: 6px 0; }

.metadata { font-size: 8pt; color: var(--text-muted); margin: 4px 0 16px; }

section {
  break-inside: avoid;
  page-break-inside: avoid;
  margin-bottom: 16px;
}

.card {
  background: var(--bg-alt);
  border-left: 3px solid var(--primary);
  padding: 10px 14px;
  margin: 12px 0;
  break-inside: avoid;
}

table { width: 100%; border-collapse: collapse; font-size: 8.5pt; line-height: 1.3; margin: 8px 0; }
thead { background: var(--primary); color: white; }
th, td { padding: 4px 6px; border: 1px solid var(--border); text-align: left; vertical-align: top; }

.table-wrapper { break-inside: avoid; margin: 12px 0; }
</style>
</head>
<body>
  <h1>Document Title</h1>
  <p class="metadata">Author · 2026-04-14 · Report Type</p>

  <div class="card" style="break-inside: avoid;">
    <strong>Executive Summary:</strong> Key findings described here...
  </div>

  <section>
    <h2>Analysis</h2>
    <p>Content that stays together with its heading...</p>
    <div class="table-wrapper">
      <h3>Data Summary</h3>
      <table>
        <thead><tr><th>Metric</th><th>Value</th><th>Change</th></tr></thead>
        <tbody>
          <tr><td>Revenue</td><td>$3.2M</td><td>+18%</td></tr>
          <tr><td>Customers</td><td>142</td><td>+23</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</body>
</html>
```

### Template B: Branded Document with SVG Diagrams

Uses brand color tokens and embedded SVG diagrams. Based on KDCube palette.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
<style>
@page {
  size: A4;
  margin: 20mm 18mm 24mm 18mm;
  @bottom-center { content: "Confidential"; font-size: 8pt; color: #7A99B0; }
  @bottom-right { content: counter(page); font-size: 8pt; color: #7A99B0; }
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #F6FAFA; --surface: #FFFFFF; --surface-2: #EEF8F7; --surface-3: #FDF9EE;
  --teal: #01BEB2; --teal-dark: #009C92; --teal-pale: #C6F3F1;
  --blue: #4372C3; --blue-dark: #2B4B8A; --blue-pale: #DDEAFE;
  --purple: #6B63FE; --purple-pale: #EBEBFF;
  --gold: #F0BC2E; --gold-dark: #C89A10; --gold-pale: #FFF8DC;
  --green: #52B044; --green-dark: #3A8030; --green-pale: #E8F7E5;
  --sky: #38B8C8; --sky-pale: #E0F5F8;
  --text: #0D1E2C; --text-2: #3A5672; --text-muted: #7A99B0;
  --border: #D8ECEB;
}

body {
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 10.5pt;
  line-height: 1.55;
  color: var(--text-2);
}

h1 { font-size: 18pt; font-weight: 800; color: var(--blue-dark); margin: 0 0 4px; }
h2 {
  font-size: 13pt; font-weight: 700; color: var(--blue-dark);
  margin: 16px 0 8px;
  padding-bottom: 3px;
  border-bottom: 2px solid var(--teal-pale);
}
h3 { font-size: 11pt; font-weight: 600; color: var(--text); margin: 10px 0 5px; }
p { margin: 5px 0; }

.subtitle { color: var(--teal-dark); font-size: 10pt; margin: 2px 0 6px; }
.metadata { font-size: 8pt; color: var(--text-muted); margin: 2px 0 12px; }
.accent-bar {
  height: 5px; border-radius: 10px; margin: 6px 0 14px;
  background: linear-gradient(90deg, var(--teal-pale), var(--teal), var(--blue));
}

.page { max-width: 700px; margin: 0 auto; padding: 0 10px; }
.page-break { page-break-after: always; }

section { break-inside: avoid; page-break-inside: avoid; margin-bottom: 14px; }

.diagram { width: 100%; margin: 12px 0 16px; }
.diagram img { width: 100%; height: auto; }

.callout { border-radius: 8px; padding: 8px 12px; margin: 7px 0; font-size: 10pt; break-inside: avoid; }
.callout-green { background: var(--green-pale); border-left: 4px solid var(--green); }
.callout-blue  { background: var(--blue-pale); border-left: 4px solid var(--blue); }
.callout-gold  { background: var(--gold-pale); border-left: 4px solid var(--gold); }

table { width: 100%; border-collapse: collapse; font-size: 9.5pt; }
th { background: var(--blue-pale); color: var(--blue-dark); font-weight: 700; padding: 7px 10px; border-bottom: 2px solid var(--blue); }
td { padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:nth-child(even) td { background: var(--surface-2); }
.table-wrapper { break-inside: avoid; margin: 10px 0; }

.badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 8.5pt; font-weight: 700; }
.badge-primary { background: var(--teal-pale); color: var(--teal-dark); }
.badge-secondary { background: var(--blue-pale); color: var(--blue-dark); }
</style>
</head>
<body>

<!-- Title page -->
<div class="page">
  <div style="padding-top: 8px;"></div>
  <h1>Architecture Overview</h1>
  <p class="subtitle">Platform Integration Blueprint</p>
  <div class="accent-bar"></div>
  <p class="metadata">Prepared for Acme Corp · April 2026</p>

  <div class="callout callout-blue">
    <strong>Key Principle:</strong> All data remains customer-owned...
  </div>

  <div class="table-wrapper">
    <h3>Parties</h3>
    <table>
      <thead><tr><th>Entity</th><th>Role</th><th>Ownership</th></tr></thead>
      <tbody>
        <tr><td>KDCube</td><td>Platform</td><td><span class="badge badge-primary">Platform</span></td></tr>
        <tr><td>Customer</td><td>Tenant</td><td><span class="badge badge-secondary">Data Owner</span></td></tr>
      </tbody>
    </table>
  </div>
<div class="page-break"></div>
</div>

<!-- Diagram page — diagram + table stay together -->
<div class="page">
  <section>
    <h2>System Architecture</h2>
    <p>The platform consists of three processing layers...</p>
    <div class="diagram"><img src="diagram-1-layers.svg" alt="Architecture layers"></div>
    <div class="table-wrapper">
      <h3>Layer Summary</h3>
      <table>
        <thead><tr><th>Layer</th><th>Technology</th><th>Purpose</th></tr></thead>
        <tbody>
          <tr><td>Ingestion</td><td>Kafka</td><td>Event streaming</td></tr>
          <tr><td>Processing</td><td>Flink</td><td>Real-time transforms</td></tr>
          <tr><td>Storage</td><td>S3 + Iceberg</td><td>Durable lake</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</div>

</body>
</html>
```

---

## Multi-Column Layouts (HTML Mode)

### Pattern: Two-Column Scientific Paper

Full-width header and abstract, two-column body. Figures and section headings
span both columns.

```html
<style>
@page { size: A4 portrait; margin: 20mm; }

.paper-header { max-width: 700px; margin: 0 auto 16px; text-align: center; }
.abstract {
  max-width: 600px;
  margin: 0 auto 16px;
  font-size: 9.5pt;
  border: 1px solid var(--border);
  padding: 10px 14px;
  background: var(--bg-alt, #f8f9fa);
}
.two-column-body {
  column-count: 2;
  column-gap: 20px;
  max-width: 700px;
  margin: 0 auto;
  text-align: justify;
}
.two-column-body h2 {
  column-span: all;
  font-size: 12pt;
  margin: 14px 0 8px;
}
.two-column-body figure {
  column-span: all;
  break-inside: avoid;
  margin: 12px 0;
}
.two-column-body .in-column {
  break-inside: avoid;
  margin-bottom: 8px;
}
</style>

<div class="paper-header">
  <h1 style="font-size: 16pt;">Paper Title: Advances in Neural Architecture</h1>
  <p class="metadata">J. Smith, A. Chen · MIT · 2026</p>
</div>

<div class="abstract">
  <strong>Abstract.</strong> We present a novel approach to transformer
  efficiency that reduces compute by 40% while maintaining accuracy...
</div>

<div class="two-column-body">
  <h2>1. Introduction</h2>
  <p>Text flows naturally in two columns. Each column is approximately
  330px wide. Keep paragraphs moderate length for balanced flow.</p>

  <figure>
    <img src="fig1.svg" style="max-width: 100%; height: auto;">
    <figcaption style="font-size: 8.5pt; color: var(--text-muted, #6b7280); margin-top: 4px;">
      Figure 1: Model architecture comparison
    </figcaption>
  </figure>

  <h2>2. Methods</h2>
  <p>Content continues in two columns after the full-width figure...</p>

  <h2>3. Results</h2>
  <div class="in-column">
    <p>Inline results that should not split across columns...</p>
  </div>
</div>
```

**Two-column constraints:**
- Each column ~330px wide
- Max unbreakable element: 180mm tall
- Figures span both columns via `column-span: all`
- Use `break-inside: avoid` on all figures, tables, and tight content blocks
- Section headings span both columns

### Pattern: Three-Column Magazine / Editorial

Landscape orientation. Suited for editorial, magazine-style, and marketing documents.
Use pull-quotes, sidebars, and hero images for visual variety.

```html
<style>
@page { size: A4 landscape; margin: 18mm; }

body {
  font-family: 'Georgia', 'Times New Roman', serif;
  font-size: 9.5pt;
  line-height: 1.55;
  color: #1f2937;
}

.magazine-hero {
  text-align: center;
  margin-bottom: 16px;
  break-inside: avoid;
}
.magazine-hero h1 {
  font-size: 24pt;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin: 0 0 4px;
}
.magazine-hero .deck {
  font-size: 11pt;
  font-style: italic;
  color: #6b7280;
  max-width: 600px;
  margin: 0 auto;
}

.magazine-body {
  column-count: 3;
  column-gap: 18px;
  max-width: 100%;
  text-align: justify;
}
.magazine-body h2 {
  column-span: all;
  font-size: 14pt;
  margin: 14px 0 8px;
  border-bottom: 2px solid var(--primary, #1e3a8a);
  padding-bottom: 4px;
}
.magazine-body figure {
  column-span: all;
  break-inside: avoid;
  margin: 12px 0;
  text-align: center;
}

.pull-quote {
  break-inside: avoid;
  background: var(--gold-pale, #FFF8DC);
  border-left: 3px solid var(--gold, #F0BC2E);
  padding: 8px 12px;
  margin: 8px 0;
  font-size: 10pt;
  font-style: italic;
}

.sidebar {
  break-inside: avoid;
  background: var(--bg-alt, #f8f9fa);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
  padding: 8px 10px;
  margin: 8px 0;
  font-size: 8.5pt;
}
.sidebar h4 {
  font-size: 9pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 4px;
  color: var(--primary, #1e3a8a);
}

.drop-cap::first-letter {
  font-size: 3em;
  float: left;
  line-height: 0.8;
  padding-right: 6px;
  font-weight: 700;
  color: var(--primary, #1e3a8a);
}
</style>

<div class="magazine-hero">
  <h1>The Future of Sustainable Computing</h1>
  <p class="deck">How next-generation chip architectures are reshaping
  the relationship between performance and energy consumption</p>
</div>

<div class="magazine-body">
  <h2>The Efficiency Revolution</h2>
  <p class="drop-cap">Lorem ipsum dolor sit amet, consectetur adipiscing
  elit. Text flows across three columns in a natural magazine layout...</p>

  <div class="pull-quote">
    "We reduced power consumption by 60% without sacrificing throughput."
    — Dr. Elena Torres, Lead Architect
  </div>

  <p>Continued body text flows naturally around pull-quotes and sidebars...</p>

  <div class="sidebar">
    <h4>By the Numbers</h4>
    <p>40% reduction in die size. 60% less power. 2x throughput per watt.</p>
  </div>

  <figure>
    <img src="hero-chip.svg" style="max-width: 80%; height: auto;">
    <figcaption style="font-size: 8.5pt; color: #6b7280;">
      Figure 1: Next-gen chip architecture layout
    </figcaption>
  </figure>

  <h2>Manufacturing Challenges</h2>
  <p>The transition to 2nm process nodes brings new lithographic challenges...</p>
</div>
```

**Three-column constraints:**
- Landscape orientation only — portrait is too narrow for 3 columns
- Each column ~280px wide
- Max unbreakable element: 120mm tall
- Use pull-quotes and sidebars for visual rhythm
- Hero images and section headings span all columns via `column-span: all`
- Serif fonts (Georgia) work well for editorial feel; sans-serif for modern/tech

### Pattern: Two-Column Brochure / Marketing

Portrait orientation, two columns with hero banner and feature cards.

```html
<style>
@page { size: A4 portrait; margin: 18mm; }

.brochure-header {
  text-align: center;
  margin-bottom: 14px;
  break-inside: avoid;
}
.brochure-header h1 { font-size: 20pt; font-weight: 800; margin: 0 0 4px; }
.brochure-header .tagline { font-size: 10pt; color: var(--text-muted, #6b7280); }

.two-col {
  column-count: 2;
  column-gap: 20px;
}
.two-col h2 {
  column-span: all;
  font-size: 13pt;
  margin: 14px 0 8px;
}

.feature-card {
  break-inside: avoid;
  background: var(--bg-alt, #f8f9fa);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 8px 0;
}
.feature-card h4 {
  font-size: 10pt;
  font-weight: 700;
  margin: 0 0 4px;
}
.feature-card p { font-size: 9pt; margin: 0; }
</style>

<div class="brochure-header">
  <h1>Product Name</h1>
  <p class="tagline">Your data platform, simplified</p>
  <div class="accent-bar"></div>
</div>

<div class="two-col">
  <h2>Why Choose Us</h2>

  <div class="feature-card">
    <h4>Real-Time Analytics</h4>
    <p>Process millions of events per second with sub-second latency...</p>
  </div>

  <div class="feature-card">
    <h4>Enterprise Security</h4>
    <p>SOC 2 Type II certified with end-to-end encryption...</p>
  </div>

  <h2>Architecture</h2>
  <figure style="column-span: all; break-inside: avoid; margin: 10px 0; text-align: center;">
    <img src="arch-diagram.svg" style="max-width: 100%; height: auto;">
  </figure>
</div>
```

---

## Component Patterns

### Tables

```css
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; line-height: 1.3; margin: 8px 0; }
thead { background: var(--primary, #1e3a8a); color: white; }
th, td { padding: 4px 6px; border: 1px solid var(--border, #e5e7eb); text-align: left; vertical-align: top; }
.table-wrapper { break-inside: avoid; margin: 12px 0; }
```

**Maximum rows before splitting:**
- 8.5pt font + 4px padding + 1.3 line-height ≈ 5–6mm per row
- Portrait safe zone ≈ 220mm → max ~12–15 rows
- Landscape safe zone ≈ 150mm → max ~8–10 rows
- For tables >12 rows: add subheadings to create semantic breaks, or split into multiple tables each wrapped in `break-inside: avoid`

### Callout Boxes

```html
<div class="callout callout-green">
  <strong>Key point:</strong> Explanation text.
</div>
```

```css
.callout { border-radius: 8px; padding: 8px 12px; margin: 7px 0; font-size: 10pt; break-inside: avoid; }
.callout-green { background: var(--green-pale, #E8F7E5); border-left: 4px solid var(--green, #52B044); }
.callout-blue  { background: var(--blue-pale, #DDEAFE); border-left: 4px solid var(--blue, #4372C3); }
.callout-gold  { background: var(--gold-pale, #FFF8DC); border-left: 4px solid var(--gold, #F0BC2E); }
```

### Badges

```html
<span class="badge badge-primary">Platform</span>
<span class="badge badge-secondary">OSS · MIT</span>
```

```css
.badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 8.5pt; font-weight: 700; }
```

---

## Height Budget Reference

```
A4 Portrait (257mm usable with 20mm margins):
  Compact header:     30–40mm
  Executive summary:  60–80mm
  Diagram (SVG):      60–80mm (depends on viewBox aspect ratio)
  Table (8 rows):     50–60mm
  Section text:       30–50mm
  Callout:            20–30mm
  Rule: diagram + table ≈ 120–140mm → fits with text on same page
  Rule: if diagram + table > 200mm → give them their own page

A4 Landscape (177mm usable):
  Section:       30–60mm
  Table row:     5–6mm (max 8–10 rows)
  Best for:      3-column magazine, wide tables, panoramic diagrams
```

---

## Essential CSS Quick Reference

```css
/* Page setup */
@page { size: A4 portrait; margin: 20mm; }
@page { size: A4 landscape; margin: 18mm; }

/* Break control */
break-inside: avoid;              /* Keep element on one page */
page-break-inside: avoid;         /* Legacy — use both for safety */
page-break-before: always;        /* Force new page before */
page-break-after: always;         /* Force new page after */

/* Column layouts */
column-count: 2;                  /* Two-column flow */
column-count: 3;                  /* Three-column (landscape only) */
column-gap: 18px;                 /* Space between columns */
column-span: all;                 /* Break out of columns (headings, figures) */

/* Typography standards */
body { font-size: 10pt; line-height: 1.5; }       /* compact professional */
body { font-size: 10.5pt; line-height: 1.55; }    /* branded / Inter font */
table { font-size: 8.5pt; line-height: 1.3; }     /* compact tables */
h1 { font-size: 18pt; }  /* max for reports; up to 24pt for magazine covers */
h2 { font-size: 13–14pt; }
h3 { font-size: 11pt; }
```

---

## Wording and Tone Guidance

- Do not advertise. Support choices by explaining what they enable.
- Every entity must be explained on first use: "processor" alone is unclear — write "processor (generic workers that dynamically load and run customer bundles)."
- Ownership statements must be scoped: "Acme owns the specific bundles written in its codebase" — not "Acme owns all bundles."
- If the same term names both a technology and data it produces, disambiguate: "Accounting" = platform service; "Accounting data" = customer-owned output.

---

## Render-Review-Fix Loop

After building HTML content, always render and inspect before delivering.

**Step 1 — Render PDF:**
```
write_pdf(path="document.pdf", content=html_content, format="html")
```

**Step 2 — Render key visuals to PNG for inspection:**
```
write_png(path="check-diagram.png", content=svg_content, format="html",
          width=1600, fit="content", content_selector="svg")
```

**Step 3 — Read the PDF and PNGs. Check for:**

| Issue | Symptom | Fix |
|-------|---------|-----|
| Blank page | Page with 0–2 lines | Remove preceding page-break or merge sections |
| Split content | Diagram on page N, its table on N+1 | Move both into same section |
| Overflow | Element extends past parent box | Increase parent height, verify viewBox |
| Leaked line | 1 line spills to next page | Reduce top padding, tighten margins, or merge |
| White inner boxes | Jarring contrast in SVG | Use light tint of parent color (never #FFFFFF) |
| Label behind arrow | Text hidden by line | Offset label 10+ px from the line |
| Ambiguous term | Same word for technology and data | Add "data" or "service" qualifier |

**Step 4 — Fix and re-render. Repeat until clean.**

Do not deliver until you have rendered and visually inspected at least once.

---

## Common Mistakes to Avoid

1. **Wrong format choice** — Using HTML when Markdown would suffice (wastes time); using Markdown when you need multi-column (impossible).

2. **Banner-style headers** — `padding: 40px; background: blue;` wastes 100mm. Use `border-bottom: 2px solid; padding-bottom: 6px;` (20mm).

3. **Not wrapping headings with content** — `<h2>Title</h2><p>Text</p>` splits across pages. Always wrap in `<section style="break-inside: avoid;">`.

4. **Excessive padding and margins** — Card `padding: 24px 40px; margin: 30px;` wastes 40mm. Use `padding: 10px 14px; margin: 12px 0;`.

5. **Tables without break-inside wrapper** — Bare `<table>` splits from its title. Always: `<div style="break-inside: avoid;"><h3>Title</h3><table>...</table></div>`.

6. **Body line-height >1.6** — Reduces content density. Use 1.5 (or 1.55 with Inter).

7. **Table line-height >1.4** — Tables become too tall. Use 1.3.

8. **Missing @page rule** — Always define `@page { size: A4 portrait; margin: 20mm; }` in HTML mode.

9. **Base64 images** — `<img src="data:image/png;base64,...">` crashes multi-page PDFs. Always use relative file paths.

10. **Separating diagram from its explanation** — Diagram and table/description must be in the same section. Never put a page break between them.

11. **3-column in portrait** — Portrait is too narrow. Use landscape for 3+ columns.

12. **Forgetting render-review-fix loop** — Always visually inspect the PDF before delivering.
