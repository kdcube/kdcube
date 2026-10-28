---
id: sdk.google-slides.response-fixtures
title: Captured Google Slides response fixtures
summary: Five trimmed presentation captures and 24 measured text-fit cases preserve the provider hierarchy, styles and geometry with neutral text.
tags: [google-slides, fixtures, testing]
keywords: [presentations.get, layouts, masters, fit calibration, provenance]
see_also:
  - ./calibration.md
  - https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations
---

# Captured Slides responses

The five presentation fixtures come from full live `presentations.get`
responses captured on 2026-10-28. Slides, layouts, masters, text-run boundaries,
notes, transforms and effective styles are retained from those bodies. The
fixtures keep the fields consumed by the adapter and calibration tests;
unused page/theme/shape properties, borders and provider URLs are omitted.
The counts are Simple 3 slides/11 layouts, Streamline 3/11, Focus 4/11,
Material 4/11 and converted PPTX 16/17, each with one master.

Drive IDs retain their stable capture aliases. Every captured text run,
speaker note, table cell and alt text is replaced with neutral text of the
same character and UTF-16 lengths, scripts, whitespace and line breaks.
Transport-escaped tags retain their syntax and receive neutral names of equal
length. Template replacement tests use separate synthetic inputs.
Presentation titles use neutral fixture aliases; theme layout names remain
unchanged. `provenance.json` records the original capture hashes, resulting
fixture hashes, retained fields, transformations and before/after sizes.
The separate first-page responses remain input evidence rather than duplicated
snapshot fixtures. Missing fields stay missing: the PPTX source, for example,
returned no `revisionId`.

`fit-calibration.json` keeps the consumed fields of all 24 captured pages and
the copies' page sizes, layouts and masters for style inheritance. Duplicate
copy slides are omitted because each case supplies its own page. Observations
from the original matching PNGs record
visible lines, line pitch and whether ink extends past the body frame.
Thumbnail hashes identify the exact images; the PNGs remain measurement input
rather than adding binary fixtures to this source tree. The Material long-text
images clip at the slide edge, so their visible line counts are lower bounds.
All 24 images were inspected. These observations describe the original text;
neutral strings preserve the estimator's inputs, not exact rendered glyph
widths. No new rendering of the neutral text is claimed.

The six JSON bodies shrink from **6,080,842 to 2,398,315 bytes** (60.6 percent).
`trim_fixtures.py --source-ref <complete-fixture-commit>` reproduces the
projection and neutralisation using only the standard library.
`compare_fixtures.py --source-ref <complete-fixture-commit>` runs with the
qualified SDK interpreter and overlay. It compares all five deck snapshots
and 24 calibration snapshots: text becomes a per-character script/length/
whitespace profile; all other fields, including every fit value, compare
exactly. The measured observations and thumbnail hashes must also match.

See [calibration](calibration.md) for the measurements, coefficient choice and
known conservative warning. The converted PPTX deck has no BODY or OBJECT
placeholder, so it has no calibration cases; that remains a verification gap
for a later slide-creation pass. Grid tolerance belongs to the later composition
stage and is not calibrated here.
