---
id: versatile@2026-03-31-13-36/agents
title: "Versatile Reference Bundle Builder-Agent Onboarding"
summary: "Builder-agent onboarding guide for maintaining the versatile reference bundle: entrypoint contract, ReAct workflow, custom iframe main UI, source-folder widget, tools, skills, MCP, runtime config, tests, docs, and release metadata."
status: "active"
tags: ["agents", "builder", "onboarding", "versatile", "reference-bundle", "chat-ui", "ui", "backend", "runtime", "redux-toolkit"]
see_also:
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md"
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/docs/design"
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py"
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py"
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/main/src/App.tsx"
  - "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/versatile_webapp"
  - "repo:applications/packages/kdcube-copilot/AGENTS.md"
  - "repo:applications/packages/kdcube-copilot/docs/instructions/builder-agent.md"
  - "repo:applications/src/demo/news@2026-05-20-12-05/AGENTS.md"
  - "ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md"
  - "ks:docs/sdk/bundle/build/how-to-write-bundle-README.md"
  - "ks:docs/sdk/bundle/bundle-widget-integration-README.md"
  - "ks:docs/sdk/bundle/bundle-client-ui-README.md"
  - "ks:docs/sdk/bundle/ui-components-lifecycle-README.md"
---
# Versatile Reference Bundle Builder-Agent Onboarding

This is the builder-agent landing page for `versatile@2026-03-31-13-36`.

`versatile` is the full-feature reference bundle for bundle builders. It
intentionally demonstrates the main SDK bundle surfaces together in one
place — chat entrypoint, ReAct workflow, economics, bundle props, bundle
secrets, bundle-local tools and skills, shared storage, MCP, isolated
exec, source-folder widget, and a custom iframe chat main view — so a
human or builder copilot can learn the platform from one concrete
implementation before branching into narrower examples.

Agents may work on the chat main UI (`ui/main/`), the source-folder
widget (`ui/widgets/versatile_webapp/`), the bundle entrypoint, the
orchestrator, tools/skills/MCP, descriptors, tests, docs, or the full
stack. Start here, pick the track that matches the task, and keep the
bundle contracts updated as you change behavior.

For general Build-with-KDCube operating rules — local runtime bootstrap,
descriptor sync-back, widget validation, quality gates, and field report
placement — read first:

- `repo:applications/packages/kdcube-copilot/AGENTS.md`
- `repo:applications/packages/kdcube-copilot/docs/instructions/builder-agent.md`
- `repo:applications/packages/kdcube-copilot/docs/instructions/local-runtime-bootstrap.md`
- `repo:applications/packages/kdcube-copilot/docs/instructions/widget-builder.md`
- `repo:applications/packages/kdcube-copilot/docs/instructions/quality-gates.md`

Even when the assigned task sounds UI-specific, this is a KDCube bundle
task. The agent is expected to understand the bundle runtime, the
entrypoint contract, configured aliases, widget and main-view serving
paths, ReAct workflow shape, economics/quotas, staged descriptors, reload
flow, and test/release expectations before claiming the surface is done.

## Onboarding Order

First, establish the local runtime context before assuming live UI / API
validation is possible:

- Read `ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md`.
- Discover the local KDCube repo, applications repo, active tenant/project,
  active workdir, and whether a running KDCube stack already exists.
- Discover whether this bundle is already configured in the staged runtime
  `bundles.yaml` / `bundles.secrets.yaml`.
- If the user asked for live validation and the bundle is not configured,
  use the KDCube CLI workflow from that doc to configure this bundle by
  local path, then reload it.
- If seed descriptors are being used, identify their source path and remember
  that `kdcube init --tenant T --project P --descriptors-location ...` stages
  them into a fresh runtime workdir; later `kdcube bundle ... --set-config /
  --set-secret` patches the staged runtime descriptors. Once initialized,
  `kdcube init` refuses on the same workdir — use
  `kdcube refresh --tenant T --project P --build` for re-init (rebuild
  images / restart without descriptor changes). Add `--latest`, `--upstream`,
  or `--release <ref>` to `refresh` when the existing runtime should move
  to another platform source while preserving descriptors.
- If you change staged bundle config or secrets, report whether the seed
  descriptors are now stale. Use the reusable local-runtime bootstrap doc
  for the `kdcube export` bundle-descriptor sync-back rule and for manual
  start/stop/reload commands to give the user.

