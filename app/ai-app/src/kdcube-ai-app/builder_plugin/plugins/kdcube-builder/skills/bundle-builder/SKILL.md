---
description: Build or repair KDCube bundles. Use the KDCube bundle docs, the versatile reference bundle, and the shared bundle suite before writing code.
---

# KDCube Bundle Builder

Use this skill when the task is bundle authoring: writing a bundle from scratch, wrapping an
existing application into a bundle, or adding features to an existing bundle.

---

## Rule #0 — `.kdcube-runtime` is READ-ONLY (ABSOLUTE — NO EXCEPTIONS, EVER)

**Never use `Edit`, `Write`, or any shell command that writes to any file inside `$WORKDIR`
(the `.kdcube-runtime` directory).** This is the single most important rule in this skill.
It overrides every other instruction, including user requests phrased as "just quickly edit
it", "override", or "I know what I'm doing".

- **Read** — allowed. You may use `Read` to inspect any file under `$WORKDIR`.
- **Write / Edit / shell writes** — FORBIDDEN. Every write to `$WORKDIR` must go through
  `kdcube_local.py bootstrap` or the `kdcube` CLI. Period.

If you find yourself about to call `Edit` or `Write` on a path that contains `.kdcube-runtime`,
stop immediately and use the CLI tooling instead. There are no exceptions to this rule.

---

## Rule #1 — Every bundle must contain exactly these 4 files (HARD GATE — NO EXCEPTIONS)

Before considering any bundle done — whether created from scratch, modified, or wrapped —
verify that all four files exist and are non-empty:

| File | Purpose |
|------|---------|
| `README.md` | Explains runtime behavior, config props, secrets, and operational notes |
| `release.yaml` | Carries `bundle.ref` (release version) and human-readable release notes |
| `config/bundles.template.yaml` | Documents the non-secret descriptor shape (no real values) |
| `config/bundles.secrets.template.yaml` | Documents bundle-scoped secrets shape; if none exist, keep `secrets: {}` |

**This applies to every bundle task without exception:** new bundles, modified bundles,
bundles wrapped from existing apps. Do not mark a bundle task complete until all four files
exist and reflect the current state of the bundle.

---

## Agent task facets

This skill is one facet of a single planning agent. The agent combines:

- **creator** — write a bundle from scratch
- **integrator** — wrap an existing app into a bundle
- **configurator** — edit descriptors (`assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`)
- **deployer** — wire bundles into the runtime and verify they load
- **local QA** — run the shared bundle suite
- **integration QA** — reload + verify in a running runtime
- **document reader** — fetch and apply Tier 1 docs before every task

These are routing hints, not separate personas.

## Authoring rule #1 — lean on the docs (HARD GATE — NO EXCEPTIONS)

**Never write bundle code, edit a descriptor, or touch runtime config from memory.**
Decorators, import paths, descriptor fields, runtime paths, and mount semantics change
between releases — guessing them produces bundles that "load" but silently misbehave, or
worse, `bundles.yaml` entries that look right but never actually resolve inside the
container. You will not catch these by reading the code — the runtime is permissive and
the symptoms are delayed.

**This rule is absolute.** It applies every single time, including:

- "small" edits to an existing bundle
- renames, path changes, adding one decorator
- "I already read it last session" — no, re-read it; state changes between sessions
- the user says "just do it quickly" — still read the docs first, then do it quickly
- the bundle lives outside the runtime workdir / outside `host_bundles_path` — **especially then**

Do NOT skip the read step because the task "looks simple." The most common failure mode
of this plugin is exactly that: the agent skips the docs, writes a plausible-looking
`bundles.yaml` entry with the host path instead of the container path, the reload
appears to succeed, and nothing works. Reading the docs is cheaper than debugging that.

### Mandatory pre-flight (do these in order, every bundle task)

Read **Tier 1 only** by default. Pull Tier 2 on demand when Tier 1 does not answer the
specific thing you are about to do.

