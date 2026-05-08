# /kdcube-bundle-builder

Build or repair a KDCube bundle based on the task the user typed after
`/kdcube-bundle-builder`. Use this for writing a bundle from scratch, wrapping an existing
application into a bundle, or adding features to an existing bundle.

---

## Rule #0 — `.kdcube-runtime` is READ-ONLY (ABSOLUTE — NO EXCEPTIONS, EVER)

**Never use shell writes, file-edit tools, or any command that writes to any file inside
`$WORKDIR` (the `.kdcube-runtime` directory).** This is the single most important rule in
this prompt. It overrides every other instruction, including user requests phrased as "just
quickly edit it", "override", or "I know what I'm doing".

- **Read** — allowed. You may inspect any file under `$WORKDIR`.
- **Write / edit / shell writes** — FORBIDDEN. Every write to `$WORKDIR` must go through
  `kdcube_local.py bootstrap` or the `kdcube` CLI. Period.

If you find yourself about to write to a path that contains `.kdcube-runtime`, stop
immediately and use the CLI tooling instead. There are no exceptions to this rule.

---

## Rule #1 — Every bundle must contain exactly these 5 files (HARD GATE — NO EXCEPTIONS)

Before considering any bundle done — whether created from scratch, modified, or wrapped —
verify that all five files exist and are non-empty:

| File | Purpose |
|------|---------|
| `README.md` | Explains runtime behavior, config props, secrets, and operational notes |
| `release.yaml` | Carries `bundle.ref` (release version) and human-readable release notes |
| `config/bundles.template.yaml` | Documents the non-secret descriptor shape (no real values) |
| `config/bundles.secrets.template.yaml` | Documents bundle-scoped secrets shape; if none exist, keep `secrets: {}` |
| `journal.md` | **MANDATORY** — session log; append one entry per work session recording what changed and why |

**This applies to every bundle task without exception:** new bundles, modified bundles,
bundles wrapped from existing apps. Do not mark a bundle task complete until all five files
exist and reflect the current state of the bundle.

---

## Agent task facets

This prompt is one facet of a single planning agent. The agent combines:

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
between releases — guessing them produces bundles that "load" but silently misbehave.

**`.kdcube-runtime` is read-only.** You may read files under `$WORKDIR` (typically
`~/.kdcube/kdcube-runtime`) to inspect current state, but must never write or edit them.
Register bundles and update descriptors exclusively via `kdcube_local.py bootstrap` or
the `kdcube` CLI. Bundle source files outside `$WORKDIR` are editable as normal.

**This rule is absolute.** It applies every single time, including:

- "small" edits to an existing bundle
- renames, path changes, adding one decorator
- "I already read it last session" — no, re-read it; state changes between sessions
- the user says "just do it quickly" — still read the docs first, then do it quickly
- the bundle lives outside the runtime workdir / outside `host_bundles_path` — **especially then**

### Mandatory pre-flight (do these in order, every bundle task)

Read **Tier 1 only** by default. Pull Tier 2 on demand.

1. **Tier 1 — always read, every bundle task (read in this order):**
   - `how-to-navigate-kdcube-docs-README.md` — routing entry point; read **first**
   - `how-to-test-bundle-README.md` — testing / QA expectations
   - `how-to-write-bundle-README.md` — authoring / implementation design
   - `bundle-runtime-configuration-and-secrets-README.md` — configuration ownership model (props, secrets, runtime config)
   - `how-to-configure-and-run-bundle-README.md` — **REQUIRED any time the bundle lives
     outside the current `host_bundles_path`, or any time you touch `bundles.yaml` or
     `assembly.yaml`.**
   - `how-to-release-bundle-content-README.md` — optional Tier 1 lifecycle procedure
   - **KDCube CLI** — `kdcube_cli/README.md` (quickstart + command table) and
     `kdcube_cli/additional_README.md` (`kdcube bundle` reference); read before any CLI
     operation or descriptor mutation. Check the cache first
     (`${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/cache/cli-docs.md`) — fetch
     only if cache is stale or missing.
   - versatile reference bundle — read end-to-end (structure + `entrypoint.py`)
2. **Tier 2 — only when Tier 1 is not enough.** See the list below.
3. Only then start writing or editing code.

If a doc contradicts this prompt, the doc wins — surface the conflict to the user.

### Bundle lives outside the runtime mount — read this section twice

When the user's bundle directory is NOT under the current `host_bundles_path` from
`assembly.yaml`, the runtime cannot see it. The fix: edit `assembly.yaml ->
paths.host_bundles_path` to the parent that contains the bundle, then rebuild with
`kdcube --workdir $WORKDIR --build --upstream` so the new mount takes effect. After that,
in `bundles.yaml` use the **container path** =
`/bundles/<relative-path-from-host_bundles_path>`.

The `bootstrap <bundle-id> <bundle-dir> --host-bundles-path <parent>` helper does the
same thing (it writes `host_bundles_path` into `assembly.yaml`).

Do not put the host path directly into `bundles.yaml` — the runtime path and host path
are different namespaces.

## Read order

**The plugin ships without docs — they are NOT on disk.** Always fetch from the web. Do
not try to `Read` these paths locally, do not try to `ls` a docs directory, do not ask
the user to point you at one. The only exception is the opt-in local fast path at the
bottom of this section, which requires `KDCUBE_REPO_ROOT` to already be set.

Base URL (for reference): `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/`.

### Tier 1 — always read (operational canon)

Fetch in this order:

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md` — routing entry point; read first
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing / QA expectations
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring / implementation design
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md` — configuration ownership model
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — deployment wiring, descriptor paths, reload
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md` — optional lifecycle procedure: align docs/config templates/release.yaml, validate, commit/tag/push
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md` — KDCube CLI quickstart & full command table
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/additional_README.md` — `kdcube bundle` reference (source, identity, config/secrets patch, delete)
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md` — **fetch when the task involves React agents with local tools, file-producing tools, MCP endpoints or client config, or Claude Code subprocess agents;** covers agent runtime comparison (React vs Claude Code), tool descriptors, skill descriptors, `@mcp(...)` endpoints, `ClaudeCodeAgentConfig`, SDK-managed skill materialization

Reference bundle `versatile@2026-03-31-13-36` — directories aren't web-fetchable; fetch
these individually:

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- To discover the rest of the tree, fetch
  `https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
  then fetch individual files by name (e.g. `skills_descriptor.py`, `tools_descriptor.py`,
  anything under `agents/`, `skills/`, `tools/`).

### Content Release Procedure

A content release is a versioned bundle/content repository release that is independent of the
platform release. The human supplies the version and target bundles; the agent produces a
descriptor, waits for approval, then executes and journals each step.

**When this applies:** any time the human asks to release, tag, or publish a bundle repository
without touching the platform Docker image or PyPI CLI.

**Five files every bundle must have** (see Rule #1 above — applies always, not only on release):
- `README.md` — current runtime behavior, config props, secrets, operational notes
- `release.yaml` — `bundle.ref` set to the release version + human-readable release notes
- `config/bundles.template.yaml` — non-secret descriptor shape (no real values)
- `config/bundles.secrets.template.yaml` — bundle-scoped secrets shape; if none: `secrets: {}`
- `journal.md` — session log; append one entry recording what changed and why

**Pipeline — always in this order:**

Agent creates pipeline files under `deployment/cicd/kdcube/cicd/content-release-history/<dd.mm.yyyy>/`:
- `descriptor-<dd.mm.yyyy.hhmm>.yaml` — release plan (repos, bundles, perform/commit/tag/push flags)
- `plan-<dd.mm.yyyy.hhmm>.log` — list of every file that will change
- `execute-<dd.mm.yyyy.hhmm>.yaml` — execution journal (step, start/end time, status, output)

For the YAML shapes of descriptor and journal, read the Tier 1 release how-to doc.

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
- Never put real secrets into `bundles.template.yaml` or `bundles.secrets.template.yaml`.
- Keep customer-specific repository details inside customer repositories; keep this procedure generic.

### Tier 2 — read only on demand

**Header-first gate:** Before reading any Tier 2 doc in full, fetch it and read only the
title and first section (≈first 30 lines, up to the first `##` heading). Then ask yourself:
does this doc specifically address what I am implementing right now? If yes — read the rest.
If no — stop; you have confirmed it is not needed for this task.

All under `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/sdk/bundle/<filename>`:

