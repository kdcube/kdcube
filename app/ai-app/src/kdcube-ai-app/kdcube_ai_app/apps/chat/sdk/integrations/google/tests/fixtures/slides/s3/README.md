---
id: google.slides.fixtures.s3
title: Slides table, video and comment contract fixtures
summary: Small neutral reconstructed provider shapes for selector and transport regression tests.
tags: [google, slides, tests, comments, tables]
keywords: [comment anchors, Drive comments, ALL_SAVED, merged table cells]
see_also:
  - https://developers.google.com/workspace/slides/api/guides/comments
  - https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request
---

# Slides contract fixtures

These fixtures are **reconstructed from design section 15.1 field shapes, not
raw responses**. They preserve the recorded request unions, text-bearing and
empty table cells, comment thread/post identities and the provider save-state
field. Identifiers, content and the presentation are neutral synthetic data.
`provenance.json` records the boundary and public API sources.

`drive-ui-anchors.json` additionally preserves field shapes from a live Drive
probe, with every identifier, timestamp value and text replaced. Its page and
shape/text anchors are JSON strings. Table targets carry a table element and
quoted text, without a cell coordinate; reads retain that loss of precision.
An arbitrary stored anchor is not evidence of visual association in Slides.

The focused adapter tests exercise all five anchor forms, file comments,
read-back mapping, author checks, failure states and bounded cursor traversal.
Pure compiler tests cover Docs-style row/column selectors, merged slots,
Unicode ranges, computed table/video placement and previews. These are source
contract checks. Native Slides comment request/response compatibility and the
visual association of API-created anchors still require a live comparison.
