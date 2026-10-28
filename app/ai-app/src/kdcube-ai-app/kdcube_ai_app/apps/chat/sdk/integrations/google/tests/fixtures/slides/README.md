# Slides response fixtures

These five fixtures reconstruct Slides v1 `Presentation` responses from live
probe observations recorded on 2026-10-27. They preserve the measured page
sizes, element sizes/transforms and layout names from Simple, Streamline,
Focus, Material and a PPTX-converted deck. Object IDs are aliases and text is
synthetic. Layout-object links, text runs, styles, master links and unmeasured
response fields are reconstructed using the official REST schema; they are
not raw provider responses. Live acceptance must verify those fields.

The rotation and group fixtures exercise relative transforms; they do not
claim to calibrate fonts across themes. The initial fit constants remain
Latin 0.50, Cyrillic 0.60 and line pitch 1.35, measured for one Arial body box.

Reference: https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations
