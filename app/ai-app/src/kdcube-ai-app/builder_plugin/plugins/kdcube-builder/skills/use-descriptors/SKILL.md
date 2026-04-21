---
description: Point a KDCube profile at an existing directory with deployment descriptors (assembly.yaml, bundles.yaml, gateway.yaml, secrets.yaml) by creating a symlink.
argument-hint: <descriptors_dir> [--profile <name>]
disable-model-invocation: true
allowed-tools: Bash, Read, WebFetch
---

Run the plugin helper to link a profile's descriptors directory to `$ARGUMENTS`:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py use-descriptors $ARGUMENTS

Then print the output verbatim. Do not edit files in the target directory — `use-descriptors` only creates a symlink.

If the helper exits non-zero, stop and show the error to the user. Do not try to "fix" missing descriptor files by creating them.

## Descriptor reference

**Always read the matching descriptor doc before answering questions about, or editing,
any descriptor file. Every time, no exceptions — including "quick" edits and cases where
you think you remember the field shape.** Descriptor schemas change between releases; the
runtime accepts wrong-looking YAML and then silently serves nothing. Reading the doc is
cheaper than debugging a no-op reload.

This is especially critical when the bundle the user is configuring lives **outside**
the current runtime workdir / outside `host_bundles_path`. In that case the host path is
NOT the same as the path that goes into `bundles.yaml` — the latter is a **container
path** `/bundles/<relative-from-host_bundles_path>`. The documented fix
(`how-to-configure-and-run-bundle-README.md`, section "If you want to change the host
bundles root") is to widen `host_bundles_path` in `assembly.yaml` and rebuild with
`--build --upstream`. Read that section before editing anything.

When the user asks what a descriptor field means, or needs to edit one, fetch the matching doc
with `WebFetch` first. Paths are under
`https://github.com/kdcube/kdcube-ai-app/blob/main/`. If
`CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is set, read the same path locally instead — that is
an opt-in fast path, not the default.

- `app/ai-app/docs/service/configuration/service-config-README.md` — overview of the workdir
  layout and how descriptors interact.
- `app/ai-app/docs/service/configuration/assembly-descriptor-README.md` — `assembly.yaml`.
- `app/ai-app/docs/service/configuration/bundles-descriptor-README.md` — `bundles.yaml`
  (bundle registry, `path`, `module`, `config`, `role_models`).
- `app/ai-app/docs/service/configuration/bundles-secrets-descriptor-README.md` —
  `bundles.secrets.yaml`.
- `app/ai-app/docs/service/configuration/gateway-descriptor-README.md` — `gateway.yaml`.
- `app/ai-app/docs/service/configuration/secrets-descriptor-README.md` — `secrets.yaml`.

For the operational flow (where to put descriptors, when to reload, props/secrets changes),
read `app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md`.