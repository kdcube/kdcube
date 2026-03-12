---
id: ks:docs/service/cicd/release-bundle-README.md
title: "Release Bundle"
summary: "Step‑by‑step bundle release workflow: tagging, bundles.yaml updates, delivery mode, props, and validation."
tags: ["service", "cicd", "release", "bundles", "delivery", "git", "baked", "props", "redis"]
keywords: ["bundle id", "tag", "commit", "bundles.yaml", "subdir", "module", "baked bundles", "git-defined bundles", "BUNDLES_FORCE_ENV_ON_STARTUP", "BUNDLES_INCLUDE_EXAMPLES"]
see_also:
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/custom-cicd-README.md
  - ks:docs/service/cicd/release-README.md
---
## Bundle Release Process

This doc describes how to release a bundle and update the **bundles descriptor**
(`bundles.yaml`). It applies to both **baked** bundles and **git‑defined** bundles.

---

## 1) Prepare the bundle

1. Update the bundle code.
2. Decide the new **bundle id** (versioned id). Example:  
   `react@2026-02-10-02-44` or `app@2-0`.
3. Ensure the bundle entrypoint contains the agentic decorator (`@agentic_workflow` or factory).

---

## 2) Tag the bundle source

Tag the repo where the bundle lives:

```
git tag <bundle-tag>
git push origin <bundle-tag>
```

Use that tag/commit as `ref` in the bundles descriptor.  
**Branch refs are for dev only** and require `BUNDLE_GIT_ALWAYS_PULL=1`.

---

## 3) Update bundles.yaml

Edit `bundles.yaml` in the **customer repo** and add/update the bundle entry.

### Example (bundle inside monorepo)

```yaml
bundles:
  version: "1"
  default_bundle_id: "react@2026-02-10-02-44"
  items:
    - id: "react@2026-02-10-02-44"
      name: "ReAct (example)"
      repo: "git@github.com:kdcube/kdcube-ai-app.git"
      ref: "v0.3.2"
      subdir: "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles"
      module: "react@2026-02-10-02-44.entrypoint"
```

**Rule:** `subdir` points to the **parent bundles directory** and `module` includes the bundle folder
(`id.entrypoint`). Keep `module` aligned with your local path convention.

---

## 4) Configure bundle props (optional)

Bundles may require **runtime props** (for example: knowledge repo + docs root).
You can define props directly in `bundles.yaml` **per bundle item**:

```yaml
bundles:
  version: "1"
  items:
    - id: "react@2026-02-10-02-44"
      repo: "git@github.com:kdcube/kdcube-ai-app.git"
      ref: "v0.3.2"
      subdir: "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles"
      module: "react@2026-02-10-02-44.entrypoint"
      config:
        knowledge:
          repo: "git@github.com:kdcube/kdcube-ai-app.git"
          ref: "v0.3.2"
          docs_root: "app/ai-app/docs"
          src_root: "app/ai-app/services/kdcube-ai-app/kdcube_ai_app"
          deploy_root: "app/ai-app/deployment"
          validate_refs: true
```

**Ref props** are resolved at deployment time:
- `env:NAME` → environment variable `NAME`
- `file:/path/to/secret` → file contents

Any string **without** these prefixes is treated as a literal value.

You can also override props at runtime via the Admin API:

```
POST /admin/integrations/bundles/<bundle_id>/props
{
  "tenant": "<tenant>",
  "project": "<project>",
  "op": "merge",
  "props": { ... }
}
```

---

## 5) Decide delivery mode

### A) Baked bundles (copy into image)
- CI copies `<subdir>/<id>` into `/bundles/<id>`.
- Runtime descriptor uses:
  - `path=/bundles`
  - `module=<id>.entrypoint`

### B) Git‑defined bundles (clone at runtime)
- Runtime descriptor uses:
  - `repo`, `ref`, `subdir`, `module`
- Proc must have git enabled and SSH keys (if private).

---

## 6) Deploy with env reset (optional)

If you need to **override existing Redis registry** on deploy:

```
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

Apply for one rollout, then return to `0`.

### What is the source of truth?

- **Redis is the runtime source of truth.**
- The release/bundle descriptor is **only applied to Redis** when
  `BUNDLES_FORCE_ENV_ON_STARTUP=1` (one‑time overwrite, guarded by a Redis lock).
- If `BUNDLES_FORCE_ENV_ON_STARTUP=0`, Redis stays as‑is; the descriptor is only
  used to seed Redis when no registry exists.

### Do admin + example bundles stay even if not in the descriptor?

Yes:

- **Admin bundle** is always injected and cannot be removed.
- **Example bundles** are merged if `BUNDLES_INCLUDE_EXAMPLES=1` (default).
  Set `BUNDLES_INCLUDE_EXAMPLES=0` to suppress them.

### Bundle secrets + sidecar tokens

Bundle secrets can be updated at runtime (admin UI) and fetched long after
startup. When using `bundles.secrets.yaml` with the local secrets sidecar,
keep the read tokens **non‑expiring**:
- `SECRETS_TOKEN_TTL_SECONDS=0`
- `SECRETS_TOKEN_MAX_USES=0`

These should be set in the workdir `.env` so `get_secret()` continues to work
for bundle secrets over time.

Bundle secrets are **write‑only**; admin UI shows key names only, never values.
If secrets are provisioned via `bundles.secrets.yaml`, the CLI also stores
`bundles.<bundle_id>.secrets.__keys` in the secrets sidecar so the UI can show
the keys list.

---

## 7) Validate

- Check the bundle registry in `/admin/integrations/bundles`.
- Run a test chat with `agentic_bundle_id=<your bundle id>`.
- Verify streaming and steps appear in the client.
