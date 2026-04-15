---
name: svg-press
description: |
  Teaches agents how to hand-craft self-contained SVG diagrams that render
  correctly in browsers, <img> tags, and Playwright PDF/PNG. Covers
  containment math, viewBox sizing, arrow construction, text hierarchy,
  KDCube brand color tokens, semantic zone fills, badges, the PNG
  inspection loop, write_png parameters, multi-diagram workflows,
  and common failure modes with fixes.
version: 2.0.0
category: document-creation
tags:
  - svg
  - diagrams
  - visualization
  - html
  - pdf
  - png
  - architecture
  - data-flow
when_to_use:
  - Creating architecture diagrams, component maps, and data flow diagrams
  - Embedding SVG diagrams into HTML that will later be rendered to PDF
  - When Mermaid is too restrictive for custom layout, nested boxes, badges, or mixed arrow styles
  - When you need exact control over spacing, fills, labels, and ownership markers
  - When diagrams must match KDCube brand colors or a specific color schema
  - Rendering SVGs to PNG for embedding in PPTX or DOCX
author: kdcube
created: 2026-04-14
updated: 2026-04-14
namespace: public
---

# SVG Press — Hand-Crafted Diagram Authoring

## Overview

This skill teaches how to build self-contained SVG diagrams that render the
same way in a browser, inside `<img>` tags, and inside HTML rendered to PDF or
PNG by Playwright. Use pure SVG with inline styles — no JavaScript, no external
CSS, no remote assets.

## Tools

| Tool | Use |
|------|-----|
| `write_png` | Render an SVG (wrapped in minimal HTML) to PNG for visual inspection or embedding in PPTX/DOCX |
| `write_pdf` | Render a parent HTML document (containing inline SVGs or `<img>` refs) to PDF |

## When to Use SVG vs. Mermaid

**Use hand-crafted SVG when you need:**
- Custom box fills (tinted backgrounds, not just white)
- Ownership badges or labels on containers
- Nested box layouts (box inside box inside box)
- Mixed arrow styles (solid + dashed, different colors)
- Precise control over text positioning and spacing
- Diagrams that must match KDCube brand colors
- Legends, perimeter borders, or zone-based layouts

**Use Mermaid when:** a simple flowchart, sequence diagram, or ER diagram with
default styling is sufficient and precise layout control is not required.

---

## Core Rules

### 1. Self-Contained SVG First

Every SVG must be a portable, standalone document:
- Explicit `viewBox` with computed dimensions
- Explicit `width`/`height` or let the container size it
- All colors and styles inline (no external CSS dependency)
- All markers, filters, and gradients in `<defs>`
- `font-family="Inter, system-ui, sans-serif"` on the root `<svg>`

### 2. Containment Math — Not Eyeballing

Every child element must fit inside its parent with margin. Before finalizing,
compute for each container:

```text
RULE: container_bottom >= last_child_bottom + 5
```

Where:
- `container_bottom = container_y + container_height`
- `last_child_bottom = last_child_y + last_child_height`

**Worked example (overflow detection):**

```text
Container:  y=375, height=70  → bottom = 445
Pill row 1: y=412, h=22      → bottom = 434  ✓
Pill row 2: y=438, h=22      → bottom = 460  ✗ OVERFLOW (460 > 445)
Fix: increase container height to at least 460 - 375 + 5 = 90
```

Common failure cases:
- Pill rows placed lower than expected
- Subtitles forgotten in height budgeting
- Badges overlapping header text
- Nested boxes sized from title only, not from full content

**Always compute. Never eyeball.**

### 3. Over-Allocate Height

SVG diagrams fail more often from clipping than from extra whitespace.

- `viewBox` height = `last_element_bottom + 10..15`
- Background `<rect>` dimensions must match the `viewBox` exactly
- Shrinking later is cheap; debugging clipped arrows and labels is not

### 4. Separate Semantic Layers Visually

Give different zones clearly different treatment:
- Outer containers: stronger border and tinted background
- Inner boxes: lighter tint of the same family — **never `#FFFFFF`**
- Directional or external flows: distinct stroke color or dashed style
- Ownership or status: compact badge/pill in the header bar

