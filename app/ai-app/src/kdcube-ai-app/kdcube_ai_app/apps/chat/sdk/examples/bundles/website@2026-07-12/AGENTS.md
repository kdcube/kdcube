---
id: website@2026-07-12/agents
title: "Website App Builder Notes"
summary: "Ownership and validation rules for the reference app-hosted website."
status: active
---

# Website App Builder Notes

Read `README.md`, `docs/README.md`, `interface/README.md`, and
`docs/storage/README.md` before changing this app.

Rules:

- Keep site composition in this app's `bundles.yaml` config, not in
  `assembly.yaml`.
- Keep each enabled site alias unique. Root selection is host first, then one
  explicit default; do not add site interpretation to the CLI or OpenResty.
- Read platform/auth configuration from `/api/cp-frontend-config`; do not
  duplicate provider-specific browser config.
- Treat `/profile` as the authoritative session state.
- Keep `entrypoint.py` thin and all runtime handlers async.
- Keep configuration defaults, descriptor template, interface, tests, release
  note, and dated journal synchronized.

Validate:

```bash
python -m py_compile entrypoint.py
node --check ui/site/site.js
python -m pytest -q tests
```
