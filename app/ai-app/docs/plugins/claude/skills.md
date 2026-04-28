# Skills

The plugin ships eight skills. One of them (`kdcube-dev`) is the main entry
point that routes all natural-language requests; the rest are either specialized
sub-flows or thin wrappers around individual CLI subcommands.

## `kdcube-dev` — the orchestrator

`skills/kdcube-dev/SKILL.md` is what the user effectively talks to. It
auto-invokes whenever a user message mentions KDCube, a bundle, or a runtime
action. Its intent map:

| User says (any language)                       | Action                                         |
|------------------------------------------------|------------------------------------------------|
| start / run / launch                           | `kdcube_local.py start latest-image`           |
| stop / kill                                    | `kdcube_local.py stop`                         |
| reload `<bundle>`                              | `reload` **then** `verify-reload` (mandatory)  |
| test bundle                                    | `bundle-tests <path>`                          |
| build / create / fix bundle                    | delegate to `/kdcube-builder:bundle-builder`   |
| wrap app into a bundle                         | delegate to `/kdcube-builder:bundle-builder`   |
| add feature to a bundle                        | delegate to `/kdcube-builder:bundle-builder`   |
| setup / first run                              | bootstrap or use-descriptors                   |
| configure / edit descriptors / assembly.yaml   | Configuration flow (read docs, then edit)      |
| inject secrets / clean / export                | delegate to `/kdcube-builder:kdcube-cli`       |
| status / what's running                        | `kdcube_local.py status`                       |

The orchestrator never asks the user to type slash commands — everything runs
through Bash, and delegation happens inside the same session.

### Agent task facets

`kdcube-dev` is a single planning agent that acts as multiple facets depending on
the request: **creator** (write bundle from scratch), **integrator** (wrap existing
app), **configurator** (edit descriptors), **deployer** (wire bundles into runtime),
**local QA** (run shared bundle suite), **integration QA** (reload + verify), and
**document reader** (fetch Tier 1 docs before every bundle task). These are routing
hints, not separate modes or personas.

## Bundle authoring

- **`bundle-builder`** — the authoring brain. Enforces a hard doc read-order gate
  (Tier 1 docs + reference bundle before writing any code — no exceptions), bundle
  placement rules, and registration in `bundles.yaml`. Covers three workflows: write
  from scratch, wrap an existing app, add a feature to an existing bundle. Tier 1 is
  the four `how-to-*` docs plus `bundle-runtime-configuration-and-secrets`; Tier 2
  is the deeper SDK docs, fetched on demand with a header-first gate. See
  [bundle-authoring.md](./bundle-authoring.md).

## Runtime control

- **`local-runtime`** — thin wrapper over `start` / `reload` / `stop` /
  `bundle-tests`. Declared with `disable-model-invocation: true`, so Claude
  reaches it only via `kdcube-dev` dispatch or an explicit slash command.
- **`verify-reload`** — POSTs to the `chat-proc` internal reset endpoint to
  confirm a bundle's proc cache was actually evicted after `reload`. Must run
  after every `reload`; see [runtime-flows.md](./runtime-flows.md).
- **`kdcube-cli`** — direct operations on the `kdcube` CLI that don't fit the
  plugin helper: secrets injection (`--secrets-set`), `--clean`, `--reset`,
  `--stop --remove-volumes`, and live-bundle export from AWS.

## Setup

- **`bootstrap-local`** — generates a fresh descriptor profile for one bundle
  from the `templates/` YAMLs (assembly, bundles, gateway, secrets).
- **`use-descriptors`** — symlinks an existing descriptor directory into a
  profile. Used when the user already has descriptors on disk.

## UI testing

- **`kdcube-ui-test`** — drives the KDCube chat UI in a real browser through
  the Playwright MCP server declared in `plugin.json`. Standard flow: open the
  chat URL, send a test message, wait for a response, screenshot before and
  after, and check for error banners.