---
name: mermaid
description: |
  Teaches agents how to write syntactically correct Mermaid diagrams that render
  without errors across all supported diagram types. Covers quoting rules,
  common syntax pitfalls, diagram-type-specific syntax (flowchart, sequence,
  class, ER, Gantt, state, pie, mindmap), subgraphs, styling with classDef
  and theme variables, KDCube brand color integration, write_png / write_pdf
  tool parameters for Mermaid rendering, readability tuning, and complexity
  limits.
version: 2.0.0
category: visualization
tags:
  - mermaid
  - diagrams
  - flowcharts
  - sequence
  - class
  - er
  - gantt
  - state
  - syntax
  - visualization
when_to_use:
  - Generating Mermaid diagrams in markdown code blocks
  - Fixing broken Mermaid that won't render
  - Validating existing Mermaid code for syntax correctness
  - Creating flowcharts, sequence diagrams, ER diagrams, Gantt charts, state machines, or other Mermaid visualizations
  - Rendering Mermaid to PNG or PDF via write_png / write_pdf
  - Styling Mermaid diagrams with KDCube brand colors
author: kdcube
created: 2026-01-26
updated: 2026-04-14
namespace: public
---

# Mermaid Diagram Authoring

## Overview

Mermaid diagrams fail to render when syntax rules are violated. This skill
teaches the critical syntax requirements, diagram-type-specific patterns,
styling, tool integration, and common error patterns that break rendering.

The KDCube rendering pipeline uses **Mermaid.js v11.3.0** via Playwright.

## Tools

| Tool | Use |
|------|-----|
| `write_png(path, content, format="mermaid")` | Render a Mermaid diagram to PNG. Supply raw Mermaid text — no ``` fences. |
| `write_pdf(path, content, format="mermaid")` | Render a Mermaid diagram to a full-page PDF. Same input rules as PNG. |

Both tools accept **raw Mermaid text** as `content` — never wrap in code fences.

---

## Signs of Broken Mermaid

**Visual indicators the diagram won't render:**
- Parser error messages in the output
- Blank diagram area where chart should appear
- Red error text or "Syntax Error" notices
- Diagram displays as plain text instead of rendering

**Common root causes:**
1. Unquoted labels containing special characters
2. Missing quotes around multi-word text
3. Special characters not properly escaped
4. Invalid arrow or node syntax
5. Incorrect subgraph structure
6. Wrong diagram type keyword or missing newline after it

---

## Critical Quoting Rules

### ALWAYS Quote When Label Contains:

- **Spaces**: `A["Multi word label"]` not `A[Multi word label]`
- **Parentheses**: `B["Data (filtered)"]` not `B[Data (filtered)]`
- **Periods**: `C["Process v2.1"]` not `C[Process v2.1]`
- **Colons**: `D["Status: Active"]` not `D[Status: Active]`
- **Commas**: `E["Input, Output"]` not `E[Input, Output]`
- **Brackets**: `F["Tags [optional]"]` not `F[Tags [optional]]`
- **Special chars**: `&`, `<`, `>`, `#`, `@`, `+`, `/`, etc.

### Quote Syntax

```mermaid
graph TB
  A["Quoted text"]      %% Double quotes for safety
  B[SimpleText]         %% No quotes needed for single alphanumeric word
```

**Rule:** When in doubt, always use double quotes `"..."` around labels.
Overquoting never breaks diagrams. Underquoting does.

---

## Common Syntax Errors

### Error 1: Unquoted Special Characters

```
%% BROKEN:
graph TB
  A[User input] --> B[Process (v2.0)]
  B --> C[Output: Result]
```

Parentheses, periods, and colons are Mermaid syntax tokens. Without quotes,
the parser treats them as structure, not content.

```
%% FIXED:
graph TB
  A["User input"] --> B["Process (v2.0)"]
  B --> C["Output: Result"]
```

### Error 2: Missing Quotes on Multi-Word Labels

```
%% BROKEN:
graph LR
  Start --> Process Data --> End Result
```

Spaces separate node IDs from labels. Without quotes, "Process Data" becomes
three separate tokens.

