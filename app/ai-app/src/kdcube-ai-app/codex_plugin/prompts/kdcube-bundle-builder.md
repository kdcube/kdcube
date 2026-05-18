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

## Rule #1 — Every bundle must contain these maintenance artifacts (HARD GATE — NO EXCEPTIONS)

Before considering any bundle done — whether created from scratch, modified, or wrapped —
verify that all mandatory files exist and are non-empty, and that any conditional files
that apply to the bundle's surfaces are also present and current:

**Mandatory (every bundle, every change):**

| File | Purpose |
|------|---------|
| `README.md` | Explains runtime behavior, config props, secrets, and operational notes |
| `AGENTS.md` (or equivalent maintainer note) | Short maintainer-facing note: what the bundle is, where the live edges are |
| `release.yaml` | Carries `bundle.ref` (release version) and human-readable release notes |
| `config/bundles.template.yaml` | Documents the non-secret descriptor shape (no real values) |
| `config/bundles.secrets.template.yaml` | Documents bundle-scoped secrets shape; if none exist, keep `secrets: {}` |
| `interface/README.md` | Bundle-visible contract: widget aliases, API/MCP/cron/job route aliases, public-auth rules, payload shapes, config keys |
| `docs/design/` | Structured design behind the implementation (not raw notes) |
| `docs/journal/journal.md` | **MANDATORY** — session log; append one entry per work session recording what changed and why |
| `tests/` | Bundle-local tests (at minimum a smoke test for each surface) |

**Conditional (only when the bundle exposes that surface):**

- `interface/*.openapi.yaml` — required when the bundle ships `@api(...)` routes
- `docs/integrations/admin-integrational-homework.md` — required when an integration needs
  external operator work (BotFather, OAuth provider config, webhook registration, etc.)

**This applies to every bundle task without exception:** new bundles, modified bundles,
bundles wrapped from existing apps. Do not mark a bundle task complete until the mandatory
files exist, reflect the current state of the bundle, and the journal entry for this
session has been appended. When runtime behavior, tool/skill contracts, storage semantics,
user-scope mapping, release shape, or Tier 1 builder guidance changes, update the journal
in the same change.

---

## Rule #2 — Bundle source layout (current contract — do not regress)

The bundle source layout for UI surfaces is:

```text
my_bundle/
  ui/
    main/                     # @ui_main source (replaces the old ui-src/ folder)
    widgets/<widget-alias>/   # @ui_widget source (replaces top-level widgets/<alias>/)
```

Rules:

- **Do not scaffold new `ui-src/` folders.** That layout is retired.
- **Do not scaffold top-level `widgets/<alias>/` source folders.** Widget source lives
  under `ui/widgets/<alias>/`.
- Runtime URLs may still contain `/widgets/<alias>` — that is the **served URL contract**,
  not the source layout. Do not let the URL shape drive where you put source files.
- Older example bundles in the repo may still have `ui-src/` or top-level `widgets/` on
  disk; treat those as legacy, not as a layout to copy when authoring new bundles.
- For the full source-folder widget build contract (`OUTDIR`, `<VI_BUILD_DEST_ABSOLUTE_PATH>`,
  Vite/npm build command), use `bundle-widget-integration-README.md` (Tier 2).

---

## Rule #3 — Sparse config overrides (no enabled-flag enumeration)

Bundle config (`bundles.template.yaml`, `bundles.yaml`, runtime overrides) is **override-first**:

- Missing config means the code default applies.
- Config and bundle props should contain **intentional overrides**, especially rare disables
  or non-default values for a specific deployment.
- **Do not** generate large config sections that enumerate `enabled: true` on every resource.
  That is noise; it does not change behavior and rots when defaults move.
- When a resource needs to be disabled in a specific deployment, that disable is an
  intentional override and belongs in config — explain it in the bundle README.

---

## Rule #4 — Access control and visibility are configurable

Current decorator/config behavior for access control and visibility:

- Bundle-wide `allowed_roles` is configurable via bundle props.
- API and widget `user_types` and `roles` are decorator args and may be overridden via
  `user_types_config` / `roles_config` in bundle props.
- Resource `enabled` state is configurable through bundle props and Admin overrides.
- **The removed `enabled_config` decorator argument must not be used.** If you find it in
  legacy code, replace it with the current `enabled.*` feature-gate config shape (see
  `how-to-configure-and-run-bundle-README.md` and `bundle-runtime-configuration-and-secrets-README.md`).
- Keep `bundles.template.yaml` examples sparse — show the override shape, not every flag.

---

## Rule #5 — Secrets discipline (no secrets in descriptors, docs, Redis, or git)

- Bot tokens, webhook secrets, signing keys, OAuth client secrets, API keys, and user
  credentials live in the configured secret store (`bundles.secrets.yaml` or the runtime
  secrets provider).
- Never put real values in `bundles.template.yaml`, `bundles.secrets.template.yaml`,
  README/design docs, journal entries, Redis cache keys, or git history.
- Templates document the **shape** (key names and brief descriptions), never live values.

---

## Rule #6 — SDK-discovery gate (HARD GATE — NO HAND-ROLLING WITHOUT PROOF)

**Before writing any subsystem inside a bundle, prove that the SDK does not already
provide it.** A "subsystem" is anything that is not product-specific business logic:
persistence, queueing, scheduling, transport, identity, authentication, file
delivery, UI serving, agent loops, model dispatch, retry/timeout machinery, locking,
caching, search indices, etc. Product-specific business logic is the prompt
wording, the policy rules, the user-visible workflow shape — nothing else.

This rule replaces every per-feature "do not hand-roll X" warning. The SDK grows;
enumerating forbidden patterns rots. The discoverable inventory is the source of
truth, and you must consult it every time.

### The gate (mandatory, before any non-trivial code)

For each subsystem the bundle needs, complete all four steps and record the outcome
in the design doc / journal entry for this session **before** writing the code:

1. **Search the SDK packages** for the capability. Cast a wide net — search by
   intent, not by your preferred name. At minimum:

   ```bash
   grep -rln "<keyword>" \
     src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/ \
     src/kdcube-ai-app/kdcube_ai_app/infra/plugin/ \
     src/kdcube-ai-app/kdcube_ai_app/infra/

   grep -n "^def [a-z_]*(" \
     src/kdcube-ai-app/kdcube_ai_app/infra/plugin/agentic_loader.py
   ```

   Search **at least three** keyword variants (synonyms, antonyms, verb/noun forms).
   For "queue background work", try `queue`, `job`, `enqueue`, `stream`, `worker`,
   `scheduler`, `cron`, `due`. For "serve a page", try `ui_main`, `main_view`,
   `static`, `serve`, `BundleBinaryResponse`. One miss is normal; three misses is a
   real signal it does not exist.

2. **Open the reference bundles** under
   `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/` and check
   whether one of them already wires that subsystem the SDK way. Shipped bundles
   are the contract, not just samples. Start with `versatile` (transport / Mini App
   / widget / admin / memory), `echo.ui` (`@ui_main` + build pipeline + `@cron`),
   and `kdcube.copilot` (knowledge space, `@on_job`).

3. **Read the matching SDK module's docstring or first 100 lines** to confirm what
   it covers and how it is configured (most SDK modules expose a
   `configure_<thing>(...)` entry that wires bundle-specific glue).

4. **Record the outcome** in `docs/design/<topic>.md` and the journal entry as a
   short block — what you searched for, what you found, and either: (a) which SDK
   module/decorator you are going to wire, or (b) the explicit reason it does not
   apply and why a custom implementation is justified. Three lines is enough; the
   point is auditability.

### Default verdict: use the SDK

If the search surfaces anything that looks even adjacent, the default is to use it
and shape your bundle around its contract — not to write a parallel implementation
because "it's simpler" or "the SDK looks heavier than I need." The SDK piece carries
operational properties (Redis locking, multi-replica safety, cache invalidation,
signed downloads, identity propagation, telemetry hooks) that hand-rolled code
silently lacks.

### When hand-rolling is allowed

You may write a custom subsystem only if **all** of the following hold, and the
design doc says so explicitly:

