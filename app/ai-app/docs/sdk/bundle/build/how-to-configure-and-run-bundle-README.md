---
id: ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
title: "How To Configure And Run A Bundle"
summary: "Operational guide for configuring bundles.yaml, bundles.secrets.yaml, assembly.yaml, and the CLI runtime loop for local bundle development."
tags: ["sdk", "bundle", "configuration", "runtime", "cli", "bundles.yaml"]
keywords: ["how to configure bundle", "bundle path", "module entrypoint", "host_bundles_path", "kdcube build upstream", "kdcube info"]
see_also:
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
---
# How To Configure And Run A Bundle

This page is the operational contract for:

- `assembly.yaml`
- `bundles.yaml`
- `bundles.secrets.yaml`
- `kdcube --build --upstream`
- `kdcube --info`

It is not a conceptual overview.
It is the exact local-development workflow.

## 1. Decide Which Runtime You Are Testing

There are two different cases.

### A. Reuse an existing initialized runtime

Use the existing runtime if your goal is to test code or descriptor changes in the runtime you already have.

Typical command:

```bash
pip install -e /abs/path/to/kdcube_cli
kdcube --workdir ~/.kdcube/kdcube-runtime/my_runtime --build --upstream
```

What this does:

- reuses `workdir/config/install-meta.json`
- reuses `workdir/config/*.yaml`
- pulls the newer upstream repo state
- rebuilds from that repo state
- does not reseed default descriptors

This is the correct mode when the runtime is already initialized.

### B. Start with a fresh empty runtime

Use a fresh workdir only if your goal is to test the first-run bootstrap behavior.

Typical command:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime-test-default
```

This is the path that exercises the local-first descriptor seeding flow.

## 2. Change Local Bundle Roots In An Existing Runtime

If the runtime already exists, change its descriptor files directly under:

```text
<workdir>/config/
```

The two files that matter first are:

- `assembly.yaml`
- `bundles.yaml`

### A. Host unmanaged bundles root

If your local source tree lives under `/Users/you/src`, set:

```yaml
# <workdir>/config/assembly.yaml
paths:
  host_bundles_path: "/Users/you/src"
```

Meaning:

- host path `/Users/you/src` is the common parent root on your machine
- runtime path `/bundles` is the container-visible root

This host root must be broad enough to span every unmanaged local bundle you want to mount.

### B. Managed bundles root

Keep managed bundles separate from local unmanaged bundles.

Typical runtime-local value:

```yaml
# <workdir>/config/assembly.yaml
paths:
  host_managed_bundles_path: "~/.kdcube/kdcube-runtime/my_runtime/data/managed-bundles"
```

Managed bundles are:

- git-resolved bundles
- built-in example bundles materialized by the platform

Unmanaged bundles are:

- local path bundles that point into your source tree

Do not point `host_bundles_path` at the managed cache.
Do not point `host_managed_bundles_path` at your source tree.

## 3. Define A Local Path Bundle Correctly

When a bundle should load directly from your local source tree, define it as a pure path bundle in `bundles.yaml`.

Correct shape:

```yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      name: "My Bundle"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
      config:
        feature_flag: true
```

Rules:

- `path` must be the container-visible path, not the host path
- `module` must match the module inside that bundle root
- if you use `path:` mode, remove `repo`, `ref`, and `subdir` from that same entry

Do not mix local-path and git fields on one bundle entry.

## 4. `path` And `module`: Exact Rule

There are exactly two valid forms.

### Preferred form: `path` is the actual bundle root

Use:

```yaml
path: /bundles/my-repo/src/my_bundle
module: entrypoint
```

This is the preferred form because it is explicit and unambiguous.

### Secondary form: `path` is the parent directory

Use:

```yaml
path: /bundles/my-repo/src
module: my_bundle.entrypoint
```

This is valid only when:

- `path` is the directory that contains `my_bundle`
- `my_bundle` is the real bundle directory name

### Form to avoid

Avoid:

```yaml
path: /bundles/my-repo/src/my_bundle
module: my_bundle.entrypoint
```

That may still load because of fallback behavior, but it is not the clean contract to depend on.

The clean rule is:

- if `path` is the actual bundle root, use `module: entrypoint`
- if `path` is the parent directory, use `module: <bundle-dir>.entrypoint`

## 5. Example: Host Root To Container Path

Suppose your local bundle directory on the host is:

```text
/Users/you/src/my-repo/src/my_bundle
```

and your runtime uses:

```yaml
paths:
  host_bundles_path: "/Users/you/src"
```

Then the container-visible path is:

```text
/bundles/my-repo/src/my_bundle
```

That is the path that must appear in `bundles.yaml`.

Do not put `/Users/you/src/...` into `bundles.yaml`.

## 6. Apply And Verify The Runtime

After editing the runtime descriptor files, rebuild or restart through the CLI:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/my_runtime --build --upstream
```

Then inspect the effective runtime mapping:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/my_runtime --info
```

That command should show the effective:

- host bundles path
- container bundles root
- host managed bundles path
- container managed bundles root
- descriptor files in use

If the runtime is already initialized, this command sequence does not reseed descriptors.
It reuses the descriptors already staged under `workdir/config/`.

## 7. Bundle Secrets

Non-secret config belongs in:

- `bundles.yaml`

Secrets belong in:

- `bundles.secrets.yaml`

Typical split:

```yaml
# bundles.yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
      config:
        api:
          header_name: "X-My-Bundle-Token"
```

```yaml
# bundles.secrets.yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      secrets:
        api:
          shared_token: "replace-me"
```

Use in code:

```python
header_name = self.bundle_prop("api.header_name", "X-My-Bundle-Token")
shared_token = get_secret("b:api.shared_token")
```

For the exact descriptor layouts, use:

- [bundles-descriptor-README.md](../../../service/configuration/bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](../../../service/configuration/bundles-secrets-descriptor-README.md)
- [assembly-descriptor-README.md](../../../service/configuration/assembly-descriptor-README.md)

## 8. Sharp Rules

Follow these rules exactly:

- `assembly.yaml` owns the host root mappings
- `bundles.yaml` owns the concrete bundle entries
- `bundles.secrets.yaml` owns bundle secrets
- use `path + module` only for local unmanaged bundles
- use `repo + ref + subdir + module` for managed git bundles
- if `path` is the actual bundle root, use `module: entrypoint`
- if `path` is the parent directory, use `module: <bundle-dir>.entrypoint`
- do not keep `repo`, `ref`, or `subdir` on a local path bundle entry
- do not put host filesystem paths into `bundles.yaml`
- do not expect `--build --upstream` on an initialized runtime to reseed descriptors