### 5. Text Needs Hierarchy

A diagram is not just boxes. It needs readable narrative structure. Every named
box **must** have a subtitle explaining what it does. A bare label like
"Marketing Copilot" is not enough — add "AI chatbot for customer engagement".

### 6. Accessibility Basics

Add `<title>` and `<desc>` inside the root `<svg>` for screen readers:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 560"
     role="img" aria-labelledby="svg-title svg-desc"
     font-family="Inter, system-ui, sans-serif">
  <title id="svg-title">Platform Architecture</title>
  <desc id="svg-desc">Three-layer architecture showing customer, platform, and data tiers</desc>
  ...
</svg>
```

---

## KDCube Brand Color Tokens

Source: `website/assets/colors.html`

```
Backgrounds:    --bg #F6FAFA  |  --surface #FFFFFF  |  --surface-2 #EEF8F7  |  --surface-3 #FDF9EE
Brand teal:     --teal #01BEB2  |  --teal-dark #009C92  |  --teal-pale #C6F3F1
Brand blue:     --blue #4372C3  |  --blue-dark #2B4B8A  |  --blue-pale #DDEAFE
Brand purple:   --purple #6B63FE  |  --purple-pale #EBEBFF
Brand gold:     --gold #F0BC2E  |  --gold-dark #C89A10  |  --gold-pale #FFF8DC
Brand green:    --green #52B044  |  --green-dark #3A8030  |  --green-pale #E8F7E5
Brand sky:      --sky #38B8C8  |  --sky-pale #E0F5F8
Text:           --text #0D1E2C  |  --text-2 #3A5672  |  --text-muted #7A99B0
Borders:        --border #D8ECEB
```

### Semantic Zone Color Assignments

| Zone meaning | Container fill | Inner box fill | Border | Header bar | Badge bg |
|-------------|---------------|---------------|--------|------------|----------|
| Customer-owned (teal) | #C6F3F1 or #E5FAF8 | #F0FAFA | #01BEB2 | #01BEB2 | #009C92 |
| Business value (gold) | #FFF8DC | #FFFCF0 | #F0BC2E | #F0BC2E | #C89A10 |
| Platform / OSS (blue) | #DDEAFE or #EBF1FF | #F0F5FF | #4372C3 | #4372C3 | #2B4B8A |
| Integration path (purple) | #EBEBFF or #F2F1FF | #F8F6FF | #6B63FE | — | — |
| Positive callout (green) | #E8F7E5 | — | #52B044 | — | — |
| Neutral / general | #EEF8F7 or #F6FAFA | — | #D8ECEB | — | — |

### Arrow Colors

| Arrow type | Color | Style |
|-----------|-------|-------|
| Data flow (default) | #3A5672 (text-2) | solid, 1.5px |
| Customer-owned flow | #009C92 (teal-dark) | solid, 1.5px |
| Integration / embed | #6B63FE (purple) | dashed (6,3), 2px |
| Perimeter border | #01BEB2 (teal) | dashed (10,5), 2.5px |

---

## Recommended SVG Skeleton

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 820 560"
     role="img" aria-labelledby="svg-title"
     font-family="Inter, system-ui, sans-serif">

  <title id="svg-title">Diagram title</title>

  <defs>
    <!-- Default arrow (text-2) -->
    <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#3A5672"/>
    </marker>
    <!-- Customer-owned flow arrow (teal-dark) -->
    <marker id="arrow-teal" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#009C92"/>
    </marker>
    <!-- Integration arrow (purple) -->
    <marker id="arrow-purple" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#6B63FE"/>
    </marker>

    <filter id="shadow" x="-2%" y="-2%" width="104%" height="104%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.08"/>
    </filter>
  </defs>

  <!-- Background — must match viewBox -->
  <rect width="820" height="560" rx="16" fill="#F6FAFA"/>

  <!-- Diagram title -->
  <text x="28" y="36" font-size="18" font-weight="800" fill="#2B4B8A">
    Diagram title
  </text>

  <!-- Container with header bar, shadow, badge -->
  <g filter="url(#shadow)">
    <rect x="24" y="60" width="360" height="180" rx="14" fill="#E6F7F5" stroke="#01BEB2" stroke-width="2"/>
    <rect x="24" y="60" width="360" height="36" rx="14" fill="#01BEB2"/>
    <text x="42" y="83" font-size="13" font-weight="700" fill="#FFFFFF">Container title</text>

    <!-- Ownership badge -->
    <rect x="270" y="66" width="96" height="22" rx="11" fill="#009C92"/>
    <text x="318" y="81" text-anchor="middle" font-size="10" font-weight="700" fill="#FFFFFF">
      Customer-owned
    </text>

    <!-- Inner box (lighter tint — NOT white) -->
    <rect x="42" y="112" width="150" height="64" rx="12" fill="#F0FAFA" stroke="#9ADFD8"/>
    <text x="56" y="134" font-size="12" font-weight="700" fill="#0D1E2C">Frontend</text>
    <text x="56" y="152" font-size="9.5" fill="#3A5672">User-facing entry point</text>
  </g>

  <!-- Arrow with marker -->
  <line x1="192" y1="144" x2="238" y2="144"
        stroke="#3A5672" stroke-width="1.5" marker-end="url(#arrow)"/>
</svg>
```

