---
id: ks:docs/service/cicd/release-README.md
title: "Release"
summary: "Monorepo release guide driven by release.yaml: git tags, image tags, CLI publish, and bundle-registry behavior."
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
  ref: "2026-03-04T17.16"
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
2026-03-04T17.16
```

---

## 3) Image Tags

Images are published **only** with the release tag:

```
kdcube-chat-ingress:2026.3.4.1716
```

---

## 4) Release Process (GitHub Actions)

Suggested flow (platform team):

1. Create a release branch (e.g. `release/2026-03-04T17.16`)
2. Update `release.yaml` and set `platform.ref`
   - Bundles can be empty; the CLI can seed sample bundles on install
3. Open PR and merge to `main`
4. GitHub Actions does the rest:
   - Reads `platform.ref`
   - Creates git tag `platform.ref`
   - Builds & pushes images to dockerhub with that tag
   - Builds & publishes the CLI with that version
   - Pushes git tag `platform.ref`

---

## 4.1) Bundle registry during release

The processor consumes a **runtime bundle descriptor** (`AGENTIC_BUNDLES_JSON`).
That descriptor has the shape which resembles the release.yaml descriptor (its `bundles` vertical).
During release, ensure one of these:

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

## 5) Release Process (CI)

CI logic (automated on merge to `main` when `release.yaml` changes):

1. Read `platform.ref` from `release.yaml`
2. Validate it is a valid git tag and **PEP440** (for PyPI)
3. Create and push git tag `platform.ref`
4. Build & push images tagged with `platform.ref` only.
5. Build & publish CLI version = `platform.ref`

### What changed

1. Release process now uses `release.yaml:platform.ref` as the **single source of truth**.
2. CI now:
   1. Reads `platform.ref` from `release.yaml`
   2. Validates it as **PEP440** for PyPI
   3. Creates git tag `platform.ref`
   4. Builds/pushes images with that tag
   5. Builds/publishes the CLI with that exact version
3. Product CI prerequisites were added to `custom-cicd-README.md`.

### Next steps

1. Update `release.yaml` to a **PEP440‑compatible** `platform.ref`.
2. Ensure GitHub secrets exist:
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`
   - `DOCKERHUB_NAMESPACE` (optional)
3. Configure PyPI Trusted Publisher for `kdcube-apps-cli` or add `PYPI_API_TOKEN`.

### Where to configure secrets (and how to obtain them)

**GitHub Secrets:**
- Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Add:
  - `DOCKERHUB_USERNAME` = your DockerHub username (or org bot user)
  - `DOCKERHUB_TOKEN` = DockerHub access token (Account Settings → Security → New Access Token)
  - `DOCKERHUB_NAMESPACE` = DockerHub org/namespace (optional; defaults to username)
  - `PYPI_API_TOKEN` = PyPI token (Account Settings → API tokens → New token)

**PyPI Trusted Publisher (recommended):**
- PyPI project → **Publishing → Add Trusted Publisher**
- Select GitHub, repo, workflow
- This removes the need for `PYPI_API_TOKEN` (uses `id-token: write`).

Images built from:
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

## 6) When to Split SDK Versioning

Only split when:

- SDK cadence diverges from platform
- Compatibility matrix is required

Until then, **keep unified versioning**.

---

## 7) CLI (immediate use cases)

The CLI lives at:
`services/kdcube-ai-app/kdcube_apps_cli`

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
