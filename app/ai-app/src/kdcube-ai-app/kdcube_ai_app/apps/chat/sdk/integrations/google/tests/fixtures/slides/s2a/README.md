---
id: sdk.google-slides.layout-fixtures
title: Captured layout creation and PPTX fit fixtures
summary: Small neutral API projections cover five themes, complete placeholder mappings, speaker notes and six measured PPTX body cases.
tags: [google-slides, fixtures, layouts, testing]
keywords: [OBJECT, TWO_OBJECTS, placeholderIdMappings, speaker notes, Trebuchet MS, calibration]
see_also:
  - ../README.md
  - ../calibration.md
  - https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request
---

# Layout creation and PPTX calibration

These projections retain the captured layout geometry and inherited styles
needed to create and preview slides. They cover Simple, Streamline, Focus,
Material and a converted PowerPoint theme. Each keeps the title/body and
two-column layouts, the referenced master style chain, page size and first
slide's insertion-master metadata. The first slide's content is omitted;
the five parent fixtures cover complete snapshot diagnostics.

The API probe on 2026-10-28 created a slide on the PowerPoint `OBJECT`
layout with both content placeholder IDs mapped. The new BODY retained the
layout's 676.913 by 305.575 pt frame and resolved through the layout/master
chain to **Trebuchet MS 18 pt**, with **100 percent** line spacing. It also
returned a real speaker-notes placeholder ID.

A second probe mapped only TITLE on a stock `TITLE_AND_BODY` layout.
Google created the unmapped BODY with a generated ID; the slide-number
placeholder was absent. Creation therefore maps every content placeholder
and explicitly deletes unfilled ones unless the caller asks to keep them.

All six LARGE PPTX thumbnails were inspected. Neutral-gray ink was separated
from the colored decorative corner using chroma spread below 40 and RGB
distance from the background above 180; at least nine pixels form an ink row,
and gaps of at most three rows merge. The initial unfiltered scan counted
the decorative corner as a line and a false overflow, which visual inspection
caught. Baseline pitch was **21.6 pt** in every case. Original text fits in
all six cases:

| Script | Characters | Visible lines | Last ink below frame top, pt | Overflow |
|---|---:|---:|---:|---|
| Latin | 130 | 2 | 44.675 | no |
| Latin | 392 | 6 | 128.075 | no |
| Latin | 785 | 11 | 236.075 | no |
| Cyrillic | 134 | 2 | 45.275 | no |
| Cyrillic | 404 | 6 | 131.675 | no |
| Cyrillic | 809 | 12 | 261.275 | no |

The existing calibrated coefficients remain unchanged. Tests compare the
captured body geometry, inherited font, warning status and line estimates
against these observations. The estimator's line counts stay within one of
the observed counts; other fonts and mixed styles remain heuristic.

`provenance.json` records original capture hashes and output hashes/sizes.
All run, notes, table and alt text uses length/script/line-break-preserving
neutral text. Presentation titles use neutral aliases. Observations and PNG
hashes refer to original rendered text, not a new rendering of the neutral
strings. The JSON projections total about 145 KB; PNG bytes and provider
URLs stay outside the source tree.

The standard-library-only `prepare_layout_fixtures.py` in the parent directory
reproduces the projections from the five response envelopes, two creation
probe captures and six inspected measurement records. It accepts
`--capture-dir`, `--probe-dir` and `--measurements`.
