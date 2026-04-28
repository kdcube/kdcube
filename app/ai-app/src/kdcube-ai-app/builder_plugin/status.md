# KDCube Builder Plugin — Dev Notes

## Goal

Claude Code plugin that lets Claude operate KDCube: run CLI, reload bundles, test, build.

Steps:
1. Minimal CLI — reload bundle by id, verify it's live
2. Edit bundle code + reload
3. Browser / UI testing

---

## Step 1 status

| Item | Done |
|---|---|
| Plugin scaffold + marketplace manifest | ✓ |
| Skills: bundle-builder, bootstrap-local, local-runtime, use-descriptors | ✓ |
| `kdcube_local.py`: use-descriptors, verify-reload commands | ✓ |
| `skills/verify-reload/SKILL.md` | ✓ |
| `skills/kdcube-dev/SKILL.md` — natural language orchestrator with TRIGGER | ✓ |
| `kdcube_local.py`: status command | ✓ |
| End-to-end smoke test | ✗ |

---

## Session log

Early work happened on `feat/claude-kdcube-cli-plugin` (now deleted); later work on
`feat/claude-kdcube-plugin-clean`. Commits below reconstruct the timeline from git.

**2026-04-12** — `041b955f`
- First commit: Claude Code bundle builder plugin marketplace scaffold

**2026-04-17** — `36aec2b5`, `2d88e40e`
- `verify-reload` skill — wraps `kdcube_local.py verify-reload <bundle_id>`, checks proc cache eviction
- `kdcube-dev` orchestrator skill — no `disable-model-invocation`, `TRIGGER when:` so Claude auto-invokes on natural-language KDCube requests; maps intents (start/reload/test/build/status) directly to `kdcube_local.py` calls
- `status` subcommand — CLI availability, descriptor profile symlink, workdir, running docker containers
- `install` subcommand — installs `kdcube-cli` via pipx or pip (venv vs global handled automatically)
- `use-descriptors` skill + `cmd_use_descriptors`
- `_expand_descriptors`: copies descriptors to tmp dir with `~` and hardcoded user paths expanded before passing to kdcube CLI
- Fix `assembly.yaml`: replace hardcoded user path with `~` in `host_git_bundles_path`
- Fix `marketplace.json` source path + `plugin.json` `userConfig` type/title fields
- Install flow clarified: `claude plugin marketplace add <path>` + `claude plugin install kdcube-builder@kdcube-builder-marketplace --scope user`

**2026-04-18** — `4d486bea`
- Added `kdcube-find-project`, `kdcube-cli`, `kdcube-ui-test` skills
- Expanded bundle authoring workflows: app wrapping, bundle placement, reload verification, local-doc resolution
- Documented Playwright MCP support; simplified install/update guidance

**2026-04-19** — `eec27ae7`, `432666a1`
- Inlined bundle path resolution (refactor)
- README: added local-install commands

**2026-04-20** — `cab51b52`, `508434e7`
- Dev docs for the plugin split into five files under `docs/plugins/claude/`: `index`, `architecture`, `skills`, `bundle-authoring`, `runtime-flows`, `extending`
- Relaxed bundle placement rules: bundle dir can live anywhere on host (default `~/.kdcube/bundles/<bundle-id>/`); plugin mounts it into container at `/bundles/<bundle-id>`
- Workdir resolution now probes `~/.kdcube/kdcube-runtime` and explicitly asks the user if not found (no silent fallback)
- Removed prescriptive "Bundle placement" section from `bundle-builder/SKILL.md`

**2026-04-21** — `db491551`, `421e6f91`, `b95e6aed`
- Merged plugin into clean branch (`feat/claude-kdcube-plugin-clean`)
- Hardened "always read docs" rule in `bundle-builder`, `kdcube-dev`, `use-descriptors`: doc read is a hard pre-flight gate (no exceptions for small edits / "remembered from last session"); explicit guidance for bundles outside `host_bundles_path`; container-path formula `/bundles/<relative-from-host_bundles_path>`
- Split `bundle-builder/SKILL.md` docs into Tier 1 (3 how-tos + versatile, always read) and Tier 2 (SDK deep-dives, descriptor docs, specialized bundles, suite tests — read on demand with explicit triggers)
- Fixed `bundles.yaml` registration example: `path` is `/bundles/<relative-path-from-host_bundles_path>`, not `/bundles/<bundle-id>`; dropped unsupported `version` / `default_bundle_id`; documented alternative `path` + `module: "<subdir>.entrypoint"` form
- Noted versatile is NOT a reference for `@cron` / `@venv` → Tier 2 (`bundle-scheduled-jobs`, `bundle-venv`) + how-to §4.1 snippets
- Dropped macOS docker-restart gotcha from `SKILL.md`