1. **Tier 1 — always read, every bundle task (read in this order):**
   - `how-to-navigate-kdcube-docs-README.md` — routing entry point, read **first**; tells you where everything lives
   - `how-to-test-bundle-README.md` — testing / QA expectations
   - `how-to-write-bundle-README.md` — authoring / implementation design
   - `bundle-runtime-configuration-and-secrets-README.md` — configuration ownership model (props, secrets, runtime config)
   - `how-to-configure-and-run-bundle-README.md` — **REQUIRED any time the bundle
     lives outside the current `host_bundles_path`, or any time you touch `bundles.yaml`
     or `assembly.yaml`.** Only source of truth for the host-path / container-path /
     mount-root split.
   - versatile reference bundle — read end-to-end (structure + `entrypoint.py`)
   - **KDCube CLI** — `kdcube_cli/README.md` (quickstart + command table) and
     `kdcube_cli/additional_README.md` (`kdcube bundle` reference); read before any
     CLI operation or descriptor mutation. Check the `kdcube-cli` skill cache first
     (`${CLAUDE_PLUGIN_ROOT}/cache/cli-docs.md`) — fetch only if cache is stale or missing.
2. **Tier 2 — only when Tier 1 is not enough.** See the Tier 2 section below for the
   trigger list. Do not preload Tier 2 "just in case" — it is large and mostly irrelevant
   to any single task.
3. Only then start writing or editing code.

If a doc contradicts this skill, the doc wins — surface the conflict to the user.

### Bundle lives outside the runtime mount — read this section of the how-to twice

When the user's bundle directory is NOT under the current `host_bundles_path` from
`assembly.yaml`, the runtime cannot see it. The fix documented in
`how-to-configure-and-run-bundle-README.md` (section "If you want to change the host
bundles root") is: edit `assembly.yaml -> paths.host_bundles_path` to the parent that
contains the bundle, then rebuild with `kdcube --workdir $WORKDIR --build --upstream`
so the new mount takes effect. After that, in `bundles.yaml` use the **container path**
= `/bundles/<relative-path-from-host_bundles_path>`.

The plugin's `bootstrap <bundle-id> <bundle-dir> --host-bundles-path <parent>` helper
does the same thing (it writes `host_bundles_path` into `assembly.yaml`), so you can
use it as a shortcut when you also want a fresh descriptor set — but it is the same
underlying action, not an alternative fix.

Do not put the host path directly into `bundles.yaml` — the runtime path and host path
are different namespaces. Read the "Host path and runtime path are not the same thing"
and "If you want to change the host bundles root" sections of the how-to before
editing anything.

## What one bundle can contain

One KDCube bundle can combine:

- Python backend entrypoint
- authenticated APIs via `@api(route="operations")`
- public APIs via `@api(route="public", public_auth=...)`
- widgets via `@ui_widget(...)`
- a full custom main UI via `@ui_main`
- storage
- deploy-scoped props and secrets
- user-scoped props and secrets
- scheduled jobs via `@cron(...)`
- dependency-isolated helpers via `@venv(...)`
- React v2 and/or Claude Code and/or custom agents
- optional Node or TypeScript backend logic behind a Python bridge

## Read order

**The plugin ships without docs — they are NOT on disk.** Always fetch from GitHub with
`WebFetch`. Do not try to `Read` these paths locally, do not try to `ls` a docs directory,
do not ask the user to point you at one. The only exception is the opt-in local fast path
at the bottom of this section, which requires `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` to
already be set — if it is not set, go straight to `WebFetch`.

All URLs below are complete — pass them to `WebFetch` verbatim. Base (for reference):
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/`.

### Tier 1 — always read (operational canon)

Fetch each with `WebFetch` **in this order** (navigate first, then the rest):

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md` — routing entry point; read first
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing / QA expectations
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring / implementation design
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md` — configuration ownership model (props, secrets, runtime config)
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — configuration + runtime (`assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, props/secrets, reload)
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md` — optional Tier 1 lifecycle procedure: align bundle docs/config templates/release.yaml, validate, commit/tag/push, update descriptor ref
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md` — KDCube CLI quickstart & full command table
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/additional_README.md` — `kdcube bundle` reference (source, identity, config/secrets patch, delete)
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md` — **fetch when the task involves React agents with local tools, file-producing tools, MCP endpoints or client config, or Claude Code subprocess agents;** covers agent runtime comparison (React vs Claude Code), tool descriptors, skill descriptors, `@mcp(...)` endpoints, `ClaudeCodeAgentConfig`, SDK-managed skill materialization