- The four-step search above turned up nothing close.
- The SDK piece exists but its contract is fundamentally wrong for this bundle
  (not just "more than I need" — actually incompatible).
- A short note in `docs/design/<topic>.md` explains the gap and what migration to
  the SDK piece would look like once it covers the case.

"MVP simplicity", "I'll swap it later", and "no time to learn the SDK" are not
acceptable justifications.

### Pre-completion smell check

Before marking the bundle task complete, scan your own diff for re-implementation
smells and, if you find one, return to the gate above.

**This list is illustrative, not exhaustive.** The smells below are common shapes
that have actually shipped in this repo; they exist to *prime* your eye, not to
define the search space. The real test is the framing above: *is this a subsystem
the SDK could plausibly own?* If yes, run the gate — even if nothing in your diff
matches any pattern below. Conversely, matching a pattern is not proof of
duplication; it is a prompt to re-check. Do not treat the absence of these specific
shapes as a green light.

Common shapes (non-exhaustive):

- A new class whose name ends in `Store`, `Executor`, `Queue`, `Runner`,
  `Scheduler`, `Dispatcher`, `Registry` — often duplicates an SDK block, but the
  SDK also owns plenty of subsystems whose hand-rolled twin would not be named
  this way (rate limiters, identity propagators, webhook validators, …).
- An `@api(method="GET", ...)` route whose body reads a file from `Path(__file__)`
  and returns `BundleBinaryResponse` — the bundle is bypassing the declarative UI
  build pipeline.
- A `_NoopXxx` / `_StubXxx` / `_MockXxx` class passed into `configure_<sdk>(...)` —
  you are stubbing out the SDK instead of wiring it.
- A polling `asyncio` loop with `await asyncio.sleep(...)` over a file/redis
  directory — the SDK already has `@cron`, due-scan, or stream subscribers for
  this.
- Direct `models_service.generate_answer(...)` / `chat(...)` calls outside the
  bundle's workflow graph — the SDK workflows (versatile, ReAct) carry timeline,
  delivery, error reporting, and cost telemetry that a raw call skips.
- Custom `threading.Lock` / file-based mutex / `os.replace` write-then-rename for
  state the SDK already owns — the SDK uses Redis or SQLite with the right
  semantics.

**Inverse rule (important):** the absence of any listed smell is **not** evidence
the bundle is SDK-clean. If your bundle adds a subsystem whose shape is not listed
above, the gate still applies — search the SDK for that capability before
shipping. The smells exist because we have seen them; the gate exists because we
have not yet seen every way the SDK can be re-implemented by accident.

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
   - `how-to-assemble-bundle-with-sdk-building-blocks-README.md` — **SDK/platform building-block map; read before implementing any subsystem so reusable blocks (Tasks, Email, Telegram, Delivery, web/browser/rendering/exec tools, storage, widgets, jobs, MCP, Claude Code) are used instead of hand-rolled mechanics**
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

Base URL (for reference): `repo:kdcube-ai-app/`.

### Tier 1 — always read (operational canon)

Fetch in this order:

- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md` — routing entry point; read first
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md` — testing / QA expectations
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md` — SDK/platform building-block map (Tasks, Email, Telegram, Delivery, web/browser/rendering/exec tools, storage, widgets, jobs, MCP, Claude Code); routes to integration docs before any custom implementation
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md` — authoring / implementation design
- `repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md` — configuration ownership model
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md` — deployment wiring, descriptor paths, reload
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md` — optional lifecycle procedure: align docs/config templates/release.yaml, validate, commit/tag/push
- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md` — KDCube CLI quickstart & full command table
- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_cli/additional_README.md` — `kdcube bundle` reference (source, identity, config/secrets patch, delete)
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md` — **fetch when the task involves React agents with local tools, file-producing tools, MCP endpoints or client config, or Claude Code subprocess agents;** covers agent runtime comparison (React vs Claude Code), tool descriptors, skill descriptors, `@mcp(...)` endpoints, `ClaudeCodeAgentConfig`, SDK-managed skill materialization

### SDK integrations — worked example (Telegram, Email, ngrok, callbacks)

> **Read this as one worked instance of Rule #6, not as the scope of Rule #6.**
> The same SDK-first / discovery-gate logic applies to every other integration
> family the bundle touches (delivery transports, identity providers, file
> producers, scheduled work, persistence, agent loops, …) — including ones not
> enumerated anywhere in this prompt. If a future integration is missing from
> this document, that is not permission to hand-roll it; it is a prompt to run
> the gate (Rule #6) against the assemble map and the SDK packages.

Before writing any custom transport, webhook, Mini App auth, OAuth callback, or local
public-HTTPS workaround, route through the SDK building-block map
(`how-to-assemble-bundle-with-sdk-building-blocks-README.md`). For Telegram / webhook /
Mini App / ngrok tasks, fetch the canonical docs **from the assemble map**:

- `repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md` — Telegram SDK surface (webhook validation, Bot API rendering, progress streaming, Mini App `initData`, user registry, signed downloads)
- `repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md` — BotFather, webhook URL, Mini App, bot token (outside-KDCube work)
- `repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md` — local public-HTTPS recipe through **one** ngrok origin and the local reverse proxy

Guardrails:

- Use the Telegram SDK; do not hand-roll the webhook registry, duplicate-update suppression, Mini App `initData` verification, or send-rendering.
- For local public callbacks: one ngrok HTTPS origin through the local reverse proxy. **Never expose proc as a separate public ngrok URL.**
- Provider URLs and webhook/callback settings: descriptor-backed (`bundles.yaml` config or `assembly.yaml`).
- Bot tokens, webhook secrets, signing keys, OAuth client secrets: in the configured secret store (`bundles.secrets.yaml` or runtime secrets provider) — never in `.env`, code, or seed descriptors.

Reference bundle `versatile@2026-03-31-13-36` is the **public reference for Telegram /
Mini App / widget / attachment / external-operator-prereq integration.** Inspect it
before implementing any of these by hand. It demonstrates:

- Telegram webhook + public route wiring (no hand-rolled webhook registry)
- Telegram Mini App / webapp support (`initData` validation handled by the SDK)
- Telegram admin/user mapping patterns
- widget operations (`@ui_widget` + `@api(route="operations")` split)
- attachment handling (signed downloads, materialized files)
- converting ReAct/agent stream output into Telegram-facing messages/artifacts
- documenting external operator prerequisites (BotFather, webhook URL, Mini App
  registration, bot token) in `docs/integrations/admin-integrational-homework.md`
- the current `ui/main/` + `ui/widgets/<alias>/` source layout

For local Telegram testing you need a public HTTPS origin — use the ngrok recipe from
`docs/service/cicd/ngrok-README.md` (one ngrok origin through the local reverse proxy;
the CLI-started runtime path is the normal flow). External operator work (BotFather, bot
token, command/menu config, webhook + Mini App URL registration) is required before code
will work end-to-end.

Directories aren't web-fetchable; fetch these individually:

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- To discover the rest of the tree, fetch
  `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
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

All under `repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/<filename>`:

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
`repo:kdcube-ai-app/app/ai-app/docs/configuration/<filename>`:

- `assembly-descriptor-README.md`, `bundles-descriptor-README.md`,
  `bundles-secrets-descriptor-README.md`, `gateway-descriptor-README.md`,
  `secrets-descriptor-README.md`.

**Specialized example bundles** — use the GitHub contents API at
`repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/<dir>`:

- `kdcube.copilot@2026-04-03-19-05` — knowledge-space / extended resolver
- `with-isoruntime@2026-02-16-14-00` — isolated exec
- `resources/node-backend-bridge` — Node/TS bridge

**Suite tests** (read when writing or debugging bundle tests):

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_bundle_state.py`
- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_run_bundle_suite.py`

### Local fast path (opt-in — do not ask for it)

If — **and only if** — `KDCUBE_REPO_ROOT` is already set, read the same paths from
`$KDCUBE_REPO_ROOT/<repo-relative-path>` locally. Derive the repo-relative path by
stripping the `repo:kdcube-ai-app/` prefix. If
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