Do not claim "opened main UI", "widget tested", "MCP tested", "skill tool
tested", or "isolated exec verified" unless a running KDCube runtime is
confirmed, this bundle is loaded there, and the relevant probes were
actually executed against the live runtime.

When scripting CLI checks, prefer machine-readable modes where available:

```bash
"$KDCUBE" info --json --tenant "$TENANT" --project "$PROJECT"
"$KDCUBE" bundle status "$BUNDLE_ID" --json --tenant "$TENANT" --project "$PROJECT"
```

If JSON output is consumed by another tool, validate it as real JSON
rather than relying on visual output.

Read these first for any task:

- [README.md](README.md)
- [entrypoint.py](entrypoint.py)
- [orchestrator/workflow.py](orchestrator/workflow.py)
- [docs/design](docs/design)

Read interface and contract surfaces before changing API, payload, or
runtime behavior:

- [interface](interface)
- [tools_descriptor.py](tools_descriptor.py)
- [skills_descriptor.py](skills_descriptor.py)
- [release.yaml](release.yaml)
- [tests/test_preferences_canvas.py](tests/test_preferences_canvas.py)

## Track-Specific Instructions

**Chat main UI agents** (`ui/main/`) must start with:

- [ui/main/src/App.tsx](ui/main/src/App.tsx)
- [ui/main/src/app/store.ts](ui/main/src/app/store.ts)
- [ui/main/src/features/chat/chatSlice.ts](ui/main/src/features/chat/chatSlice.ts)
- [ui/main/src/features/chat/chatTypes.ts](ui/main/src/features/chat/chatTypes.ts)
- [ui/main/src/api/types.ts](ui/main/src/api/types.ts)
- `repo:applications/src/demo/styles/how-to-style-kdcube-interfaces.md`
- `repo:applications/src/demo/styles/restyle-kdcube-built-in-ui-components-lookbook.html`
- `ks:docs/sdk/bundle/bundle-client-ui-README.md`