**2026-04-22** — `819b3eeb`
- Restored 5 YAML descriptor templates lost when the plugin was originally moved
  to the clean branch: `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`,
  `gateway.yaml`, `secrets.yaml` — these are the templates used by `cmd_bootstrap`
  to generate a fresh descriptor profile

**2026-04-23** — `488d0125` — Codex CLI port + header-first gate
- Built `codex_plugin/` — Codex CLI port of the builder plugin. Shares `kdcube_local.py`
  and templates from `builder_plugin/` (copied by `install.sh`); adapted to Codex's
  extension model: `AGENTS.md` as always-in-context rule block + `prompts/*.md` as slash
  commands
- Prompt files: `kdcube-dev`, `kdcube-bundle-builder`, `kdcube-bootstrap`,
  `kdcube-use-descriptors`, `kdcube-runtime`, `kdcube-verify-reload`, `kdcube-cli`,
  `kdcube-ui-test` (8 total)
- `install.sh`: idempotent, copies runtime + templates from sibling `builder_plugin/`,
  merges `AGENTS.md` block between HTML markers (`<!-- kdcube-builder:begin/end -->`)
- `uninstall.sh`: strips prompt files and AGENTS.md block, removes runtime dir
- Added **header-first gate** to both plugins (builder + codex): Tier 2 docs and
  descriptor docs — fetch, read title + first section only, decide if full read needed;
  Tier 1 docs stay always-full-read

**2026-04-29** — `.kdcube-runtime` read-only rule
- Added hard rule across all 6 skill/prompt files (builder_plugin + codex_plugin): AI may
  `Read` files in `$WORKDIR` to inspect state, but must never use `Edit`/`Write` tools there
- All runtime config mutations (descriptors, config files, secrets) must go through `kdcube`
  CLI or `kdcube_local.py` helper exclusively
- Workflows in `bundle-builder` (both plugins): registration step replaced with
  `kdcube_local.py bootstrap` call instead of direct `bundles.yaml` edit
- macOS Docker gotcha in `kdcube-dev/SKILL.md` reformulated to reflect read-only constraint

**2026-04-24** — `13a764e8` — Agent facets + Tier 1 doc reorder + URL path fix
- Added **Agent task facets** section to both `bundle-builder/SKILL.md` and `kdcube-dev/SKILL.md`:
  lists creator/integrator/configurator/deployer/local-QA/integration-QA/document-reader as
  routing hints (not separate personas); `kdcube-dev` delegates bundle authoring to `bundle-builder`
- Added `how-to-navigate-kdcube-docs-README.md` as first Tier 1 doc (routing entry point, read
  before all others) in both skills
- Added `bundle-runtime-configuration-and-secrets-README.md` to Tier 1 in both skills
- Reordered Tier 1 fetch sequence: navigate → test → write → config (was: write → configure → test)
- Fixed URL path for descriptor docs in builder_plugin skills: `docs/service/configuration/` → `docs/configuration/`
- Removed `service-config-README.md` from descriptor doc list (no longer exists at that path)

---

## Cross-tool notes

- **Codex plugin ships as `codex_plugin/`.** Not a marketplace plugin — Codex has no
  plugin system. Instead: `AGENTS.md` (always-in-context orchestrator) + `prompts/*.md`
  (slash commands). Tier 1/Tier 2 split lives in `kdcube-bundle-builder.md`; `AGENTS.md`
  delegates bundle authoring to it. Capabilities identical to builder_plugin; routing
  reliability differs (harness-driven vs model-driven).

---

**Next:** smoke test — start runtime, reload telegram-bot, verify via natural language