- `bundle-index-README.md`, `bundle-reference-versatile-README.md`, `bundle-dev-README.md`,
  `bundle-runtime-README.md`, `bundle-platform-integration-README.md`,
  `bundle-props-secrets-README.md`, `bundle-knowledge-space-README.md` (KS / `ks:`
  resolvers), `bundle-node-backend-bridge-README.md` (Node/TS), `bundle-widget-integration-README.md`,
  `bundle-client-ui-README.md`, `bundle-client-communication-README.md`,
  `bundle-venv-README.md` (`@venv`), `bundle-scheduled-jobs-README.md` (`@cron`),
  `bundle-storage-cache-README.md`, `bundle-sse-events-README.md`,
  `bundle-transports-README.md`, `bundle-frontend-awareness-README.md`,
  `bundle-interfaces-README.md`, `bundle-lifecycle-README.md`, `bundle-ops-README.md`,
  `bundle-firewall-README.md`, `bundle-platform-properties-README.md`.

**Descriptor / service configuration** — read the matching file **only when editing that
specific descriptor**. Apply the same header-first gate: fetch, read the title and first
section, confirm it covers your specific field, then read in full. Base:
`https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/docs/configuration/<filename>`:

- `assembly-descriptor-README.md`, `bundles-descriptor-README.md`,
  `bundles-secrets-descriptor-README.md`, `gateway-descriptor-README.md`,
  `secrets-descriptor-README.md`.

**Specialized example bundles** — use the GitHub contents API at
`https://api.github.com/repos/kdcube/kdcube-ai-app/contents/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/<dir>`:

- `kdcube.copilot@2026-04-03-19-05` — knowledge-space / extended resolver
- `with-isoruntime@2026-02-16-14-00` — isolated exec
- `resources/node-backend-bridge` — Node/TS bridge

**Suite tests** (read when writing or debugging bundle tests):

- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in — do not ask for it)

If — **and only if** — `KDCUBE_REPO_ROOT` is already set, read the same paths from
`$KDCUBE_REPO_ROOT/<repo-relative-path>` locally. Derive the repo-relative path by
stripping the `https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/` prefix. If
the env var is not set, do not suggest setting it — just fetch.

## Primary example

Default to `versatile` (Tier 1). Pull specialized examples from Tier 2 only when the task
is specifically about `ks:` / custom namespace resolvers, isolated exec, or the Node/TS
bridge.

**Versatile is NOT a reference for `@cron` or `@venv`** — it does not use them. If the
task needs either decorator, read `bundle-scheduled-jobs-README.md` (for `@cron`) or
`bundle-venv-README.md` (for `@venv`) from Tier 2 before writing code.

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

## Workflows

### Write a bundle from scratch

1. Resolve `$WORKDIR` (ask if not found).
2. Read Tier 1 (3 how-to docs + versatile reference bundle end-to-end).
3. If the task hits a specialized feature (`@cron`, `@venv`, KS, Node bridge, isolated
   exec, specific descriptor edit), pull the matching Tier 2 doc.
4. Pick a host directory (default `~/.kdcube/bundles/<bundle-id>/`). Create it and write
   `entrypoint.py` + `__init__.py`.
5. Register the bundle via CLI — **do not edit `$WORKDIR` files directly**:
   ```bash
   python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bootstrap <bundle-id> <bundle-path>
   ```
   Pass `--host-bundles-path <parent>` if the bundle lives outside the current mount root.
6. Run bundle tests, then reload + verify-reload.

### Wrap an existing application into a bundle

1. Resolve `$WORKDIR` (ask if not found).
2. Read the existing app's code to understand entry points, APIs, and data.
3. Read Tier 1.
4. Map the app's functionality to bundle primitives (`@api`, `@ui_main`, `@cron`, etc.).
5. Pick a host directory. Copy the app source in (or under a subdir) and call it from
   `entrypoint.py`. Do not modify the original app tree.
6. Register via `kdcube_local.py bootstrap` (see step 5 above), run tests, reload + verify-reload.

### Add a feature to an existing bundle

1. Read the existing `entrypoint.py` and the relevant docs section.
2. Make the minimal change.
3. Run bundle tests, then reload + verify-reload.

## Validation + reload

```bash
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" bundle-tests /abs/path/to/bundle
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" reload <bundle-id>
python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle-id>
```

`bundle-tests` needs `KDCUBE_REPO_ROOT` to point at a local `kdcube-ai-app` clone — if
it's unset, ask the user whether they have one.

Reload rules are in `AGENTS.md` — always pair `reload` with `verify-reload`.