Chat main UI uses the modular Redux Toolkit architecture introduced
across Waves 1-4 — see [Modular Main-UI Architecture](#modular-main-ui-architecture)
below. New chat state goes in `features/chat/chatSlice.ts` as a slice
reducer + typed action; do **not** reintroduce `useState<ChatState>` in
`App.tsx`. New chat events go through the slice's event reducers
(`chatStarted` / `chatDelta` / `chatStep` / `chatCompleted` /
`chatErrored` / `convStatusUpdated`) which delegate to the pure
`apply*` functions in `features/chat/chatReducers.ts`.

**Source-folder widget agents** (`ui/widgets/versatile_webapp/`) must
start with:

- [ui/widgets/versatile_webapp](ui/widgets/versatile_webapp)
- `ks:docs/sdk/bundle/bundle-widget-integration-README.md`
- `ks:docs/sdk/bundle/ui-components-lifecycle-README.md`
- `repo:applications/src/demo/styles/how-to-style-kdcube-interfaces.md`

UI-specialized agents (either surface) still own the KDCube runtime /
bootstrap validation needed to prove their work. They should not stop at
`npm run build` if the request requires checking the UI inside a running
KDCube.

**Backend / orchestrator / tools / skills agents** must start with:

- [entrypoint.py](entrypoint.py)
- [orchestrator/workflow.py](orchestrator/workflow.py)
- [agents](agents)
- [tools](tools)
- [skills](skills)
- [tools_descriptor.py](tools_descriptor.py)
- [skills_descriptor.py](skills_descriptor.py)
- [preferences_store.py](preferences_store.py)
- [tests/test_preferences_canvas.py](tests/test_preferences_canvas.py)
- `ks:docs/sdk/bundle/build/how-to-write-bundle-README.md`
- `ks:docs/sdk/bundle/bundle-agent-integration-README.md`
- `ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md`

**Full-stack agents** must read both UI and backend tracks before
changing behavior across `entrypoint.py`, `orchestrator/`, `tools/`,
`skills/`, `interface/`, and `ui/`.

## KDCube Docs

Read these local KDCube docs before changing bundle or widget surfaces:

- `ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md`
- `ks:docs/sdk/bundle/build/how-to-write-bundle-README.md`
- `ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md`
- `ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md`
- `ks:docs/sdk/bundle/build/how-to-test-bundle-README.md`
- `ks:docs/sdk/bundle/bundle-widget-integration-README.md`
- `ks:docs/sdk/bundle/bundle-client-ui-README.md`
- `ks:docs/sdk/bundle/ui-components-lifecycle-README.md`
- `ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md`
- `ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md`

Read the KDCube CLI's own READMEs before running any `kdcube` command on
this bundle's workdir. Most importantly: `kdcube init` is first-time
setup only and refuses on already-initialized workdirs; for re-init use
`kdcube refresh --tenant T --project P --build`, optionally with exactly
one of `--latest`, `--upstream`, or `--release <ref>`:

- `ks:src/kdcube-ai-app/kdcube_cli/README.md`
- `ks:src/kdcube-ai-app/kdcube_cli/additional_README.md`

Link convention:

- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/...`
  maps to this bundle in the knowledge space.
- `ks:docs/...` and `ks:src/...` map to KDCube platform docs/source.
- `repo:applications/...` maps to the applications repository.
- `repo:website/...` maps to the KDCube website repository.

Use `ks:` links in front matter and for KDCube platform references. Use
relative Markdown links in narrative text when pointing to files inside
this bundle.

## Local Runtime Bootstrap For This Bundle

When asked to configure or validate this bundle in a local KDCube runtime,
follow the coding-agent bootstrap doc and adapt this bundle-specific shape.
This bundle ships inside the kdcube-ai-app repo, so the local path lives
under the platform tree rather than the applications tree:

```bash
export BUNDLE_ID="versatile@2026-03-31-13-36"
export BUNDLE_PATH="<kdcube-ai-app-root>/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36"

"$KDCUBE" bundle "$BUNDLE_ID" \
  --tenant "$TENANT" --project "$PROJECT" \
  --local-path "$BUNDLE_PATH" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" reload "$BUNDLE_ID" --tenant "$TENANT" --project "$PROJECT"
```

After editing the main UI source under `ui/main/src/`, a `kdcube reload`
is enough — the platform's `_ensure_ui_build` machinery rebuilds the
Vite bundle on the next request once the source tree's signature
changes. If you want to verify the build locally before reloading:

```bash
cd "$BUNDLE_PATH/ui/main"
npm install --no-package-lock
OUTDIR=/tmp/versatile-main-build npm run build
```

Patch deployment values through CLI, not by editing runtime YAML by hand:

```bash
"$KDCUBE" bundle "$BUNDLE_ID" \
  --tenant "$TENANT" --project "$PROJECT" \
  --set-config <key> <value> \
  --set-secret <key> "$SECRET"

"$KDCUBE" reload "$BUNDLE_ID" --tenant "$TENANT" --project "$PROJECT"
```

## Style Inputs For UI Work

The demo style directives are referenced as:

```text
repo:applications/src/demo/styles/how-to-style-kdcube-interfaces.md
repo:applications/src/demo/styles/restyle-kdcube-built-in-ui-components-lookbook.html
repo:applications/src/demo/styles/colors_with_pink.html
```

UI work must follow those directives. The main-UI uses the §2 design
tokens (`--blue`, `--text-2`, `--muted`, `--line-soft`, `--surface`,
`--surface-2`, `--gold`, `--green`, `--sky`, `--pink`, `--red`) and the
§3 component primitives (`.k-workitem`, `.k-tint-*`, `.k-msg`,
`.k-notice`, `.k-status`, `.k-chip`, `.k-tabs`, `.k-tab`,
`.k-result-list`, `.k-result-row`, `.k-result-favicon`, `.k-snippet`,
`.k-composer`, `.k-empty`, `.k-appbar`) defined in
[ui/main/src/index.css](ui/main/src/index.css). Do not introduce new
ad-hoc colours; widen the token if the design genuinely needs a new
value.

The pre-refactor monolithic `ui/main/src/App.tsx` is kept at
`ui/main-bckp/` as a reference snapshot. Do not edit `main-bckp/` — it
is read-only history. Do not commit it as new work.

## Bundle Map

```text
AGENTS.md
README.md
release.yaml
entrypoint.py
event_filter.py
preferences_store.py
skills_descriptor.py
tools_descriptor.py
agents/
config/
docs/
  design/
  integrations/
  journal/
  scenarios/
  storage/
interface/
orchestrator/
resources/
skills/
tests/
tools/
ui/
  main/          # Modular RTK chat main view — see "Modular Main-UI Architecture"
  main-bckp/     # Read-only pre-refactor snapshot
  widgets/
    versatile_webapp/
```

## Modular Main-UI Architecture

The custom iframe chat view at `ui/main/` was refactored from a single
~4500-line `App.tsx` into a modular Redux Toolkit app in four waves
(commits `69df9842`, `b2c39dca`, `e02f899a`, `2959f22e`). The shape that
new code must respect:

```text
ui/main/src/
├── App.tsx                   Top-level component + SSE connect/send glue.
│                             Reads from RTK via useAppSelector; dispatches typed actions.
├── main.tsx                  Mounts <App/> inside <Provider store={store}>.
├── index.css                 Design tokens + component primitives.
├── settings.ts               Parent-window settings handshake (tenant/project/auth).
├── service.ts                Thin barrel re-exporting from ./api/.
│
├── api/
│   ├── types.ts              Wire types (envelopes, DTOs, params).
│   ├── transport.ts          buildRequestHeaders, resolveAbsoluteUrl,
│   │                         downloadBlobAsFile, requireScope,
│   │                         fetchProfileSessionId.
│   ├── sseTransport.ts       openChatStream (EventSource lifecycle).
│   └── client.ts             HTTP fetchers + downloads.
│
├── app/
│   ├── store.ts              configureStore({ reducer: { chat } }).
│   └── hooks.ts              Typed useAppDispatch + useAppSelector.
│
├── components/               Generic, feature-agnostic primitives:
│   ├── utils.ts                helpers (formatTime, shortUrl, …)
│   ├── highlight.ts            syntax highlighter
│   ├── MarkdownBlock.tsx
│   ├── Snippet.tsx
│   ├── CanvasRender.tsx
│   ├── CopyButton.tsx
│   ├── DownloadButton.tsx
│   ├── CaretIcon.tsx
│   └── SuggestedQuestions.tsx
│
└── features/
    ├── banners/BannerStrip.tsx
    ├── conversations/ConversationsSidebar.tsx
    ├── composer/Composer.tsx
    └── chat/
        ├── chatTypes.ts          Domain types + `initialState`.
        ├── chatReducers.ts       Pure `ChatState → ChatState` functions
        │                         (applyChatStart, applyChatDelta, …) plus
        │                         hydrateHistoricalConversation.
        ├── chatSlice.ts          RTK slice. Event reducers delegate to
        │                         the apply* functions. Action surface
        │                         is the only legal way to mutate state.
        ├── turnTabs.tsx          Overview / Timeline / Steps / Links /
        │                         Files / Canvas tab content + helpers.
        ├── ChatTurnView.tsx      "Chat" tab — calm rendering of the
        │                         same turn data Overview shows.
        └── TurnView.tsx          Per-turn tab dispatcher.
```

### Modular Architecture Rules

- **State**: every piece of chat state lives in `features/chat/chatSlice.ts`.
  Add a typed action + reducer for any new state. Do not reintroduce
  `useState<ChatState>` in `App.tsx`. Composer text/files, banners,
  input lock, conversation list, conversation pointer/title, and
  per-turn data are all in the slice.
- **State machine events**: SSE envelopes from `openChatStream` are
  dispatched as `chatActions.chatStarted` / `chatDelta` / `chatStep` /
  `chatCompleted` / `chatErrored` / `convStatusUpdated`. Each delegates
  to the matching pure `apply*` in `features/chat/chatReducers.ts`.
  Keep the pure functions pure — Immer accepts a returned-from-reducer
  state as the new state.
- **Per-turn UI**: per-turn tab content lives in `turnTabs.tsx`; the
  calm "Chat" tab variant lives in `ChatTurnView.tsx`. The tab strip
  and dispatcher live in `TurnView.tsx`. The Chat tab and the Overview
  tab read the **same** `mergeOverviewEvents(turn.artifacts,
  turn.additionalUserMessages)` source — do not split the data path.
- **API**: HTTP calls live in `api/client.ts`. SSE in `api/sseTransport.ts`.
  Shared header / URL / auth helpers in `api/transport.ts`. Wire types
  in `api/types.ts`. Existing code imports through the `./service.ts`
  barrel; new code should import from the specific `./api/*` module.
- **Primitives vs features**: a file is feature code if it knows about
  `ChatTurn`, `Artifact`, or a specific tab; otherwise it is a
  primitive and belongs in `components/`. Do not push domain knowledge
  into `components/`.
- **Style**: every visual element uses the §2 tokens and §3 primitives
  from the styles handbook. New colours mean a token review, not an
  ad-hoc hex.
- **Backup**: `ui/main-bckp/` is a read-only snapshot of the
  pre-refactor monolithic App. Do not edit it.

## Mission And Invariants

Build and maintain a reference bundle that demonstrates the main SDK
bundle surfaces together in one place, so a human or builder copilot can
learn the platform from one concrete implementation.

Keep these invariants:

- The bundle entrypoint inherits a concrete `BaseEntrypoint` family
  class (currently the economics variant) so the platform's UI build
  machinery, economics gating, and bundle props lifecycle apply. See
  `ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md`.
- The orchestrator drives a single ReAct workflow; the chat main UI is
  a transcript over that workflow's events, not an independent state
  machine.
- The custom iframe main view (`ui/main/`) is the canonical chat
  surface. It uses the modular RTK architecture described above. The
  source-folder widget (`ui/widgets/versatile_webapp/`) is a separate
  surface that exercises the source-folder widget contract.
- Vite `base: './'` and `OUTDIR` build behavior must be preserved for
  both `ui/main/` and `ui/widgets/versatile_webapp/`.
- Bundle props / secrets / quota policies stay declarative in
  `entrypoint.py` (and `configuration_defaults()` where applicable).
  Patch values through `kdcube bundle … --set-config / --set-secret`,
  not by hand-editing staged YAML.
- Bundle-local tools and skills are reached through
  `tools_descriptor.py` / `skills_descriptor.py`. MCP endpoints are
  exposed through entrypoint decorators (e.g. `preferences_tools_mcp`).
- `preferences_store.py` is the shared bundle storage adapter; do not
  duplicate its access pattern in tools or skills.
- Do not store real API keys, git tokens, or Claude Code secrets in
  the repo.
- The pre-refactor `ui/main/src/App.tsx` snapshot at `ui/main-bckp/`
  is read-only history. Do not commit edits to it.

## Current State

Implemented:

- Bundle entrypoint with economics + bundle props + quotas
- ReAct workflow (`orchestrator/workflow.py`) with `agents/gate.py`
- Bundle-local tools (`tools/preference_tools.py`) + skills
  (`skills/product/preferences/`)
- Shared bundle storage (`preferences_store.py`)
- MCP endpoint (`entrypoint.py:preferences_tools_mcp`)
- Isolated exec entrypoint (`entrypoint.py:preferences_exec_report`)
- Source-folder widget (`ui/widgets/versatile_webapp/`)
- Custom iframe chat main view (`ui/main/`) — modular RTK architecture
  across Waves 1-4:
  - Wave 1: types + primitives + utils extracted
  - Wave 2: feature subcomponents (banners, conversations sidebar,
    composer, turn tabs, chat view, turn view) extracted
  - Wave 3: RTK store + `chatSlice` + slice-backed event reducers
  - Wave 4: API split into `api/{types,transport,sseTransport,client}.ts`;
    `service.ts` reduced to a barrel
- "Chat" tab in the per-turn UI rendering the same data as Overview
  with calmer chrome (steel-blue dotted thinking timeline,
  collapsible-with-streaming-preview, suppressed top-level citations,
  ext-aware file downloads, plain inline timeline notes, favicons on
  web search/fetch).

Still requires hardening and validation:

- The bundle-generated answer markdown occasionally embeds bare
  `Download: <name>` lines pointing at the platform `/platform/chat`
  origin instead of a real resource URL. The Chat tab's `ChatFileBlock`
  exposes a working download for the same files; the bundle-side
  generator still needs to either stop emitting those lines or rebuild
  them from the artifact's `rn`.
- Style-handbook conformance pass across all tabs (Steps / Timeline /
  Canvas / Links / Files / Overview) hasn't been re-audited since the
  Wave-2 extraction split them out of `App.tsx`.
- Wider live testing of the modular chat surface against historical
  conversation reload + concurrent followup/steer sends.

## Change Ownership

When you change behavior, update the matching contract artifacts in the
same work pass.

Backend / entrypoint / orchestrator / tools / skills behavior changed:

- update [entrypoint.py](entrypoint.py) docstrings and decorators
- update [orchestrator/workflow.py](orchestrator/workflow.py)
- update [tools_descriptor.py](tools_descriptor.py) /
  [skills_descriptor.py](skills_descriptor.py) if surfaces changed
- update relevant docs under [docs/design](docs/design)
- update [tests/test_preferences_canvas.py](tests/test_preferences_canvas.py)
  and add new tests for new contracts
- update [interface](interface) if payloads or aliases changed

Chat main UI behavior changed (`ui/main/`):

- if state shape changed: update
  [ui/main/src/features/chat/chatTypes.ts](ui/main/src/features/chat/chatTypes.ts)
  and the corresponding reducer cases in
  [ui/main/src/features/chat/chatSlice.ts](ui/main/src/features/chat/chatSlice.ts)
- if reducer logic changed: update the pure function in
  [ui/main/src/features/chat/chatReducers.ts](ui/main/src/features/chat/chatReducers.ts);
  the slice reducer is a thin wrapper
- if a new component was added: place it in `components/` (primitive)
  or `features/<area>/` (feature code) per
  [Modular Architecture Rules](#modular-architecture-rules)
- if a new API call was added: add to `api/client.ts` (HTTP) or
  `api/sseTransport.ts` (SSE), re-export from `service.ts` if it must
  be part of the public surface
- update [ui/main/src/index.css](ui/main/src/index.css) if new style
  tokens or primitives were introduced
- update [README.md](README.md) "What it demonstrates" table if a
  capability moved or was added

Widget behavior changed (`ui/widgets/versatile_webapp/`):

- run the widget build (`OUTDIR=… npm run build`)
- update widget-side README if present
- update [README.md](README.md) widget row if the integration shape
  changed

Config / descriptors changed:

- update bundle config template if one exists under [config](config)
- update [README.md](README.md) if the demonstration matrix changed
- update tests that assert config / default contracts

Release-facing change:

- update [release.yaml](release.yaml)

Any meaningful behavior, workflow, or assumption change:

- append [docs/journal](docs/journal)

## Validation

Run Python checks for backend, entrypoint, tools, skills, descriptor,
or config work:

```bash
python3 -m py_compile \
  src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py \
  src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py \
  src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/preferences_store.py

PYTHONPATH=<kdcube-ai-app-root>/app/ai-app/src/kdcube-ai-app pytest -q \
  src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests
```

Run chat main UI checks for UI work:

```bash
cd <bundle-path>/ui/main
npm install --no-package-lock
npx tsc --noEmit --skipLibCheck
OUTDIR=/tmp/versatile-main-build npm run build
```

Run widget build for source-folder widget work:

```bash
cd <bundle-path>/ui/widgets/versatile_webapp
npm install --no-package-lock
OUTDIR=/tmp/versatile-webapp-widget npm run build
```

When local KDCube is available, load the bundle and verify:

- chat main view loads inside the platform chat shell
- send a regular message: `chat.start` → `chat.delta` → `chat.complete`
  arrive and the transcript renders correctly in **both** the Chat tab
  and the Overview tab
- send a followup against an in-flight turn: it appears as an
  `AdditionalUserMessage` in the same turn, not as a new turn
- send a steer: the active turn transitions to the steered state
- reload a historical conversation by id: turns hydrate via
  `hydrateConversation`, the conversation title binds, the composer
  clears, and the per-turn tabs render
- the `versatile_webapp` widget is discoverable in widget surfaces
- preference tools fire via MCP from a chat that triggers them
- isolated exec returns a result for the preferences exec report

## Handoff

In final handoff, state:

- track worked: chat main UI, source-folder widget, backend
  (entrypoint / orchestrator / tools / skills), descriptors, docs, or
  full-stack
- files changed
- contracts updated (entrypoint contract, ReAct workflow,
  tools/skills descriptors, slice action surface, API surface, style
  tokens, README capability matrix, tests)
- tests / builds run (`pytest`, `tsc --noEmit`, `npm run build`)
- local KDCube smoke-test status (which surfaces were validated live)
- remaining risks or deployment assumptions