```
%% FIXED:
graph LR
  Start --> ProcessData["Process Data"] --> EndResult["End Result"]
```

### Error 3: Dots/Periods in Labels

```
%% BROKEN:
graph TB
  A[Version 2.5] --> B[File.txt]
```

```
%% FIXED:
graph TB
  A["Version 2.5"] --> B["File.txt"]
```

### Error 4: Broken Subgraph Syntax

```
%% BROKEN:
graph TB
  subgraph Backend Services
    A["API"] --> B["DB"]
  end
```

Subgraph titles with spaces need quotes:

```
%% FIXED:
graph TB
  subgraph "Backend Services"
    A["API"] --> B["DB"]
  end
```

### Error 5: Raw Content in format="mermaid"

```
%% BROKEN — code fences passed to write_png:
```mermaid
graph TB
  A --> B
```
```

When using `write_png` or `write_pdf` with `format="mermaid"`, pass **raw
Mermaid text only** — no ``` fences, no markdown wrapping:

```
%% CORRECT — raw text:
graph TB
  A --> B
```

---

## Node Shape Reference

```
A[Rectangle]          %% default
B(Rounded)            %% rounded corners
C([Stadium])          %% pill shape
D[(Database)]         %% cylinder
E((Circle))           %% circle
F>Asymmetric]         %% flag/ribbon
G{Diamond}            %% decision
H{{Hexagon}}          %% hexagon
I[/Parallelogram/]    %% input
J[\Parallelogram\]    %% output
K[/Trapezoid\]        %% trapezoid
```

**Critical:** Shape brackets must match exactly. `[(` requires `)]`,
`((` requires `))`, `{{` requires `}}`.

---

## Arrow Syntax

### Flowchart Arrows

| Syntax | Meaning |
|--------|---------|
| `-->` | Solid arrow |
| `-.->` | Dotted arrow |
| `==>` | Thick arrow |
| `--x` | Cross ending |
| `--o` | Circle ending |
| `-->\|label\|` | Arrow with inline label |
| `--"label"-->` | Arrow with quoted label |

**Invalid:**
- `->` (too short — use `-->`)
- `--->` (three dashes — use `-->`)
- `-- >` (space breaks syntax)

### Arrow Labels

Two valid patterns for labeled arrows:

```
graph LR
  A -->|"API call"| B
  C --"Webhook"--> D
```

Always quote labels containing special characters.

---

## Diagram Types

### Flowchart (graph)

The most common type. Use `graph TB` (top-to-bottom) or `graph LR`
(left-to-right).

```
graph TB
  A["Start"] --> B{"Decision"}
  B -->|"Yes"| C["Process"]
  B -->|"No"| D["Skip"]
  C --> E["End"]
  D --> E
```

#### Subgraphs

Group related nodes inside named boundaries:

```
graph TB
  subgraph "Frontend"
    A["React App"] --> B["API Client"]
  end
  subgraph "Backend"
    C["API Server"] --> D["Database"]
  end
  B --> C
```

Rules:
- Subgraph titles with spaces or special chars **must** be quoted
- `end` keyword closes each subgraph — must be on its own line
- Subgraphs can be nested (but avoid more than 2 levels deep)
- Nodes can link across subgraph boundaries

### Sequence Diagram

```
sequenceDiagram
  participant U as User
  participant F as Frontend
  participant A as API
  participant D as Database

  U->>F: Click submit
  F->>A: POST /data
  A->>D: INSERT record
  D-->>A: OK
  A-->>F: 201 Created
  F-->>U: Show success
```

Syntax notes:
- `participant` declares actors (use `as` for short aliases)
- `->>` solid arrow, `-->>` dashed reply
- `-x` for lost/failed messages
- Colons separate the arrow from the message text
- Messages do **not** need quotes (colons are part of the syntax here)
- Use `Note over A,B: text` for notes spanning participants
- Use `alt`/`else`/`end` for conditionals, `loop`/`end` for loops

### Class Diagram

```
classDiagram
  class User {
    +String name
    +String email
    +login() bool
  }
  class Order {
    +int id
    +Date created
    +calculate() float
  }
  User "1" --> "*" Order : places
