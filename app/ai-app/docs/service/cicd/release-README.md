---
id: ks:docs/service/cicd/release-README.md
title: "Release"
summary: "Monorepo release guide driven by release.yaml: CI build/publish and deployment-time descriptor usage."
tags: ["service", "cicd", "release", "versioning", "git", "images", "bundles", "registry"]
keywords: ["release.yaml", "platform.ref", "git tag", "image tags", "BUNDLES_FORCE_ENV_ON_STARTUP", "BUNDLE_GIT_REDIS_LOCK", "monorepo versioning", "PyPI"]
see_also:
  - ks:docs/service/cicd/release-bundle-README.md
  - ks:docs/service/cicd/release-descriptor-README.md
  - ks:docs/service/cicd/custom-cicd-README.md
  - ks:docs/service/cicd/cli-README.md
---
# Release + Versioning

We use **one unified version** for the monorepo (platform + SDK) until the SDK is split.

---

## 1) Version Source

**Single source of truth:** `release.yaml`

```
platform:
  ref: "2026.3.4.1716"
```

Notes:
1. `platform.ref` becomes the **git tag**.
2. `platform.ref` is also used as the **image tag**.
3. `platform.ref` must be **PEP440‑compatible** so the CLI can be published to PyPI.

---

## 2) Tagging Convention

Release tags use **exactly** the value of `platform.ref`.

Example:

```
2026.3.4.1716
```

---

## 3) Image Tags

Images are published **only** with the release tag:

```
kdcube-chat-ingress:2026.3.4.1716
```

---

## 4) CI: Build + Publish (GitHub Actions)

Suggested flow (platform team):

1. Create a release branch (e.g. `release/2026.3.4.1716`)
2. Update `release.yaml` and set `platform.ref`
   - Bundles can be empty; the CLI can seed sample bundles on install
3. Open PR and merge to `main`
4. GitHub Actions does the rest:
   - Reads `platform.ref`
   - Validates `platform.ref` as **PEP440** (required by PyPI)
   - Creates git tag `platform.ref`
   - Builds & pushes images to dockerhub with that tag
   - Builds & publishes the CLI with that exact version

Notes:
- The release branch name is a **convention only** (e.g., `release/<version>`). CI does not require it.
- The CI workflow that runs automatically on merge is `release-kdcube-platform.yml`.
- The standalone CLI workflow (`publish-kdcube-cli.yml`) is **manual** and useful for re‑publishing CLI only.

---

### CI Prerequisites (Secrets + Permissions)

**GitHub Secrets (Repo Settings → Secrets and variables → Actions):**
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`
- `DOCKERHUB_NAMESPACE` (optional; defaults to username)
- `PYPI_API_TOKEN` (only if not using Trusted Publisher)

**Where to get them:**
- DockerHub: Account Settings → Security → New Access Token
- PyPI: Account Settings → API tokens → New token

**PyPI Trusted Publisher (recommended):**
- PyPI project → Publishing → Add Trusted Publisher
- Select GitHub, repo, workflow (`.github/workflows/publish-kdcube-cli.yml`)
- This removes the need for `PYPI_API_TOKEN` (uses `id-token: write`)

---

## 5) Deployment-Time Descriptor Usage

The processor consumes a **runtime bundle descriptor** (`AGENTIC_BUNDLES_JSON`).
That descriptor has the shape which resembles the release.yaml descriptor (its `bundles` vertical).
Example of the release descriptor shape 
```yaml
platform:
  repo: "kdcube-ai-app"
  ref: "2026.3.4.1716"          # tag

bundles:
  default_bundle_id: "react@2026-02-10-02-44"
  items:
    - id: "app@2-0"
      name: "Customer App"
      repo: "git@github.com:org/customer-repo.git"
      ref: "bundle-v2026.02.22"
      subdir: "service/bundles"
      module: "app@2-0.entrypoint"
```
During deployment, ensure one of these:

- **Baked bundles:** CI generates `AGENTIC_BUNDLES_JSON` (path `/bundles/...`) and injects it into proc.
- **Git bundles:** CI injects `AGENTIC_BUNDLES_JSON` with `repo/ref/subdir`.

If you need to **override existing Redis registry**, deploy proc with:

```
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

Then turn it off after rollout.

For git bundles in multi‑replica deployments, enable:

```
BUNDLE_GIT_REDIS_LOCK=1
```

This makes each replica pull **once** on startup (no cross‑replica contention).

---

## 6) Build Inputs (What CI Builds)

Images are built from:
`deployment/docker/all_in_one_kdcube`

Dockerfiles:
- `Dockerfile_Ingress`
- `Dockerfile_Chatproc`
- `Dockerfile_Exec`
- `Dockerfile_Metricservice`
- `Dockerfile_PostgresSetup`
- `Dockerfile_ProxyLogin`
- `Dockerfile_ProxyOpenResty`
- `Dockerfile_UI`

---

## 7) When to Split SDK Versioning

Only split when:

- SDK cadence diverges from platform
- Compatibility matrix is required

Until then, **keep unified versioning**.

---

## 8) CLI (immediate use cases)

The CLI lives at:
`services/kdcube-ai-app/kdcube_cli`

Immediate use cases to support:

1. **Validate release descriptor**
   - `kdcube release validate --file release.yaml`
2. **Render runtime bundle registry**
   - `kdcube release render-bundles --file release.yaml`
   - Output `AGENTIC_BUNDLES_JSON` payload for proc
3. **Generate env files**
   - Uses `deployment/docker/all_in_one_kdcube/sample_env` as the reference
   - `kdcube env init --preset all-in-one --out ./env`
4. **Seed sample bundles**
   - For local/dev installs when `release.yaml` has no bundles
   - `kdcube bundles seed --preset samples`
5. **Doctor / verify**
   - Verify required paths + env variables
   - `kdcube doctor --env ./env/.env.proc`

Note: The CLI does **not** replace CI. It provides a consistent local
experience that mirrors the release descriptor and the sample envs.
