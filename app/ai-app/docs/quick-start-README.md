---
id: repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
title: "Quick Start: Local KDCube"
summary: "Current local-start guide for booting KDCube with the CLI, descriptor sets, Docker Compose runtime, bundle registry, refresh flow, and bundle release loop."
tags: ["docs", "quickstart", "local", "docker-compose", "cli", "descriptors", "bundle"]
keywords: ["local quick start", "kdcube init start bundle reload", "docker compose startup", "descriptor driven install", "oss cli descriptors", "local bundle development", "run kdcube locally", "bundle config apply", "bundle release", "demo environment bootstrap", "kdcube copilot local"]
updated_at: 2026-05-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/environment/setup-for-dockercompose-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
---
# Quick Start: Local KDCube

This is the shortest current path to run KDCube locally and begin iterating on
bundles.

Use this page when you need to:

- start a local KDCube runtime from descriptors
- test reference bundles
- update bundle declarations and reload them
- rebuild or refresh the platform runtime
- understand where bundle release fits into the local loop

For the product overview, read
[What You Can Do With KDCube](what-you-can-do-with-kdcube-README.md).

## 1. Runtime Flow

```text
seed descriptors                         platform source/ref
assembly.yaml / bundles.yaml / ...       --path / --release / --latest
              |                                |
              +---------------+----------------+
                              v
            kdcube init --descriptors-location <dir> --build
                              |
                              v
       workdir/config/*.yaml + workdir/repo + generated compose/env files
                              |
                              v
                         kdcube start
                              |
                              v
                  browser UI + chat/API/widgets/MCP/jobs
```

The important boundaries:

- `tenant/project` is one isolated runtime environment.
- descriptors configure the environment and bundle registry.
- `kdcube init` creates or reseeds a runtime workdir.
- `kdcube start` starts the Docker Compose stack.
- `kdcube refresh` rebuilds or moves the platform runtime while preserving
  staged descriptors.
- `kdcube bundle config apply` updates staged bundle descriptors from a seed.
- `kdcube bundle reload <bundle_id>` refreshes bundle code/config in the
  running proc without rebuilding platform images.

## 2. Local Paths

Use generic paths like this:

```shell
export REPO="/abs/path/to/kdcube-ai-app"
export CLI_VENV="$REPO/app/venvs/ai-app/kdcube-cli"
export KDCUBE="$CLI_VENV/bin/kdcube"
export DESCRIPTORS="$REPO/app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli"

export TENANT="demo-tenant"
export PROJECT="demo-project"
export WORKDIR="$HOME/.kdcube/kdcube-runtime/${TENANT}__${PROJECT}"
```

The `oss-cli` descriptor seed is the default local demo/development set:

```text
app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli
```

The main descriptor files are:

| File | Owns |
| --- | --- |
| `assembly.yaml` | tenant/project, platform ref, auth, ports, infra, storage, ReAct/runtime settings |
| `secrets.yaml` | platform secrets such as model keys, infra passwords, git tokens |
| `gateway.yaml` | gateway capacity, throttling, process limits |
| `bundles.yaml` | bundle registry, source refs, bundle props, non-secret config |
| `bundles.secrets.yaml` | bundle-level secrets |

## 3. Install Or Refresh The CLI

Only do this when the CLI package changed or the venv is missing:

```shell
cd "$REPO"
mkdir -p app/venvs/ai-app

python3 -m venv "$CLI_VENV"
source "$CLI_VENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel
pip install -e "$REPO/app/ai-app/src/kdcube-ai-app/kdcube_cli"

"$KDCUBE" --help
deactivate
```

## 4. Init Once

Use `init` for first-time runtime creation or intentional reseeding. It stages
descriptors into `$WORKDIR/config`, stages the platform source, and optionally
builds images. It does not start containers.

```shell
"$KDCUBE" init \
  --path "$REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build
```

Add `--cors-origin https://<stable-public-origin>` when local provider
callbacks, webhooks, or Mini Apps must call your local runtime through public
HTTPS.

Use `--set-secret` only when you intentionally want CLI-provided values to
override descriptor secrets during init. Keep actual values in your shell or
secret store, not in docs:

```shell
"$KDCUBE" init \
  --path "$REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build \
  --set-secret services.openai.api_key "$OPENAI_API_KEY" \
  --set-secret services.anthropic.api_key "$ANTHROPIC_API_KEY" \
  --set-secret services.git.http_token "$GIT_HTTP_TOKEN"
```

## 5. Start, Inspect, Stop

```shell
"$KDCUBE" start --workdir "$WORKDIR"

"$KDCUBE" info --workdir "$WORKDIR"

"$KDCUBE" bundle status <bundle_id> \
  --workdir "$WORKDIR" \
  --json

"$KDCUBE" stop --workdir "$WORKDIR"
```

Open the UI URL printed by `start` or `info`.

## 6. Refresh Platform Runtime

Use `refresh` after platform code changes or when moving the runtime to another
platform ref. `refresh` preserves staged descriptors under `$WORKDIR/config`.