---

## Text Hierarchy

| Role | font-size | font-weight | fill color |
|------|-----------|-------------|------------|
| Diagram title | 18 | 800 | #2B4B8A (blue-dark) |
| Section header (inside box) | 12–13 | 700 | #0D1E2C (text) |
| Body text / subtitle | 9.5–11 | 400–600 | #3A5672 (text-2) |
| Caption / fine print | 8.5–9.5 | 400 | #7A99B0 (text-muted) |
| Badge text | 10–10.5 | 700 | #FFFFFF |
| Pill label | 9–10 | 600 | #2B4B8A (blue-dark) |

Guidelines:
- Do not let text touch borders — leave at least 10px padding
- Keep line-length short inside narrow boxes
- Prefer one subtitle line over dense paragraphs
- Text at the smallest size (8.5pt) must still be legible at 100% zoom

### Text Wrapping in Narrow Boxes

SVG has no automatic text wrapping. For multi-line text in narrow boxes, use
multiple `<text>` elements with incremented `y`:

```xml
<text x="56" y="134" font-size="12" font-weight="700" fill="#0D1E2C">Frontend Service</text>
<text x="56" y="150" font-size="9.5" fill="#3A5672">Handles routing and</text>
<text x="56" y="163" font-size="9.5" fill="#3A5672">session management</text>
```

Line height ≈ font-size × 1.3–1.4. For 9.5pt text, use ~13px spacing.

Do **not** use `<foreignObject>` for text wrapping — it renders inconsistently
across browsers and fails in Playwright PDF/PNG in many cases.

### Term Disambiguation

If the same word appears as both a technology (platform capability) and data
(customer-owned output):
- Platform pill: "Accounting" (the service)
- Data table/label: "Accounting data" (the customer-owned output)

Always add the qualifier on the customer-owned side. The platform side keeps
the bare name.

---

## Arrow Rules

### Standard Arrows

Define markers in `<defs>` and reuse them (see skeleton above). Apply via
`marker-end`:

```xml
<line x1="180" y1="140" x2="240" y2="140"
      stroke="#3A5672" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

### Bidirectional Arrows

Use two parallel lines offset ~10px in Y, each with the same
`marker-end="url(#arrow)"`. The `orient="auto"` makes the arrowhead point in
the line's direction automatically.

```xml
<!-- Forward: left to right -->
<line x1="182" y1="105" x2="234" y2="105" stroke="#3A5672" stroke-width="1.5" marker-end="url(#arrow)"/>
<!-- Backward: right to left -->
<line x1="234" y1="115" x2="182" y2="115" stroke="#3A5672" stroke-width="1.5" marker-end="url(#arrow)"/>
```

Do **not** define a separate `arrow-back` marker with `refX` at the tail. It
renders inconsistently across renderers.

### Dashed Integration Arrows

Use a distinct stroke and dash pattern for embed paths, external integrations,
or optional routes:

```xml
<line x1="535" y1="152" x2="592" y2="152"
      stroke="#6B63FE" stroke-width="2"
      stroke-dasharray="6,3"
      marker-end="url(#arrow-purple)"/>