```

Syntax notes:
- Methods use `+` (public), `-` (private), `#` (protected)
- Relationships: `-->` association, `--|>` inheritance, `--*` composition,
  `--o` aggregation
- Cardinality in quotes: `"1"`, `"*"`, `"0..1"`
- Relationship labels after `:` — quote if they contain special chars

### ER Diagram

```
erDiagram
  CUSTOMER ||--o{ ORDER : places
  ORDER ||--|{ LINE_ITEM : contains
  PRODUCT ||--o{ LINE_ITEM : "is in"
```

Syntax notes:
- Entity names must be **unquoted single words** (use underscores, not spaces)
- Relationship labels after `:` — quote if multi-word
- Cardinality symbols: `||` exactly one, `o{` zero or more, `|{` one or more,
  `o|` zero or one

### Gantt Chart

```
gantt
  title Project Timeline
  dateFormat YYYY-MM-DD
  section Planning
    Requirements     :a1, 2026-01-01, 14d
    Design           :a2, after a1, 10d
  section Development
    Backend          :b1, after a2, 21d
    Frontend         :b2, after a2, 18d
  section Testing
    Integration test :c1, after b1, 7d
```

Syntax notes:
- `dateFormat` must precede task definitions
- Tasks use `:taskId, startDate, duration` or `:taskId, after otherId, duration`
- Duration: `1d`, `2w`, etc.
- `section` groups tasks visually
- Task names do **not** use quotes
- Avoid special characters in task names

### State Diagram

```
stateDiagram-v2
  [*] --> Idle
  Idle --> Processing : submit
  Processing --> Success : complete
  Processing --> Failed : error
  Failed --> Idle : retry
  Success --> [*]
```

Syntax notes:
- Use `stateDiagram-v2` (not the legacy `stateDiagram`)
- `[*]` is the start/end pseudo-state
- Transition labels after `:`
- Use `state "Display Name" as s1` for states with special chars in their name

### Pie Chart

```
pie title Revenue by Region
  "North America" : 42
  "Europe" : 28
  "Asia Pacific" : 20
  "Other" : 10
```

Syntax notes:
- Labels **must** be quoted
- Values are numbers (percentages or absolute — Mermaid normalizes)
- `title` is optional but recommended

### Mindmap

```
mindmap
  root((Product Strategy))
    Growth
      New Markets
      Partnerships
    Retention
      Onboarding
      Support
    Platform
      API
      Integrations
```

Syntax notes:
- Indentation defines hierarchy (use consistent spaces)
- Root node shape: `((circle))`, `[square]`, `(rounded)`, or plain text
- Child nodes are plain text by default
- No arrows — hierarchy is implicit from indentation

---

## Styling

### classDef and style

Apply custom styles to flowchart nodes:

```
graph TB
  A["API Gateway"]:::accent --> B["Service"]:::neutral
  B --> C["Database"]:::storage

  classDef accent fill:#4372C3,stroke:#2B4B8A,color:#FFFFFF,stroke-width:2px
  classDef neutral fill:#EEF8F7,stroke:#D8ECEB,color:#0D1E2C
  classDef storage fill:#DDEAFE,stroke:#4372C3,color:#0D1E2C
```

Rules:
- Define `classDef` **after** all node/edge declarations
- Apply with `:::className` suffix on the node
- Colors must be hex (`#RRGGBB`) — no named CSS colors
- Available properties: `fill`, `stroke`, `color` (text), `stroke-width`,
  `stroke-dasharray`

### Init Directive (Theme Variables)

Override theme colors globally using the init directive at the top of the
diagram:

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#EEF8F7',
  'primaryBorderColor': '#01BEB2',
  'primaryTextColor': '#0D1E2C',
  'lineColor': '#3A5672',
  'secondaryColor': '#DDEAFE',
  'tertiaryColor': '#FFF8DC',
  'fontFamily': 'Inter, system-ui, sans-serif'
}}}%%
graph TB
  A["Start"] --> B["End"]
