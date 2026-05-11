---
id: ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
title: "How To Release Bundle Content"
summary: "Optional but recommended Tier 1 lifecycle procedure for releasing bundle/content repositories: align bundle docs, config templates, release.yaml, validation, git commit/tag/push, and descriptor ref updates from a self-contained public bundle-builder workflow."
tags: ["sdk", "bundle", "release", "content", "lifecycle", "tier-1"]
keywords: ["bundle content release", "bundle release procedure", "release yaml", "bundle config templates", "bundle tag", "bundle descriptor ref", "agent release workflow", "optional release procedure", "bundle lifecycle maintenance"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
---
# How To Release Bundle Content

This is the public Tier 1 bundle lifecycle procedure for content or application
bundle repositories.

It is optional.
Use it when the user wants a bundle to become a pinned, repeatable release or
when a git-backed descriptor must point at a known ref.

It is also the recommended way to work when building a bundle from scratch:

- create the skeleton early
- keep config/docs/tests aligned while building
- validate before release
- only then commit, tag, push, and update runtime descriptors

Do not rely on another release procedure when using this page.
This page is the self-contained public bundle-builder procedure.

## 1. Release Decision With The User

Do not start a release just because code changed.

Before doing release actions, conclude these values with the user:

| Value | What to confirm |
| --- | --- |
| target bundle | bundle id and bundle directory |
| repository | local checkout and remote URL |
| release ref | exact tag/ref, for example `2026.5.2.1643` |
| release scope | what changed and what should be described |
| validation | which local and runtime checks are expected |
| git actions | whether to commit, tag, and push |
| descriptor update | which `bundles.yaml` should point at the new ref, if any |

If the user explicitly says to release, commit, tag, push, and update a named
descriptor, that is enough. Otherwise ask for the missing value before touching
git history.

## 2. Files That Must Stay Aligned

For every released bundle, check these files:

```text
<bundle>/
  README.md
  release.yaml
  config/
    bundles.template.yaml
    bundles.secrets.template.yaml
  docs/
    design/
    journal/
      journal.md
  tests/
```

Rules:

- `README.md` describes the current bundle behavior, surfaces, config, secrets,
  operational notes, and links to design/config/journal docs
- `release.yaml` names the release ref and describes what the release contains
- `config/bundles.template.yaml` documents non-secret deployment props
- `config/bundles.secrets.template.yaml` documents deployment-scoped bundle
  secrets, but never real secret values
- if the bundle has public/external users, docs explain the bundle user-scope
  model and do not imply every user must have a KDCube control-plane account
- personal OAuth tokens or user credentials are described as user-scoped runtime
  state/secrets, not committed deployment descriptors
- `docs/design/` reflects the implemented design, not only early notes
- if the bundle uses SDK integrations or solutions, `docs/design/` names those
  blocks and explains which product policy remains in the bundle
- `docs/journal/journal.md` records important implementation and release
  decisions
- tests prove the bundle contract before release
- if the bundle has tools that produce user-visible files or attachments,
  docs/tests describe the `ret.artifact_type == "files"` protocol or the
  `host_files(...)` tool-side hosting path, including the prepared runtime
  context required for `host_files(...)`

For a brand-new bundle, `release.yaml` may be empty during skeleton work. Fill
it only when the user agrees to cut a release.

## 3. Minimal `release.yaml`

Use this shape unless the bundle repo already has a stricter local format:

```yaml
bundle:
  repo: "https://github.com/org/applications.git"
  ref: "2026.5.2.1643"
  description: |
    Release for my.bundle@1-0.

    Highlights

    - Added or changed one important behavior.
    - Added or changed one important integration.
    - Added tests or validation for the release.

    Known follow-ups

    - Name real remaining work, or write "None known."
```

The `bundle.ref` value must match the git tag or git ref that deployment
descriptors will use.

## 4. Validation Before Release

Use the working environment from
[how-to-test-bundle-README.md#1a-working-environment-for-agents](how-to-test-bundle-README.md#1a-working-environment-for-agents).

At minimum:

```bash
git diff --check -- <bundle-path>
```

```bash
PYTHONPATH=<kdcube-source-root> \
  <runtime-python> -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path <bundle-path>
```

```bash
PYTHONPATH=<kdcube-source-root> \
  <runtime-python> -m pytest -q <bundle-path>/tests
```

If the bundle is or may be git-managed, run the import-shape checks from
[how-to-test-bundle-README.md#syntax-and-imports](how-to-test-bundle-README.md#syntax-and-imports).

If runtime behavior changed, also run the relevant manual/API/widget checks
from the test guide.

If a release changes a buildable widget source folder or widget build
configuration, also validate the widget build contract from
[how-to-test-bundle-README.md#52b-source-folder-widget-build-contract](how-to-test-bundle-README.md#52b-source-folder-widget-build-contract).
At minimum, run the widget build with an explicit temporary `OUTDIR` and confirm
`index.html` is written there.

If a release changes generated standalone HTML, browser-facing widget behavior,
or ReAct/browser-tool behavior, include the relevant browser-tool smoke test in
the validation notes. Prefer DOM/status checks and reserve screenshots for
visual assertions that cannot be proven from text/DOM state.

If a release changes descriptor-selected bundle refs, update the active
environment descriptor rather than only the bundle repo. For cloud deployment
descriptors this usually means the git-backed `bundles.yaml` entry that contains
the bundle repo, subdir/module, and released `ref`.

If a release changes file-producing tools or attachment materialization, also
validate the tool result contract from
[how-to-test-bundle-README.md#1c-react-toolskill-checks](how-to-test-bundle-README.md#1c-react-toolskill-checks).
Confirm the runtime produces hosted file metadata, and include the isolated
runtime path when the tool can execute there.

If user identity or external auth changed, validate both:

- the KDCube-authenticated path
- each public/external path, such as Telegram Mini App/webhook mapping to the
  resolved bundle user scope

If a validation cannot be run, record that explicitly in the release notes or
journal. Do not silently treat skipped validation as passing validation.

## 5. Git Release Steps

Only do these steps after the user has agreed to the release.

1. Stage only release-owned files.
2. Check the staged diff.
3. Commit.
4. Tag the commit with the agreed release ref.
5. Push the branch.
6. Push the tag.

Example:

```bash
git add <bundle-path>/README.md \
  <bundle-path>/release.yaml \
  <bundle-path>/config/bundles.template.yaml \
  <bundle-path>/config/bundles.secrets.template.yaml \
  <bundle-path>/docs \
  <bundle-path>/tests \
  <bundle-path>/entrypoint.py
git diff --cached --stat
git commit -m "Release my bundle 2026.5.2.1643"
git tag 2026.5.2.1643
git push origin main
git push origin 2026.5.2.1643
```

Do not stage unrelated repository changes.
Do not put real secrets into committed bundle templates.

## 6. Descriptor Ref Update

After a git-backed bundle release, update the environment descriptor that should
consume the new release:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      repo: "https://github.com/org/applications.git"
      ref: "2026.5.2.1643"
      subdir: "src"
      module: "my.bundle@1-0.entrypoint"
```

Descriptor ownership matters:

- bundle-local `config/bundles.template.yaml` documents the expected shape
- active environment `bundles.yaml` selects the actual deployed ref
- local seed/source descriptors may be gitignored developer config
- staged runtime descriptors under a runtime workdir may be the active local
  authority after initialization

Use [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
to decide which descriptor copy should be edited and whether reload or restart
is needed.

## 7. Done Criteria

A bundle content release is done when:

- release-owned files are aligned
- validation results are known
- `release.yaml` points at the released ref
- the release commit exists
- the tag exists locally and remotely, if push was requested
- any requested environment descriptor points at the new ref
- the user knows whether reload, restart, or descriptor restaging is needed
