---
id: sdk.google-slides.response-fixtures
title: Captured Google Slides response fixtures
summary: Five sanitized full presentation responses and 24 measured text-fit cases preserve the provider hierarchy, styles and geometry used by snapshot tests.
tags: [google-slides, fixtures, testing]
keywords: [presentations.get, layouts, masters, fit calibration, provenance]
see_also:
  - ./calibration.md
  - https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations
---

# Captured Slides responses

The five presentation fixtures come from full live `presentations.get`
responses captured on 2026-10-28. They replace the earlier envelopes built
around structural measurements. Slides, layouts, masters, text runs, notes,
transforms and styles come from those captured bodies; none are reconstructed.
The counts are Simple 3 slides/11 layouts, Streamline 3/11, Focus 4/11,
Material 4/11 and converted PPTX 16/17, each with one master.

The capture provider sanitized Drive file IDs to stable aliases, replaced
provider content/source URLs with a redaction marker, and escaped four literal
template tags for text transport. The escaped tags remain as captured; template
replacement tests use their own small synthetic inputs. Fixture extraction
keeps the envelope's `presentation` member, assigns a neutral presentation title,
and formats JSON. Every other provider field value is preserved. The original
PPTX title identified a person and is represented by its neutral fixture alias.
`provenance.json` records the input and fixture digests and all transformations.
The separate first-page responses remain input evidence rather than duplicated
snapshot fixtures. Missing fields stay missing: the PPTX source, for example,
returned no `revisionId`.

`fit-calibration.json` retains the full captured pages for 24 writes and the
copies' presentations, layouts and masters for style inheritance. Presentation
titles are neutral aliases. Observations added from the matching PNGs record
visible lines, line pitch and whether ink extends past the body frame.
Thumbnail hashes identify the exact images; the PNGs remain measurement input
rather than adding binary fixtures to this source tree. The Material long-text
images clip at the slide edge, so their visible line counts are lower bounds.
All 24 images were inspected.

See [calibration](calibration.md) for the measurements, coefficient choice and
known conservative warning. The converted PPTX deck has no BODY or OBJECT
placeholder, so it has no calibration cases; that remains a verification gap
for a later slide-creation pass. Grid tolerance belongs to the later composition
stage and is not calibrated here.
