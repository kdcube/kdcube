# /kdcube-use-descriptors

Point a KDCube profile at an existing directory with deployment descriptors
(`assembly.yaml`, `bundles.yaml`, `gateway.yaml`, `secrets.yaml`) by creating a symlink.

Take `<descriptors_dir>` from the text the user typed after `/kdcube-use-descriptors`.
Optional `--profile <name>` flag.

Run:

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" use-descriptors <descriptors_dir> [--profile <name>]
```

Print the output verbatim. Do not edit files in the target directory — `use-descriptors`
only creates a symlink. If the helper exits non-zero, stop and show the error. Do not
try to "fix" missing descriptor files by creating them.

## Descriptor reference

**Always read the matching descriptor doc before answering questions about, or editing,
any descriptor file. Every time, no exceptions** — including "quick" edits and cases
where you think you remember the field shape. Descriptor schemas change between
releases; the runtime accepts wrong-looking YAML and then silently serves nothing.

This is especially critical when the bundle the user is configuring lives **outside**
the current runtime workdir / outside `host_bundles_path`. In that case the host path is
NOT the same as the path that goes into `bundles.yaml` — the latter is a **container
path** `/bundles/<relative-from-host_bundles_path>`. The documented fix is to widen
`host_bundles_path` in `assembly.yaml` and rebuild with `--build --upstream`.

**The plugin ships without docs — they are NOT on disk.** Fetch with a web-fetch tool
using the complete URLs below. Only fall back to local `Read` if `KDCUBE_REPO_ROOT` is
already set — in that case strip the raw.github prefix and read the repo-relative path.
Do not ask the user for a local repo.

**Header-first gate:** Before reading any descriptor doc in full, fetch it and read only
the title and first section. Confirm it covers the specific field or problem you need.
Then read the full content.

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/service-config-README.md` — workdir layout overview.
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/assembly-descriptor-README.md` — `assembly.yaml`.
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/bundles-descriptor-README.md` — `bundles.yaml` (registry, `path`, `module`, `config`, `role_models`).
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/bundles-secrets-descriptor-README.md` — `bundles.secrets.yaml`.
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/gateway-descriptor-README.md` — `gateway.yaml`.
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/service/configuration/secrets-descriptor-README.md` — `secrets.yaml`.

For the operational flow (where to put descriptors, when to reload, props/secrets
changes): `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md`.