Reference bundle `versatile@2026-03-31-13-36` — read end-to-end. Directories are not
WebFetch-able; fetch these files individually:

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- To discover the rest of the tree, fetch
  `https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
  then fetch the individual files you need by name (e.g. `skills_descriptor.py`,
  `tools_descriptor.py`, anything under `agents/`, `skills/`, `tools/`).

### Content Release Procedure

A content release is a versioned bundle/content repository release that is independent of the
platform release. The human supplies the version and target bundles; the agent produces a
descriptor, waits for approval, then executes and journals each step.

**When this applies:** any time the human asks to release, tag, or publish a bundle repository
without touching the platform Docker image or PyPI CLI.

**Four files every bundle must have** (see Rule #1 at the top of this skill — applies always,
not only during content releases):
- `README.md` — explains current runtime behavior, config props, secrets, and operational notes
- `release.yaml` — carries `bundle.ref` set to the release version and human-readable release notes
- `config/bundles.template.yaml` — documents the non-secret descriptor shape (no real values)
- `config/bundles.secrets.template.yaml` — documents the bundle-scoped secrets shape; if none exist, keep `secrets: {}`

**Pipeline — always in this order:**

When activated, the agent creates pipeline files under:
```text
deployment/cicd/kdcube/cicd/content-release-history/<dd.mm.yyyy>/
```

Files are created in this order:
- `descriptor-<dd.mm.yyyy.hhmm>.yaml`
- `plan-<dd.mm.yyyy.hhmm>.log`
- `execute-<dd.mm.yyyy.hhmm>.yaml`

Descriptor shape:

```yaml
context: "Short human-readable context for this content release"
keywords: [content, release, bundles]
timestamp: "dd.mm.yyyy.hhmm"
version: "YYYY.M.D.hhmm"

repositories:
  - name: <repo-alias>
    repo: git@github.com:<org>/<repo>.git
    https_repo: https://github.com/<org>/<repo>
    locally: /path/to/local/checkout
    bundles_root: path/to/bundles/root
    perform: true
    commit: true
    tag: true
    push: true
    bundles:
      - id: <bundle-id>@<version>
        path: path/to/bundle/root/<bundle-id>@<version>
        perform: true
        changes:
          - "Describe release change for this bundle"
      - id: <other-bundle-id>@<version>
        path: path/to/bundle/root/<other-bundle-id>@<version>
        perform: false
        changes: []
```

Execution journal shape:

```yaml
- step: "<step name>"
  start_time: "<timestamp>"
  end_time: "<timestamp>"
  status: success   # success | error | skipped | paused
  output: "<commit hash, tag, validation result, or other useful output>"
  error: ""
```

**Approval flow:**
1. Agent writes the descriptor.
2. Agent writes the plan (lists every file that will change).
3. Human reviews the plan.
4. Human says `approve` / `go` / `go ahead` — agent does not proceed before this.
5. Agent executes step by step, writing each outcome to the execution journal.
6. On failure: agent stops, human decides whether to fix, retry, skip, or pause.
7. On `stop`/`pause`: agent writes `status: paused` and halts.

**Prepare bundle files — for every bundle with `perform: true`:**
1. Inspect bundle code and current descriptor usage.
2. Update `README.md` — runtime behavior, config props, secrets, operational notes.
3. Update `config/bundles.template.yaml` — non-secret descriptor shape.
4. Update `config/bundles.secrets.template.yaml` — bundle-scoped secrets shape; if none: `secrets: {}`.
5. Update `release.yaml` — set `bundle.ref` to the release version, add human-readable bullets.

**Validate before commit:**
- `git status` — confirm no unrelated or generated files are staged
- Validate YAML files if a parser is available
- `python3 -m py_compile` on changed Python files

**Agent rules:**
- Use the human-provided version string exactly — never infer it from the date.
- Read existing `release.yaml` before writing; do not overwrite release notes blindly.
- Stage only the release files for in-scope bundles; never stage unrelated or generated files.
- If `commit: false` / `tag: false` / `push: false`, skip that step entirely.
- If a git tag already exists at the requested version, stop and ask what to do.
- Never put real secrets into `bundles.template.yaml` or `bundles.secrets.template.yaml` config examples.
- Keep customer-specific repository details inside customer repositories; keep this procedure generic.

### Tier 2 — read only on demand (when Tier 1 is not enough)

**Header-first gate:** Before reading any Tier 2 doc in full, fetch it and read only the
title and first section (≈first 30 lines, up to the first `##` heading). Then ask yourself:
does this doc specifically address what I am implementing right now? If yes — read the rest.
If no — stop; you have confirmed it is not needed for this task.

