---
id: ks:docs/service/cicd/release-bundle-README.md
title: "Release A Bundle"
summary: "Human and agent guide for releasing KDCube bundles: prepare bundle-local README/config/release files, commit/tag the content repository, then update deployment descriptors separately."
tags: ["service", "cicd", "release", "bundle", "content-release", "descriptors", "configuration"]
keywords: ["bundle release", "content release", "release.yaml", "bundle config shape", "bundles.yaml", "bundles.secrets.yaml", "bundle tag", "descriptor update", "bundle repository release"]
see_also:
  - ks:deployment/cicd/kdcube/procedures/content-release.md
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
---
# Release A Bundle

This page explains the bundle release workflow in simple terms.

Use it when you want to release bundle code/content, for example a content
repository that contains one or more KDCube bundles.

For the executable agent procedure, use:

- `deployment/cicd/kdcube/procedures/content-release.md`

## Short Version For Humans

A bundle release has two separate parts.

1. Release the bundle repository.
   - update the bundle docs/config shape/release notes
   - commit those changes
   - tag the bundle repository with the release version

2. Later, update deployment descriptors to use that release.
   - update `bundles.yaml` entries to point to the new `ref`
   - update any environment descriptors that should consume this bundle version
   - deploy or reload through the deployment/runtime procedure

Do not mix these by accident.

The bundle release says:

- "this bundle repository now has version X"

The deployment descriptor says:

- "this tenant/project/environment should run version X"

## What Must Be In A Releasable Bundle

Each releasable bundle should keep these files in the bundle root:

- `README.md`
  - explains what the bundle does, what config it reads, what secrets it needs,
    and how operators should run or validate it

- `release.yaml`
  - contains the bundle repository URL, release `ref`, and release notes
  - only `bundle.ref` is the release version; do not add a separate config version

- `config/bundles.yaml`
  - documents the non-secret `bundles.yaml` shape for this bundle
  - use placeholders or safe non-sensitive example values

- `config/bundles.secrets.yaml`
  - documents the bundle-scoped secrets shape
  - if the bundle has no bundle-scoped secrets, keep an explicit empty shape:

```yaml
secrets: {}
```

Never put real secrets in bundle-local config examples.

## What The Human Tells The Agent

Tell the agent:

- release version
- repository or repositories
- bundle ids
- whether to commit
- whether to tag
- whether to push

Example:

```text
Use deployment/cicd/kdcube/procedures/content-release.md.
Make content release 2026.4.29.1545 for the content bundle repo.
Release bundles sample-marketing@2-0 and user-mgmt@1-0.
Prepare descriptor and plan first.
```

If you already know you want to finish the release:

```text
Use deployment/cicd/kdcube/procedures/content-release.md.
Release version is 2026.4.29.1545.
Repository is content.
Bundles are sample-marketing@2-0 and user-mgmt@1-0.
Commit, tag, and push after I approve the plan.
```

## What The Agent Does

The agent follows a simple pipeline:

1. Create a descriptor.
2. Create a readable plan.
3. Wait for human approval.
4. Update bundle files.
5. Validate.
6. Commit only scoped release files.
7. Create the release tag.
8. Push commit and tag if requested.
9. Write the execution journal after every step.

The journal lives under:

```text
deployment/cicd/kdcube/cicd/content-release-history/<dd.mm.yyyy>/
```

The files are:

- `descriptor-<dd.mm.yyyy.hhmm>.yaml`
- `plan-<dd.mm.yyyy.hhmm>.log`
- `execute-<dd.mm.yyyy.hhmm>.yaml`

## Agent Rules

- Read existing `release.yaml` before changing it.
- Set `bundle.ref` to the requested release version.
- Do not invent a release version from the date unless the human asks.
- Do not stage unrelated files.
- Do not commit generated files, scratch files, or local secrets.
- If a bundle config changed, update `README.md`, `config/bundles.yaml`, and
  `config/bundles.secrets.yaml` in the same release.
- If the repo has unrelated dirty files, leave them untouched and mention them.

## Updating Deployment Descriptors

After the bundle repository is released, deployment descriptors can consume it.

For a git-defined bundle, update the bundle entry in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "my-bundle@1-0"
      name: "My Bundle"
      repo: "git@github.com:example/my-bundle-repo.git"
      ref: "2026.4.29.1545"
      subdir: "src/path/to/bundles"
      module: "my-bundle@1-0.entrypoint"
      config:
        my_bundle:
          example_setting: "value"
```

Rule:

- `subdir` points to the parent directory that contains the bundle folder
- `module` includes the bundle folder/module name and entrypoint
- `config` carries non-secret bundle props
- bundle secrets go to `bundles.secrets.yaml`, not `bundles.yaml`

## Delivery Modes

Most content releases use git-defined bundles:

- bundle source is in a separate repo
- release creates a git tag
- deployment descriptors use that tag as `ref`

Baked bundles are different:

- bundle code is copied into the platform image
- this is part of the platform release, not only content release

Do not treat baked bundle release and git-defined content release as the same
operation.

## Validation

Before tagging, validate what changed.

Minimum checks:

- YAML parses for `release.yaml`, `config/bundles.yaml`, and
  `config/bundles.secrets.yaml`
- changed Python files compile with `python3 -m py_compile`
- `git status` confirms only scoped files are staged

If the bundle has tests, run the relevant bundle tests before tagging.

Runtime validation happens after descriptors are updated and the environment is
started/reloaded. For local descriptor-backed runtimes, the targeted bundle
reload path applies the updated descriptor and evicts that bundle from proc
caches without rebuilding platform images:

- [cli-README.md#bundle-reload-flow](cli-README.md#bundle-reload-flow)