```

### Curved / Bent Arrows

Route around obstacles with `<path>` instead of `<line>`:

```xml
<path d="M 720 242 L 755 270 L 755 378"
      stroke="#3A5672" stroke-width="1.5"
      fill="none" marker-end="url(#arrow)"/>
```

For smooth curves, use quadratic Bézier (`Q`) or cubic Bézier (`C`):

```xml
<path d="M 200 150 Q 300 100 400 150"
      stroke="#3A5672" stroke-width="1.5"
      fill="none" marker-end="url(#arrow)"/>
```

### Arrow Labels

Position labels **above** or **beside** the line — never directly on it.
Offset by at least 10px from the line path. For busy diagrams, place a small
neutral backing rect behind the label to prevent line–text collision:

```xml
<rect x="245" y="125" width="60" height="16" rx="3" fill="#F6FAFA"/>
<text x="275" y="137" text-anchor="middle" font-size="9" fill="#3A5672">API call</text>
```

---

## Badges, Pills, and Perimeters

### Ownership Badges

Compact rounded pills in the header bar of each major container:

```xml
<!-- Customer-owned badge (teal-dark) -->
<rect x="640" y="62" width="140" height="24" rx="12" fill="#009C92"/>
<text x="710" y="79" text-anchor="middle" font-size="10.5" font-weight="700" fill="#FFFFFF">Customer-owned</text>

<!-- OSS badge (blue-dark) -->
<rect x="672" y="62" width="108" height="24" rx="12" fill="#2B4B8A"/>
<text x="726" y="79" text-anchor="middle" font-size="10.5" font-weight="700" fill="#FFFFFF">OSS · MIT</text>
```

Every major container should have an ownership or status badge when the diagram
conveys responsibility or deployment boundaries.

### Capability Pills

Small rounded-rect tags inside a box showing features or technologies:

```xml
<rect x="50" y="178" width="80" height="22" rx="11" fill="#DDEAFE"/>
<text x="90" y="193" text-anchor="middle" font-size="9.5" font-weight="600" fill="#2B4B8A">Auth</text>
```

When a container has multiple pill rows, budget each row's height explicitly
in containment math (pill_height + gap ≈ 28px per row).

### Dashed Perimeter Borders

For "inside this boundary" diagrams:

```xml
<rect x="20" y="50" width="780" height="496" rx="14"
      fill="none" stroke="#01BEB2" stroke-width="2.5" stroke-dasharray="10,5"/>
```

Perimeter label on the top edge — place a small background rect under the label
so the dashed line does not cut through the text:

```xml
<rect x="28" y="44" width="218" height="20" rx="4" fill="#F6FAFA"/>
<text x="34" y="58" font-size="11" font-weight="700" fill="#009C92">Customer Environment</text>
```

The background rect width must be only text_width + ~10px padding. If it's too
wide, the dashed border disappears for too long and looks broken.

### Legend / Key

For complex diagrams with multiple zone colors or arrow types, add a compact
legend in a bottom corner:

```xml
<g transform="translate(580, 510)">
  <text x="0" y="0" font-size="10" font-weight="700" fill="#0D1E2C">Legend</text>
  <line x1="0" y1="14" x2="24" y2="14" stroke="#3A5672" stroke-width="1.5" marker-end="url(#arrow)"/>
  <text x="30" y="18" font-size="9" fill="#3A5672">Data flow</text>
  <line x1="0" y1="30" x2="24" y2="30" stroke="#6B63FE" stroke-width="2" stroke-dasharray="6,3" marker-end="url(#arrow-purple)"/>
  <text x="30" y="34" font-size="9" fill="#3A5672">Integration</text>
  <rect x="0" y="42" width="16" height="10" rx="3" fill="#C6F3F1" stroke="#01BEB2"/>
  <text x="22" y="51" font-size="9" fill="#3A5672">Customer-owned</text>