Pull these when the task specifically hits the topic. Do not preload. All under
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/<filename>`:

- `bundle-index-README.md` — SDK map
- `bundle-reference-versatile-README.md` — annotated walkthrough of versatile
- `bundle-dev-README.md` — dev loop / layout
- `bundle-runtime-README.md` — runtime internals
- `bundle-platform-integration-README.md` — platform hooks
- `bundle-props-secrets-README.md` — props / secrets model (read when editing either)
- `bundle-knowledge-space-README.md` — **read for KS / `ks:` namespace resolvers**
- `bundle-node-backend-bridge-README.md` — **read for Node/TS backend**
- `bundle-widget-integration-README.md` — widget deep-dive
- `bundle-client-ui-README.md` / `bundle-client-communication-README.md` — client UI + transport
- `bundle-venv-README.md` — `@venv` internals
- `bundle-scheduled-jobs-README.md` — `@cron` internals
- `bundle-storage-cache-README.md` — storage + cache
- `bundle-sse-events-README.md`, `bundle-transports-README.md`, `bundle-frontend-awareness-README.md`,
  `bundle-interfaces-README.md`, `bundle-lifecycle-README.md`, `bundle-ops-README.md`,
  `bundle-firewall-README.md`, `bundle-platform-properties-README.md` — specialized; read by name when the topic matches.

**Descriptor / service configuration** — read the matching file **only when editing that
specific descriptor**. Apply the same header-first gate: fetch, read the title and first
section, confirm it covers your specific field, then read in full. Base:
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/configuration/<filename>`:

- `assembly-descriptor-README.md` — when editing `assembly.yaml`
- `bundles-descriptor-README.md` — when editing `bundles.yaml`
- `bundles-secrets-descriptor-README.md` — when editing `bundles.secrets.yaml`
- `gateway-descriptor-README.md` — when editing `gateway.yaml`
- `secrets-descriptor-README.md` — when editing `secrets.yaml`

**Specialized example bundles** — list via the GitHub contents API, then fetch
individual files. Base:
`https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/<dir>`:

- `kdcube.copilot@2026-04-03-19-05` — knowledge-space / extended resolver
- `with-isoruntime@2026-02-16-14-00` — isolated exec
- `resources/node-backend-bridge` — Node/TS bridge

**Suite tests** (read when writing or debugging bundle tests):

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in — do not ask for it)

If — **and only if** — `CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT` is already set in the
environment, read the same paths from
`$CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT/<repo-relative-path>` with `Read` instead of
`WebFetch`. Derive the repo-relative path by stripping the
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/` prefix from any URL above.
If the env var is not set, do not suggest setting it — just use `WebFetch`.

## Primary example

Default to `versatile` (Tier 1). Pull specialized examples from Tier 2 only when the task
is specifically about `ks:` / custom namespace resolvers, isolated exec, or the Node/TS bridge.

**Versatile is NOT a reference for `@cron` or `@venv`** — it does not use them. If the task
needs either decorator, read `bundle-scheduled-jobs-README.md` (for `@cron`) or
`bundle-venv-README.md` (for `@venv`) from Tier 2 before writing code. The copyable
snippets in `how-to-write-bundle-README.md` §4.1 are the minimum correct starting point.

## Register the bundle in `bundles.yaml`

Recommended form — `path` = bundle root, `module: entrypoint`:

```yaml
bundles:
  items:
    - id: "<bundle-id>"
      name: "<Human Name>"
      path: "/bundles/<relative-path-from-host_bundles_path>"
      module: "entrypoint"
