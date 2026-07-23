# AGENTS.md — operating rules for coding agents

Two kinds of agents work with this repository. Identify which you are before
acting; the rules differ, and several are opposites.

```text
platform contributor      you change KDCube itself: the SDK, runtime,
                          services, built-in apps, and docs in THIS repo

operator / app builder    you work WITH KDCube: install and configure a
                          runtime, debug it, and build apps (bundles) that
                          run on it — normally from your own repo, with this
                          repo as installed platform + read-only knowledge
```

Routing test: if the task changes files under `app/ai-app/src/kdcube-ai-app/`
or `app/ai-app/docs/`, you are a contributor. If the task is to run KDCube,
configure a deployment, or build an app for it, you are an operator/builder —
and this repo is your dependency, so you do not edit it. When builder work
surfaces a platform defect, capture a minimal reproduction and open an issue
or propose a PR; keep the workaround in your app.

---

## Shared ground rules (both classes)

- **Configuration is descriptor-owned.** KDCube has no environment-variable
  configuration: every knob is a property in a descriptor
  (`assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, `gateway.yaml`).
  Secrets are `*_ref` pointers resolved server-side; secret values never sit
  in tracked descriptors, prompts, code, or generated output.
  See [docs/configuration/bundles-descriptor-README.md](app/ai-app/docs/configuration/bundles-descriptor-README.md).
- **Modular from the first version.** Split by responsibility into focused
  files; entrypoints and providers stay thin and delegate. Monoliths are not
  an acceptable draft state.
- **Say what a thing is.** In docs, descriptions, and UI copy, use positive
  framing: describe what something does, never define it by contrast.
- **Report outcomes faithfully.** Failing tests are reported with their
  output; skipped steps are named as skipped; nothing is declared verified
  that was not run.
- **Trust model first.** Before deciding where code or data belongs, read
  [docs/arch/security-and-trust-model-README.md](app/ai-app/docs/arch/security-and-trust-model-README.md).

---

## A. Platform contributors

**Purpose:** several agents and people work this repository concurrently,
often on one shared working tree. These rules keep their work from corrupting
each other and keep the platform's contracts intact.

### Git and shared-tree etiquette

- Work on the branch you were given. On a shared working tree, do not create
  or switch branches, and do not stash — other agents' in-progress state dies
  with those moves. To inspect another branch or review a PR, use
  `git worktree` in a separate directory.
- Never commit unprompted. Stage by explicit path, never `git add -A`, and
  re-check `git status` immediately before committing — the tree may have
  changed under you.
- Never step into another agent's in-progress merge, rebase, or revert.

### Code architecture

- SDK code (`kdcube_ai_app/...`) uses **absolute imports only**, including in
  `__init__.py`.
- App-bundle code uses **package-relative imports only** — never a
  `try/except ImportError` fallback to a top-level import. The release bundle
  suite rejects it.
- Every UI widget an app serves is declared with `@ui_widget` on the
  entrypoint — the serving contract and the build contract align by alias.
- Widgets are built by the platform pipeline. Edit widget `src/` only;
  `dist/` is generated. `tsc --noEmit` is the local typecheck.

### Runtime contracts that must not regress

- **Harness transparency:** the model is told in-band what happened to its
  output (what the user actually saw) — no silent re-runs, drops, or UI
  dedup. Platform-caused interruptions are stated to the model and logged
  with evidence.
- **Consent is demand-driven, per tool, default-closed.** A delegated caller
  holds exactly what a user explicitly granted; absence of a grant is a
  denial with a precise, actionable reason — never an implicit allow.
- **Distributed by default:** turns hop workers. Rebuildable state is rebuilt
  per turn, never cached in a long-lived process object.
- Fix the failure class, not the trigger — and propagate the fix to known
  similar subsystems in the same pass, with a regression test for the exact
  surfaced case.

### Docs and public content

- Every doc carries YAML front matter (`id`, `title`, `summary`, `tags`,
  `keywords`, `see_also`).
- One document owns each concept's depth; other documents get a one-line
  pointer, not a restated explanation. Public docs never link untracked or
  ignored paths.
- Commit messages and content in this repository are public. No private
  names, no internal-relationship vocabulary, no links to material a reader
  of this repository cannot open.

### Verification

- Run the test suites your change touches before claiming completion; name
  any pre-existing failures you did not cause.
- A code change is live only in a runtime that actually loaded it. Local
  runtimes execute a **staged copy** of the platform source, not your
  checkout — restage explicitly (the CLI's refresh with a source selector)
  before judging a fix "not working".

---

## B. Operators and app builders

**Purpose:** run KDCube and build apps on it. Your deliverable is a working
deployment or a working app — the platform itself is not yours to change.

### The packaged toolkit

The **`kdcube@kdcube`** Claude Code plugin (published at
[github.com/kdcube/agent-plugins](https://github.com/kdcube/agent-plugins))
packages this whole workflow: runtime
bootstrap (`/kdcube:runtime-init`), bundle scaffolding
(`/kdcube:bundle-new`), configuration (`/kdcube:bundle-configure`),
testing (`/kdcube:bundle-test`), release (`/kdcube:bundle-release`),
an operator skill for configure→apply→verify→logs loops, and an offline
Tier-1 documentation pack. If you are a Claude Code agent, install it — it
encodes everything below plus the details.

### Operating a runtime

- Install via the published CLI: `pip install kdcube-cli`, then
  `kdcube init` with your descriptor set. The runtime workdir
  (`~/.kdcube/kdcube-runtime/<tenant>__<project>`) holds staged descriptors
  (`config/`) and a staged platform copy (`repo/`).
- Change configuration by editing descriptors and applying them
  (`kdcube bundle config apply`, `kdcube bundle reload <bundle-id>`), never
  by editing runtime state directly. Redis and generated files are derived
  views — read them for diagnosis, never write them.
- Debug from evidence: `kdcube info`, `kdcube bundle status --json`,
  container logs, and the workdir `logs/`. Start with
  [how-to-avoid-common-bundle-integration-failures-README.md](app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md).

### Building apps (bundles)

An app is a descriptor-addressed package with a declared boundary. Project
the user's requirements onto that boundary — surfaces first, code second:

- **Choose surfaces deliberately.** What the app exposes
  (`surfaces.as_provider`: API, widgets, MCP, chat) and what it may consume
  (`surfaces.as_consumer`: per-agent tools, MCP connections, named services)
  are independent decisions. No surface family is mandatory.
  Read [architecture-of-what-you-build-README.md](app/ai-app/docs/arch/architecture-of-what-you-build-README.md).
- **Follow the bundle contract:** thin `entrypoint.py` composition root with
  the runtime decorators (`@bundle_entrypoint`, `@api`, `@ui_widget`,
  `@mcp`, `@cron`, `@on_job`, …), `configuration_defaults()` for code-owned
  defaults, domain logic in `services/`, transport adapters in `surfaces/`,
  interface declarations kept in sync with the code.
  Start at [how-to-write-bundle-README.md](app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md).
- **Product intent lives in configuration**, not in code introspection: what
  an agent may use is the admin-declared inventory in the descriptor; users
  narrow it per conversation; the runtime enforces at the operation boundary.
- **Package-relative imports** inside the bundle; the platform SDK is
  imported absolutely. Every widget has its `@ui_widget` declaration; widget
  builds run through the platform pipeline.
- **Test before claiming done:** the shared contract suite
  (`python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite
  --bundle-path <app>`) plus your bundle's own pytest, then verify against
  the running runtime over the real transport — a passing unit suite is not
  a working app. See [how-to-test-bundle-README.md](app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md).
- **Keep the package synchronized:** descriptors, interface files, docs,
  tests, and release metadata describe the same surfaces after every change.

### Where to read

[how-to-navigate-kdcube-docs-README.md](app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
is the map. The reference apps under
`app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/`
are worked examples of every pattern above; read them, copy their shape, and
keep your app in your own repository.