</g>
```

---

## Shadow Filter

```xml
<filter id="shadow" x="-2%" y="-2%" width="104%" height="104%">
  <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.08"/>
</filter>
```

Apply to **primary containers only**: `filter="url(#shadow)"`. Do not apply to
inner boxes, pills, or arrows — it creates visual noise and slows rendering.

---

## Layout Rules

### ViewBox Sizing

For document-embedded diagrams, a width of **820px** works well for A4 pages.
Use this pattern:
- Width: fixed from diagram design (820 standard)
- Height: computed from last visual element + 10–15px margin
- Background `<rect>` dimensions exactly matching the `viewBox`

### Section Height Budgeting

When a container has a header bar, several boxes, pills, and labels, budget for
all of them explicitly:

```text
container_y     = 300
header_h        = 36
content_start   = 300 + 36 + 14 = 350
box_1           = y=350, h=64   → bottom = 414
box_2           = y=424, h=64   → bottom = 488
last_child_bot  = 488
container_h     = 488 - 300 + 10 = 198 (minimum)
```

### Inner Fills

Do **not** use pure white (`#FFFFFF`) for boxes inside colored containers
unless the contrast goal is explicit. Prefer a lighter tint of the parent zone
(see Semantic Zone Color Assignments table).

Good pattern:
- Outer container: tinted (e.g., `#C6F3F1`)
- Inner cards: lighter tint (e.g., `#F0FAFA`)
- Page background: neutral off-white (`#F6FAFA`)

This preserves visual grouping.

---

## Diagram Type Templates

### Three-Layer Architecture

Three horizontal bands stacked vertically. Each band has a colored background
with header bar, ownership badge on the right, and inner boxes or pills showing
components.

Typical dimensions: **820 × 550px**.

### Data Boundary / Flow

Dashed perimeter enclosing all boxes. Flow arrows between: User → Frontend →
Platform → Bundle → Storage/APIs/External.

Typical dimensions: **820 × 600px**.

### Component Anatomy

Nested boxes showing internal structure of a component. Optional: side panel
showing integration target with dashed embed arrow.

Typical dimensions: **820 × 565px**.

---

## Rendering and Inspection Workflow

### Step 1: Write the SVG

Produce the pure SVG with computed geometry.

### Step 2: Render to PNG for Inspection

Wrap the SVG in minimal HTML and render with `write_png`:

```
write_png(
    path="check.png",
    format="html",
    content="<html><body style='margin:0;background:white'>" + svg_content + "</body></html>",
    content_selector="svg",
    fit="content",
    width=1600,
    device_scale_factor=2,
)
```

### Key `write_png` Parameters

| Parameter | Default | Use |
|-----------|---------|-----|
| `format` | `"mermaid"` | Set to `"html"` for SVG rendering |
| `content_selector` | None | Set to `"svg"` to crop tightly to the SVG element |
| `fit` | `"content"` | `"content"` = tight crop; `"viewport"` = full page |
| `width` | 3000 | Viewport width in px. Use 1600–2400 for diagrams |
| `device_scale_factor` | 2.0 | Pixel ratio. Use 2 for inspection, 3 for final high-res |
| `padding_px` | 32 | Padding around content when `fit="content"` |
| `zoom` | None | CSS zoom (1.2–1.8) for improved readability |
| `background` | `"white"` | Set to `"transparent"` for alpha-channel PNGs |
| `render_delay_ms` | 1000 | Extra delay for JS-heavy content (not needed for pure SVG) |
| `full_page` | True | Capture full scrollable page (only relevant for `fit="viewport"`) |

**For high-resolution PNG output (embedding in PPTX/DOCX):**