```

`path` is the **container path** — `/bundles/` + the bundle's path relative to
`assembly.yaml -> paths.host_bundles_path`. It is **not** `/bundles/<bundle-id>` unless
the bundle directory happens to sit directly under `host_bundles_path` with that name.
Host path in `bundles.yaml` is the #1 source of silent reload failures — see
"Host path and runtime path are not the same thing" in the how-to.

Alternative form (less readable, use only when needed): `path` points at the parent,
`module` carries the bundle subdir — `module: "<bundle_dir>.entrypoint"`.

## Workflows

### Write a bundle from scratch

1. Resolve `$WORKDIR` (ask the user if not found).
2. Read Tier 1 (3 how-to docs + versatile reference bundle end-to-end).
3. If the task hits a specialized feature (`@cron`, `@venv`, KS, Node bridge, isolated
   exec, specific descriptor edit), pull the matching Tier 2 doc.
4. Pick a host directory for the bundle (default `~/.kdcube/bundles/<bundle-id>/`,
   or wherever the user asked). Create it and write `entrypoint.py` + `__init__.py`.
5. Register the bundle via CLI — **do not edit `$WORKDIR` files directly**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bootstrap <bundle-id> <bundle-path>
   ```
   If the bundle lives outside the current `host_bundles_path`, pass `--host-bundles-path
   <parent>` so the mount root is updated atomically.
6. Run bundle tests (`bundle-tests <host-path>`), then reload + verify-reload.

### Wrap an existing application into a bundle

1. Resolve `$WORKDIR` (ask if not found).
2. Read the existing app's code to understand entry points, APIs, and data.
3. Read Tier 1 (3 how-to docs + versatile). Pull Tier 2 on demand.
4. Map the app's functionality to bundle primitives (`@api`, `@ui_main`, `@cron`, etc.).
5. Pick a host directory (default `~/.kdcube/bundles/<bundle-id>/`, or wherever the user
   asked). Copy the app source into it (or under a subdir) and call it from `entrypoint.py`.
   Do not modify the original app tree.
6. Register via `kdcube_local.py bootstrap` (see step 5 above), run bundle tests,
   then reload + verify-reload.

### Add a feature to an existing bundle

1. Read the existing `entrypoint.py` and the relevant docs section.
2. Make the minimal change that adds the feature.
3. Run bundle tests, then reload + verify-reload.

## Authoring rules

- Read the docs and examples before writing code — every time, even for small changes.
- Do not invent decorators, import paths, or bundle tree layout.
- For third-party Python packages, first check whether the runtime already has them.
- Use `@venv(...)` for dependency-heavy leaf helpers, not for request-bound orchestration.
- Keep communicator, request context, Redis, DB clients, and other live proc/runtime
  bindings outside `@venv(...)`.
- If a Node backend is needed, keep Python as the bundle boundary and put Node/TS behind a
  narrow bridge.
- If local runtime setup is needed, use `/kdcube-builder:bootstrap-local` first.
- **`.kdcube-runtime` is read-only** — see Rule #0 at the top of this skill. This is the
  absolute constraint; refer to Rule #0 if in doubt. Bundle source files outside `$WORKDIR`
  are editable as normal.

## Validation + reload

Run the shared bundle suite before considering bundle work done:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" bundle-tests /abs/path/to/bundle
```

Then reload if the runtime is running — **always pair `reload` with `verify-reload`**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle-id>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle-id>
```

### Reload rules (read before touching a running runtime)

- Editing files in `HOST_BUNDLES_PATH/<bundle-id>/` does **not** hot-reload. The runtime
  serves the cached bundle until an explicit `reload <bundle-id>`. Old code keeps running
  until you reload — that is the usual cause of "my change didn't take effect".
- `reload` only works if `<bundle-id>` is registered in `bundles.yaml` with the correct
  container path (`/bundles/<bundle-id>`). A typo or host-path in `bundles.yaml` makes the
  reload succeed-looking but no-op.
- **Always run `verify-reload` after `reload`.** The reload call returns before the proc
  cache actually rotates; without verify you do not know whether the new code is live.
- `verify-reload` reporting `eviction: None` for a bundle that was supposed to be active is
  a red flag — the bundle was never in the proc cache, which usually means the id/path in
  `bundles.yaml` is wrong, or the bundle was never loaded in the first place.
- Any container restart (secrets injection, `kdcube --stop`/`start`, Docker restart) drops
  the proc cache. Reload every active bundle immediately after such events.