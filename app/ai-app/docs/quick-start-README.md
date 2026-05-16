---
id: ks:docs/quick-start-README.md
title: "Quick Start: Local KDCube"
summary: "Dense local-start guide for booting KDCube with the current CLI, descriptor sets, Docker Compose runtime, bundle registry, and the first bundle-development loop."
tags: ["docs", "quickstart", "local", "docker-compose", "cli", "descriptors", "bundle"]
keywords: ["local quick start", "kdcube init start reload", "docker compose startup", "descriptor driven install", "oss cli descriptors", "local bundle development", "run kdcube locally", "bundle reload", "demo environment bootstrap", "kdcube copilot local"]
updated_at: 2026-05-16
see_also:
  - ks:docs/what-you-can-do-with-kdcube-README.md
  - ks:docs/service/cicd/cli-README.md
  - ks:docs/service/environment/setup-for-dockercompose-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
---
# Quick Start: Local KDCube

This is the shortest current path to run KDCube locally and begin iterating on
bundles.

Use this page when you need to:

- start a local KDCube runtime from descriptors
- test the built-in reference/copilot bundles
- mount or reload a local bundle while developing
- give a coding agent the minimum correct local-run contract

For the product overview, read
[What You Can Do With KDCube](what-you-can-do-with-kdcube-README.md).

## 1. Mental Model

```text
descriptor set + platform source/ref
  -> kdcube init
  -> scoped runtime workdir
  -> Docker Compose stack
  -> ingress + proc + UI + infra
  -> bundle registry
  -> chat / API / widgets / MCP / jobs
```

The important boundaries:

- `tenant/project` is one isolated runtime environment
- a bundle is one application unit inside that environment
- descriptors configure the environment and bundle registry
- bundle code exposes surfaces through `entrypoint.py` decorators
- `kdcube init` stages runtime config; `kdcube start` starts containers;
  `kdcube reload <bundle_id>` refreshes bundle config/cache without a full
  platform rebuild

## 2. Choose A Descriptor Set

For local demo/development, prefer the current CLI descriptor seed:

```text
app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli
```

This set is intended for descriptor-driven local startup and reference-bundle
testing.

The main descriptor files are:

| File | Owns |
| --- | --- |
| `assembly.yaml` | tenant/project, platform ref, auth, ports, infra, storage, ReAct/runtime settings |
| `secrets.yaml` | platform secrets such as model keys, infra passwords, git tokens |
| `gateway.yaml` | gateway/runtime capacity and throttling |
| `bundles.yaml` | bundle registry and non-secret bundle config |
| `bundles.secrets.yaml` | bundle-level secrets such as bot tokens and webhook secrets |

Storage rule:

- `storage.kdcube: null` and `storage.bundles: null` means the CLI creates
  default tenant/project storage under the runtime data directory
- set explicit `file://...` or `s3://...` values only when you intentionally
  want custom storage locations

## 3. Init From Local Source

Use this when you are testing the current checkout, including uncommitted
platform changes:

```bash
export REPO="/abs/path/to/kdcube-ai-app"
export DESCRIPTORS="$REPO/app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli"
export WORKDIR="$HOME/.kdcube/kdcube-runtime/demo-tenant__demo-project"
export KDCUBE="$REPO/app/venvs/ai-app/kdcube-cli/bin/kdcube"

"$KDCUBE" init \
  --path "$REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build
```

Add `--cors-origin https://<stable-ngrok-domain>` when local provider callbacks
or Telegram Mini Apps must call your local runtime through a public HTTPS
origin.

Use `--set-secret` only when you want to override descriptor secrets during
init:

```bash
"$KDCUBE" init \
  --path "$REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build \
  --set-secret services.openai.api_key "$OPENAI_API_KEY" \
  --set-secret services.anthropic.api_key "$ANTHROPIC_API_KEY" \
  --set-secret services.git.http_token "$GIT_HTTP_TOKEN" \
  --set-secret git.http_token "$GIT_HTTP_TOKEN"
```

`init --build` stages the runtime and builds local Docker images. It does not
start containers.

## 4. Start, Inspect, Stop

```bash
"$KDCUBE" start --workdir "$WORKDIR"
"$KDCUBE" info --workdir "$WORKDIR"
"$KDCUBE" stop --workdir "$WORKDIR"
```

Open the UI URL printed by `start` or `info`.

Default local descriptor context for the `oss-cli` seed:

- tenant: `demo-tenant`
- project: `demo-project`

If you changed only bundle descriptors or bundle source references, reload the
bundle. If you changed platform code that is baked into images, rerun
`init --build` and restart.

## 5. Bundle Development Loop

Typical loop:

1. edit bundle code or bundle config
2. update the active staged `bundles.yaml` through descriptors or CLI
3. reload the bundle
4. test through chat/API/widget/MCP

Reload:

```bash
"$KDCUBE" reload <bundle_id> --workdir "$WORKDIR"
```

Example built-in bundle ids:

```text
kdcube.copilot@2026-04-03-19-05
versatile@2026-03-31-13-36
```

Local-path bundle registration:

```bash
"$KDCUBE" bundle my.bundle@1-0 \
  --workdir "$WORKDIR" \
  --local-path "/abs/path/to/my.bundle@1-0" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" reload my.bundle@1-0 --workdir "$WORKDIR"
```

Git-backed bundle registration:

```bash
"$KDCUBE" bundle my.bundle@1-0 \
  --workdir "$WORKDIR" \
  --git-repo "https://github.com/org/repo.git" \
  --git-ref "2026.5.16.001" \
  --git-subdir "src/my.bundle@1-0" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" reload my.bundle@1-0 --workdir "$WORKDIR"
```

Bundle source paths are interpreted by the CLI. For local-path bundles, the CLI
normalizes host paths to the container-visible `/bundles/...` mount using
`assembly.yaml -> paths.host_bundles_path`.

## 6. What To Verify In A Local Demo

Use the UI/admin surfaces to check:

- the active tenant/project and platform ref
- registered bundles and default bundle
- bundle props and bundle secrets
- chat route loads the expected bundle
- widget routes build and serve static assets
- public APIs/webhooks are reachable when configured
- MCP endpoints are listed and callable when the bundle exposes them
- generated files and timeline artifacts appear with expected visibility

For `kdcube.copilot`, the important demo checks are:

- it can answer questions from the KDCube docs
- its docs MCP endpoint is exposed by the bundle when configured
- its Memory widget opens when memory is enabled
- its Telegram Mini App routes work when Telegram config/secrets are set

## 7. Coding-Agent Bootstrap Prompt

Use this as a compact instruction when asking a coding agent to build or wrap a
bundle locally:

```text
You are building a KDCube bundle. First read:
- docs/what-you-can-do-with-kdcube-README.md
- docs/quick-start-README.md
- docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
- docs/sdk/bundle/build/how-to-test-bundle-README.md
- docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
- docs/sdk/bundle/build/how-to-write-bundle-README.md
- docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md

Use the versatile reference bundle for patterns. Keep product logic separate
from the KDCube adapter. Expose surfaces through entrypoint.py decorators.
Map config to bundles.yaml and secrets to bundles.secrets.yaml. Test locally
with kdcube init/start/reload using the active descriptor set.
```

## 8. Where To Go Next

| Need | Read |
| --- | --- |
| product overview | [what-you-can-do-with-kdcube-README.md](what-you-can-do-with-kdcube-README.md) |
| CLI details | [service/cicd/cli-README.md](service/cicd/cli-README.md) |
| descriptor semantics | [configuration/assembly-descriptor-README.md](configuration/assembly-descriptor-README.md), [configuration/bundles-descriptor-README.md](configuration/bundles-descriptor-README.md) |
| bundle authoring | [sdk/bundle/build/how-to-navigate-kdcube-docs-README.md](sdk/bundle/build/how-to-navigate-kdcube-docs-README.md) |
| reference implementation | [sdk/bundle/versatile-reference-bundle-README.md](sdk/bundle/versatile-reference-bundle-README.md) |
| public HTTPS callbacks | [service/cicd/ngrok-README.md](service/cicd/ngrok-README.md) |

## 9. Common Pitfalls

- Running `start` before `init`; initialize first.
- Forgetting `--path "$REPO"` when testing dirty local platform source.
- Expecting descriptor changes to affect a running proc without reload/restart.
- Writing bundle secrets into non-secret config.
- Using a host filesystem path inside runtime config where a container path is
  required; let the CLI normalize local bundle paths.
- Using browser/top-page origins in widgets instead of the KDCube frame/runtime
  origin.
- Expecting public provider callbacks to call `localhost`; use a stable HTTPS
  origin such as ngrok during local testing.