```shell
# Rebuild and restart from the platform source already recorded in the runtime.
"$KDCUBE" refresh --workdir "$WORKDIR" --build

# Copy the current checkout into the runtime first, then rebuild and restart.
"$KDCUBE" refresh --workdir "$WORKDIR" --path "$REPO" --build

# Move to a released platform ref, then rebuild and restart.
"$KDCUBE" refresh --workdir "$WORKDIR" --release <platform-ref> --build
```

Use `refresh --path "$REPO" --build` when your dirty local checkout should be
copied into `$WORKDIR/repo` before images are rebuilt.

## 7. Configure Bundles

When the seed descriptor files are the source of truth for bundle declarations,
props, or secrets, apply them to the active runtime:

```shell
"$KDCUBE" bundle config apply \
  --workdir "$WORKDIR" \
  --descriptors-location "$DESCRIPTORS" \
  --dry-run

"$KDCUBE" bundle config apply \
  --workdir "$WORKDIR" \
  --descriptors-location "$DESCRIPTORS" \
  --reload
```

`--dry-run` previews the staged changes. `--reload` asks the running proc to
reload changed bundle ids after staging.

Use a direct reload when the staged descriptor is already correct and only proc
cache/code visibility needs refreshing:

```shell
"$KDCUBE" bundle reload <bundle_id> --workdir "$WORKDIR"
```

Local-path bundle registration:

```shell
"$KDCUBE" bundle my.bundle@1-0 \
  --workdir "$WORKDIR" \
  --local-path "/abs/path/to/my.bundle@1-0" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" bundle reload my.bundle@1-0 --workdir "$WORKDIR"
```

Git-backed bundle registration:

```shell
"$KDCUBE" bundle my.bundle@1-0 \
  --workdir "$WORKDIR" \
  --git-repo "https://github.com/org/repo.git" \
  --git-ref "<bundle-release-ref>" \
  --git-subdir "src/my.bundle@1-0" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" bundle reload my.bundle@1-0 --workdir "$WORKDIR"
```

For local-path bundles, the CLI normalizes host paths to the container-visible
`/bundles/...` mount using `assembly.yaml -> paths.host_bundles_path`.

## 8. Release Bundle Content

The local runtime loop ends by releasing bundle content and patching descriptors
to the released ref.

Use the release guide:

[How To Release Bundle Content](sdk/bundle/build/how-to-release-bundle-content-README.md)

Usual flow:

```text
develop/test bundle locally
  -> update bundle docs, interface docs, config templates, release.yaml
  -> run bundle validation/tests
  -> commit/tag/push the bundle/content repo
  -> update deployment descriptors to the released git ref
  -> apply descriptors and reload, or deploy the target environment
```

## 9. What To Verify

Use the UI/admin surfaces and CLI to check:

- active tenant/project and platform ref
- registered bundles and default bundle
- bundle props and bundle secrets
- chat route loads the expected bundle
- widget routes build and serve static assets
- public APIs/webhooks are reachable when configured
- MCP endpoints are listed and callable when the bundle exposes them
- generated files and timeline artifacts appear with expected visibility

For `kdcube.copilot`, check that it answers questions from the KDCube docs and
that its docs MCP endpoint is exposed when configured.

## 10. Coding-Agent Bootstrap Prompt

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
with kdcube init/start/bundle reload using the active descriptor set.
```

## 11. Where To Go Next

| Need | Read |
| --- | --- |
| product overview | [what-you-can-do-with-kdcube-README.md](what-you-can-do-with-kdcube-README.md) |
| CLI details | [service/cicd/cli-README.md](service/cicd/cli-README.md) |
| descriptor semantics | [configuration/assembly-descriptor-README.md](configuration/assembly-descriptor-README.md), [configuration/bundles-descriptor-README.md](configuration/bundles-descriptor-README.md) |
| bundle authoring | [sdk/bundle/build/how-to-navigate-kdcube-docs-README.md](sdk/bundle/build/how-to-navigate-kdcube-docs-README.md) |
| release bundle content | [sdk/bundle/build/how-to-release-bundle-content-README.md](sdk/bundle/build/how-to-release-bundle-content-README.md) |
| reference implementation | [sdk/bundle/versatile-reference-bundle-README.md](sdk/bundle/versatile-reference-bundle-README.md) |
| public HTTPS callbacks | [service/cicd/ngrok-README.md](service/cicd/ngrok-README.md) |

## 12. Common Pitfalls

- Running `start` before `init`; initialize first.
- Rerunning `init` just to rebuild an existing runtime; use `refresh`.
- Forgetting `refresh --path "$REPO" --build` when testing dirty local platform
  source in an existing runtime.
- Expecting descriptor changes to affect a running proc without
  `bundle config apply --reload`, `bundle reload`, or restart.
- Writing bundle secrets into non-secret config.
- Using a host filesystem path inside runtime config where a container path is
  required; let the CLI normalize local bundle paths.
- Using browser/top-page origins in widgets instead of the KDCube frame/runtime
  origin.
- Expecting public provider callbacks to call `localhost`; use a stable HTTPS
  origin during local testing.