```

The init directive must be the **very first line** — no blank lines before it.

### KDCube Brand Colors in Mermaid

Use these token values for brand-consistent diagrams:

| Purpose | Color | Token |
|---------|-------|-------|
| Primary fill (teal tint) | #EEF8F7 | surface-2 |
| Primary border | #01BEB2 | teal |
| Text (dark) | #0D1E2C | text |
| Text (secondary) | #3A5672 | text-2 |
| Line/arrow color | #3A5672 | text-2 |
| Secondary fill (blue) | #DDEAFE | blue-pale |
| Tertiary fill (gold) | #FFF8DC | gold-pale |
| Accent fill | #4372C3 | blue |
| Accent text (on dark bg) | #FFFFFF | white |

**Via `classDef` (flowcharts):**

```
classDef kdTeal fill:#EEF8F7,stroke:#01BEB2,color:#0D1E2C,stroke-width:2px
classDef kdBlue fill:#DDEAFE,stroke:#4372C3,color:#0D1E2C
classDef kdGold fill:#FFF8DC,stroke:#F0BC2E,color:#0D1E2C
classDef kdAccent fill:#4372C3,stroke:#2B4B8A,color:#FFFFFF,stroke-width:2px
```

**Via `write_png` / `write_pdf` parameters (all diagram types):**

```
write_png(
    path="diagram.png",
    content=mermaid_text,
    format="mermaid",
    mermaid_theme="base",
    mermaid_theme_variables={
        "primaryColor": "#EEF8F7",
        "primaryBorderColor": "#01BEB2",
        "primaryTextColor": "#0D1E2C",
        "lineColor": "#3A5672",
        "secondaryColor": "#DDEAFE",
        "tertiaryColor": "#FFF8DC",
    },
)
```

This approach works for **all** diagram types (sequence, ER, Gantt, etc.),
not just flowcharts.

---

## Rendering with write_png

### Key Mermaid-Specific Parameters

| Parameter | Default | Use |
|-----------|---------|-----|
| `format` | `"mermaid"` | Must be `"mermaid"` for raw Mermaid text |
| `mermaid_theme` | `"default"` | Theme: `default`, `neutral`, `dark`, `forest`, `base` |
| `mermaid_font_size_px` | None | Force font size in px (16–22 recommended for readability) |
| `mermaid_font_family` | None | Force font family (CSS font-family string) |
| `mermaid_scale` | None | Scale the SVG (1.1–1.6 improves readability) |
| `mermaid_config` | None | Full Mermaid initialize config (dict or JSON) |
| `mermaid_theme_variables` | None | Theme variable overrides (dict or JSON) |

### General Parameters That Matter for Mermaid

| Parameter | Default | Use |
|-----------|---------|-----|
| `width` | 3000 | Viewport width. Use 1600–2400 for diagrams |
| `device_scale_factor` | 2.0 | Pixel ratio. Use 2 for standard, 3 for high-res |
| `fit` | `"content"` | `"content"` = tight crop around the diagram |
| `content_selector` | None | Not needed for Mermaid (auto-selects `.mermaid svg`) |
| `padding_px` | 32 | Padding around the cropped diagram |
| `background` | `"white"` | Set `"transparent"` for alpha PNGs |

### Recipe: Readable PNG with KDCube Colors

```
write_png(
    path="architecture.png",
    content=mermaid_text,
    format="mermaid",
    mermaid_theme="base",
    mermaid_theme_variables={
        "primaryColor": "#EEF8F7",
        "primaryBorderColor": "#01BEB2",
        "primaryTextColor": "#0D1E2C",
        "lineColor": "#3A5672",
        "secondaryColor": "#DDEAFE",
        "tertiaryColor": "#FFF8DC",
    },
    mermaid_font_size_px=18,
    mermaid_scale=1.3,
    width=2000,
    device_scale_factor=2,
)
```

### Recipe: High-Res PNG for PPTX/DOCX Embedding

```
write_png(
    path="diagrams/flow.png",
    content=mermaid_text,
    format="mermaid",
    mermaid_font_size_px=20,
    mermaid_scale=1.4,
    width=2400,
    device_scale_factor=3,
)
```

## Rendering with write_pdf

```
write_pdf(
    path="diagram.pdf",
    content=mermaid_text,
    format="mermaid",
)
```

This produces a single-page PDF containing the diagram. For diagrams embedded
within a larger document, use `write_pdf` with `format="html"` and embed the
Mermaid diagram inline (or render to PNG first and include as an image).

---

## Complexity and Readability

### When to Split Diagrams

Split a diagram into multiple smaller ones when:
- More than **15–20 nodes** in a flowchart — the layout becomes tangled
- More than **8 participants** in a sequence diagram — too wide to read
- More than **12 entities** in an ER diagram — relationship lines overlap
- The rendered PNG requires horizontal scrolling at reasonable zoom

### Readability Tips

- Use `mermaid_font_size_px=18` or higher for presentation-quality output
- Use `mermaid_scale=1.2–1.5` to increase overall diagram size
- Keep labels concise — 3–5 words maximum per node
- Use short node IDs (`A`, `B1`, `svc`) and longer display labels in quotes
- For dense diagrams, prefer `graph LR` (left-to-right) over `graph TB` — it
  tends to produce a more compact layout for pipeline-style flows
- For Gantt charts, keep sections to 3–5 tasks each

### Avoiding JavaScript and Interactivity

Mermaid supports `click` callbacks and links, but these do **not** work in
Playwright PNG/PDF rendering. Never use `click` directives in diagrams
destined for static output.

---

## Validation Checklist

Before finalizing a Mermaid diagram:

1. **Quote check** — Any label with spaces, punctuation, or symbols? Add `"..."`
2. **Shape check** — Node shapes properly opened and closed? (`[(` needs `)]`)
3. **Arrow check** — Using valid arrow syntax? (`-->` not `->`)
4. **Type check** — Diagram type declared on line 1? (`graph TB`, `sequenceDiagram`, etc.)
5. **Subgraph check** — Titles quoted? Every `subgraph` has matching `end`?
6. **classDef check** — Defined after all nodes? Hex colors used (not named)?
7. **Init directive check** — If using `%%{init:...}%%`, is it the very first line?
8. **No fences** — If using `write_png`/`write_pdf`, content has no ``` wrappers?
9. **Complexity check** — Under 15–20 nodes? If not, consider splitting.