```
write_png(
    path="diagrams/arch-diagram.png",
    format="html",
    content="<html><body style='margin:0;background:white'>" + svg_content + "</body></html>",
    content_selector="svg",
    fit="content",
    width=2400,
    device_scale_factor=3,
)
```

### Step 3: Inspect and Fix

Read the PNG. Check the inspection checklist. Fix issues. Re-render. Repeat
until all checks pass.

### Step 4: Embed into Final Document

**For PDF (`write_pdf` with HTML format):**
Embed as inline SVG for maximum fidelity and self-containment:

```html
<div class="diagram-container">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 560" ...>
    <!-- diagram content -->
  </svg>
</div>
```

Or reference a saved SVG file via `<img>`:

```html
<img src="diagrams/arch.svg" style="width:100%;max-width:820px" />
```

Prefer inline SVG when you need maximum fidelity and self-containment.

**For PPTX (`write_pptx`) or DOCX (`write_docx`):**
These formats do not support SVG. Render the SVG to a high-resolution PNG
first (using `device_scale_factor=3`, `width=2400`), then embed the PNG.

---

## Multi-Diagram Workflow

When a document requires several diagrams:

1. **Build each SVG independently** — each should be a self-contained document
   with its own `viewBox`, `<defs>`, and background
2. **Use unique marker IDs** across diagrams if they will be inlined into the
   same HTML page (e.g., `arrow-1`, `arrow-2`), or use the same IDs if each
   SVG is in its own `<img>` tag (isolation avoids conflicts)
3. **Render each to PNG** for inspection before embedding
4. **Use a consistent color vocabulary** across all diagrams in the same
   document — same zone fills, same arrow conventions, same text hierarchy

---

## Inspection Checklist

After generating each SVG, render to PNG and verify:

| Check | What to verify |
|-------|----------------|
| Containment | No box, text, pill, or arrow extends past its parent |
| ViewBox | Background rect matches viewBox; no clipping at edges |
| Arrow direction | All arrowheads point in the intended direction |
| Label placement | No text hidden behind strokes or overlapping lines |
| Inner fills | Inner boxes use tinted fills — no accidental `#FFFFFF` inside colored containers |
| Subtitles | Every major named box has a short explanation |
| Ownership | Major containers have ownership/status labeling when relevant |
| Disambiguation | Technology and data distinguished where overlapping |
| Readability | The smallest text (8.5pt) still reads clearly in PNG output |
| Marker IDs | If multiple SVGs inline in one HTML, marker IDs are unique |

---

## Common Failure Modes

### Clipped Bottom Edge

**Cause:** `viewBox` height too small.
**Fix:** Recompute `last_child_bottom`; increase overall height by 10–15px.

### Pills Overflow Container

**Cause:** Container height budget ignored pill rows.
**Fix:** Count each pill row explicitly (pill_height + gap ≈ 28px per row);
increase container height.

### Diagram Looks Washed Out

**Cause:** Too many similar pale fills.
**Fix:** Strengthen container border stroke; use one darker header bar color;
keep inner cards lighter than the outer container; check the semantic zone
table for appropriate fill contrast.

### Arrow Label Unreadable

**Cause:** Text placed directly on the line.
**Fix:** Offset label by at least 10px; or add a small neutral backing rect
behind it.

### Text Overlaps at Small Sizes

**Cause:** Too many elements in a small box.
**Fix:** Increase box dimensions, or reduce the number of items shown (move
detail to a separate "detail" box or use pills instead of full labels).

### White Boxes Inside Colored Zones

**Cause:** Default `fill` or explicit `#FFFFFF` on inner boxes.
**Fix:** Use a lighter tint of the parent color (see Semantic Zone table).

### Marker Not Rendering in PDF

**Cause:** `marker-end` referencing a marker ID from a different inlined SVG.
**Fix:** Ensure unique marker IDs per SVG, or isolate each SVG in its own
`<img>` element.

---

## Final Rule

Do not trust SVG layout by inspection of the code alone.

For any non-trivial diagram:
1. Compute the geometry
2. Render to PNG
3. Inspect visually
4. Fix spacing, containment, and labels
5. Only then embed into the final document
