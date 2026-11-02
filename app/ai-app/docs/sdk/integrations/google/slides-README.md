---
id: sdk.integrations.google-slides
title: "Connected Presentations Through The Slides Namespace"
summary: "Import presentations, edit selected slides, and read focused write diagnostics with deck-wide counts."
tags: [sdk, integrations, google, slides, named-services]
keywords: [slides, named_services_call, import, object_ref, diagnostics_summary, file_ref]
see_also:
  - repo:kdcube/app/ai-app/docs/sdk/integrations/google/google-README.md
  - repo:kdcube/app/ai-app/docs/sdk/integrations/public-reachability-README.md
---

# Connected Presentations Through The Slides Namespace

The `slides` namespace lets an authorized caller read, create, copy, fill and
edit presentations through a connected Google account. `object.schema`
publishes the available actions, selectors, account claims and payloads.

## Import A New Presentation

Import creates a presentation from a Drive source or an authorized deployment
file. Use the targetless `named_services_call` route:

```json
{
  "namespace": "slides",
  "operation": "object.action",
  "action": "import",
  "payload": {"source_file": "drive-file-id", "title": "Imported deck"}
}
```

Omit `object_ref`. The schema declares `import.object_ref: none`; an existing
presentation or slide ref is refused with `slides_import_target_forbidden` and
the correct call route. A deployment file uses `file_ref` in the payload in
place of `source_file`. PPTX/ODP uploads are bounded to 5 MB and validated
before conversion. An account-bound source must match the selected account.

The descriptor's `slides.tools.call.operations` declares
`object.action.import` with `named_services:use`, `slides:read` and
`slides:write` grants. Align this declaration in each deployment descriptor set
before publishing that set. Runtime consent still applies to the selected
account and operation.

## Read A Write Result

Write results carry `diagnostics_summary` for the whole deck:

```json
{
  "slides": 8,
  "fit": {"ok": 19, "tight": 1, "overflow_risk": 0},
  "overlaps": 1,
  "off_slide": 0,
  "geometry_diagnostics_truncated": false,
  "predicted": false
}
```

Ordinary mutations include detailed `fit`, `overlaps` and `off_slide` rows for
the slides their compiled requests touch. Source slides read for duplication
remain outside that scope. Counts still describe the whole deck, including
problems on other slides. Detailed response lists retain their existing
1,000-row limit; summary fit counts include every projected element. Geometry
counts describe the bounded snapshot diagnostics and identify truncation.

Whole-deck creations (`create`, `import`, `copy`, `fill_template`) return the
new presentation identity and the summary. `object.get` retains full
per-element diagnostics in its requested slide range. Preview diagnostics
are predicted; import previews report `available: false` because conversion
has not produced a native presentation yet. A trashed presentation can also
report unavailable diagnostics until it can be read.

For image `file_ref` actions, the shared
[public-origin probe](../public-reachability-README.md) diagnoses a tunnel or
proxy refusal before provider execution. Image URI validation uses an
anonymous browser-like fetch and retains its SSRF, format and size checks.
