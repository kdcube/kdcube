---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/release-README.md
title: "Release"
summary: "Monorepo release guide: release.yaml platform.ref sets the version; a hand-dispatched workflow builds and publishes; assembly.yaml and bundles.yaml are used at deployment time."
tags: ["service", "cicd", "release", "versioning", "git", "images", "bundles", "registry"]
keywords: ["release.yaml", "assembly.yaml", "bundles.yaml", "platform.ref", "workflow_dispatch", "git tag", "image tags", "BUNDLES_FORCE_ENV_ON_STARTUP", "BUNDLE_GIT_REDIS_LOCK", "monorepo versioning", "PyPI"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/release-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/custom-cicd-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
---
# Release + Versioning

We use **one unified version** for the monorepo (platform + SDK) until the SDK is split.

---

## 1) Version Source

**Source of truth:** `platform.ref` in `release.yaml` at the repository root.
`assembly.yaml` and `bundles.yaml` are deployment descriptors (section 5); the
release workflow does not read them.

```
platform:
  repo: "kdcube-ai-app"
  ref: "2026.3.4.1716"
  description: |
    What this release contains.
```

Notes:
1. `platform.ref` becomes the **git tag**, exactly as written.
2. `platform.ref` is also used as the **image tag**, exactly as written.
3. `platform.ref` must be **PEP 440-compatible**, so the CLI can be published
   to PyPI. The workflow checks this and stops otherwise.
4. Write it **zero-stripped**: `2026.3.4.1716`, not `2026.03.04.1716`.
   - PyPI stores the normalized form, and the workflow does not normalize. A
     ref with leading zeros gives a git and image tag that differ from the
     PyPI version.
   - The workflow's "already on PyPI" check compares the ref as written, so it
     never matches a padded ref.

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

Flow (platform team):

1. Open a pull request that sets `platform.ref` and its `description` in
   `release.yaml`. Review and merge it to `main` like any other change.
2. Start the release by hand: **Actions → Release KDCube Platform (Images + CLI)
   → Run workflow** on `main` (`release-kdcube-platform.yml`, `workflow_dispatch`
   only). A merge never starts a release, so the record can be corrected or
   prepared ahead.
3. The workflow then:
   - reads `platform.ref` from `release.yaml` and checks it as **PEP 440**;
   - creates the git tag `platform.ref` if it is missing;
   - builds and pushes the images to DockerHub with that tag, skipping an
     image whose tag already exists;
   - builds and publishes the CLI to PyPI with that version, skipping it if
     the version already exists;
   - checks that every image is pullable;
   - creates a GitHub Release for the tag if it is missing.

Notes:
- A release branch name such as `release/<version>` is a **convention only**.
  The workflow does not require it.
- The standalone CLI workflow (`publish-kdcube-cli.yml`) is also manual. It
  reads the same `release.yaml` and is for re-publishing the CLI alone.

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

The processor consumes the current **bundle descriptor authority**.
In local and compose-style deployments that authority is `bundles.yaml`.

Example (`bundles.yaml`):
```yaml
bundles:
  version: "1"
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

- **Baked bundles:** CI publishes bundle code under `/bundles/...` and stages matching `bundles.yaml` entries.
- **Git bundles:** CI stages `bundles.yaml` entries with `repo/ref/subdir`.

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
`src/kdcube-ai-app/kdcube_cli`

Immediate use cases to support:

1. **Validate assembly descriptor**
   - `kdcube release validate --file assembly.yaml`
2. **Render runtime bundle registry**
   - `kdcube release render-bundles --file bundles.yaml`
   - Output or stage the current `bundles.yaml` authority for proc
3. **Generate env files**
   - Uses `deployment/docker/all_in_one_kdcube/sample_env` as the reference
   - `kdcube env init --preset all-in-one --out ./env`
4. **Seed sample bundles**
   - For local/dev installs when `bundles.yaml` is empty
   - `kdcube bundles seed --preset samples`
5. **Doctor / verify**
   - Verify required paths + env variables
   - `kdcube doctor --env ./env/.env.proc`

Note: The CLI does **not** replace CI. It provides a consistent local
experience that mirrors the assembly descriptor and the sample envs.