---

## Quick Fixes

**Diagram won't render at all:**
Quote ALL labels with double quotes, even simple ones. This fixes 80% of cases.

**"Syntax error" on a specific line:**
Check that line for unquoted special characters (`:`, `.`, `()`, `+`, spaces).

**Node shape looks wrong:**
Verify shape bracket pairs: `[(` needs `)]`, `((` needs `))`, `{{` needs `}}`.

**Subgraph errors:**
Quote the title, ensure `end` is on its own line, check for mismatched nesting.

**Diagram renders but text is tiny:**
Use `mermaid_font_size_px=18` and `mermaid_scale=1.3` in the `write_png` call.

**Colors not applying:**
Ensure `classDef` lines are after all node declarations. Use `theme: 'base'`
when overriding `themeVariables` (other themes may ignore your overrides).

**Init directive ignored:**
It must be the very first line — no blank lines, no comments before it.

---

## Remember

- **Quotes are cheap** — overquoting never hurts, underquoting breaks diagrams
- **Special chars are poison** — `:`, `.`, `()`, `[]`, `+`, spaces all need quotes
- **Shape syntax is strict** — bracket pairs must match exactly
- **Raw text for tools** — no ``` fences when using `write_png`/`write_pdf`
- **`theme: 'base'` for custom colors** — other themes override your variables
- **Font size matters** — use `mermaid_font_size_px=18+` for readable output
- **Split large diagrams** — 15+ nodes → multiple smaller diagrams
- **No `click` directives** — they don't work in static PNG/PDF output
- **Init directive first** — `%%{init:...}%%` must be line 1, nothing before it
- **When broken** — start by quoting every label, then simplify
