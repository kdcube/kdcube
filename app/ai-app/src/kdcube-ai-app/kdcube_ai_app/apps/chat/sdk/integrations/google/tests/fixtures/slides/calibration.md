---
id: sdk.google-slides.fit-calibration
title: Slides text-fit calibration from rendered body boxes
summary: Twenty-four rendered cases calibrate a conservative fit warning for captured Arial, Lato and Roboto text while preserving explicit limits for other fonts and PPTX.
tags: [google-slides, fit, testing]
keywords: [Latin, Cyrillic, line pitch, overflow, body placeholder, calibration]
see_also:
  - ./README.md
  - https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text
---

# Text-fit calibration

Captured 2026-10-28: four themes, Latin and Cyrillic, each at one, three and
six repeats. All 24 LARGE PNGs were inspected. Their SHA-256 values, actual
page geometry, layout/master chains and length/script-preserving neutral text
are in `fit-calibration.json`. Observations describe the original captured
text and PNGs; neutral text preserves the estimator inputs but has not been
rendered to claim identical glyph widths. Its input
capture SHA-256 is `16b76ad18a2dbdccbe669b412510e569cf11ad56345f02c6f032b736d9b146d7`.

The stock body font resolves to Lato 13 pt for Streamline/Focus, Arial 18 pt
for Simple, and Roboto 18 pt for Material. Master paragraph line spacing is
115 percent. Stable multiline ink-row pitch is 18 pt for Lato and 24.75 pt
for Arial/Roboto; two-line glyph bounds vary from 17.55 to 25.2 pt.
Dividing pitch by font size and by 1.15 gives about 1.20; the estimator rounds
the base line-pitch multiplier to **1.22**. Multiplying the old 1.35 starting
value by the inherited 1.15 overestimated the measured line spacing.

The calibrated width factors are **Latin 0.46, Cyrillic 0.54** (bold still
adds 0.08). The nominal inset is **7.2 pt per edge**, supported by first ink
appearing roughly 7 pt from the frame's left edge in these images. Insets,
widths and line pitch remain estimates, not font-metric measurements. The
paragraph's actual bullet membership controls list indentation: an absent
`ParagraphMarker.bullet` means it is not a list, even if its parent's style
comes from a bulleted placeholder. The official Text reference defines this
separately from inherited paragraph styling.

Ink was measured by horizontal row projection against the dominant body
background: RGB distance over 180, at least nine ink pixels per row, merging
row groups across at most three empty rows. Line pitches use the median row
start separation. The image width and presentation page size convert pixels
to points. Visual inspection checked the line counts and frame crossings.
For long Material cases the slide image clips additional text, so 10 visible
lines is a lower bound rather than the total number of rendered lines.

| Case | Font pt | Chars | Visible lines | Pitch pt | Ink beyond frame | Estimated lines | Warning |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| streamline_rotated.latin.1 | 13 | 130 | 2 | 18.0 | no | 2 | ok |
| streamline_rotated.latin.3 | 13 | 392 | 4 | 18.0 | no | 4 | ok |
| streamline_rotated.latin.6 | 13 | 785 | 8 | 18.0 | no | 8 | ok |
| streamline_rotated.cyrillic.1 | 13 | 134 | 2 | 18.0 | no | 2 | ok |
| streamline_rotated.cyrillic.3 | 13 | 404 | 5 | 18.0 | no | 5 | ok |
| streamline_rotated.cyrillic.6 | 13 | 809 | 9 | 18.0 | no | 10 | overflow_risk |
| focus_group.latin.1 | 13 | 130 | 2 | 17.55 | no | 2 | ok |
| focus_group.latin.3 | 13 | 392 | 5 | 18.0 | no | 5 | ok |
| focus_group.latin.6 | 13 | 785 | 9 | 18.0 | no | 9 | ok |
| focus_group.cyrillic.1 | 13 | 134 | 2 | 18.0 | no | 2 | ok |
| focus_group.cyrillic.3 | 13 | 404 | 5 | 18.0 | no | 6 | ok |
| focus_group.cyrillic.6 | 13 | 809 | 10 | 18.0 | no | 11 | tight |
| material_group_rotated.latin.1 | 18 | 130 | 2 | 24.75 | no | 2 | ok |
| material_group_rotated.latin.3 | 18 | 392 | 6 | 24.75 | no | 6 | ok |
| material_group_rotated.latin.6 | 18 | 785 | 10+ | 24.75 | yes | 11 | overflow_risk |
| material_group_rotated.cyrillic.1 | 18 | 134 | 2 | 25.2 | no | 3 | ok |
| material_group_rotated.cyrillic.3 | 18 | 404 | 6 | 24.75 | no | 7 | ok |
| material_group_rotated.cyrillic.6 | 18 | 809 | 10+ | 24.75 | yes | 13 | overflow_risk |
| simple_table.latin.1 | 18 | 130 | 2 | 24.75 | no | 2 | ok |
| simple_table.latin.3 | 18 | 392 | 5 | 24.75 | no | 5 | ok |
| simple_table.latin.6 | 18 | 785 | 10 | 24.75 | no | 10 | tight |
| simple_table.cyrillic.1 | 18 | 134 | 2 | 24.75 | no | 2 | ok |
| simple_table.cyrillic.3 | 18 | 404 | 6 | 24.75 | no | 6 | ok |
| simple_table.cyrillic.6 | 18 | 809 | 12 | 24.75 | yes | 12 | overflow_risk |

The arithmetic comparison flags all three observed overflowing cases. It
reduces conservative false warnings from five to one: Streamline Cyrillic at
six repeats fits visually, while the estimator warns near the boundary.
For every fully visible sample, estimated line count is within one line of
the observed count. Tests assert these captured outcomes; execution evidence
is recorded with the tested source head separately.

The input is one repeated sentence per script and three font families. It
does not establish accuracy for arbitrary prose, mixed styles, custom fonts,
other aspect ratios, or an exact text-rendering engine. A thumbnail remains
the visual check. The converted PPTX deck has no BODY/OBJECT placeholder, so
PPTX fit calibration remains open for a later slide-creation pass. Grid
alignment tolerance is outside this calibration stage.
