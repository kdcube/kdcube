---
id: repo:kdcube/app/ai-app/docs/service/cicd/config-export-README.md
title: "Configuration export and import"
summary: "What kdcube config export writes, what each flag adds, where each part is read from for every secrets provider, what is never exported and why, the output layout, and how kdcube config import and the cloud secrets intake consume it."
tags: ["service", "cicd", "cli", "secrets", "export", "import", "bootstrap"]
keywords: ["kdcube config export", "kdcube config import", "configuration bootstrap", "include-platform-descriptors", "include-user-data", "secrets folder export", "bundles.secrets.yaml", "secrets.yaml users root", "user_bundle_props", "user props export", "browser-approved secret export", "bootstrap selection", "acquired tokens excluded", "connection hub records excluded", "runtime records excluded", "SECRETS_YAML", "BUNDLES_SECRETS_YAML", "aws secrets manager bootstrap", "secrets-file", "secrets-service", "host-vault", "aws-sm"]
updated_at: 2026-10-17
see_also:
  - repo:kdcube/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
  - repo:kdcube/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube/app/ai-app/docs/sdk/solutions/connections/connection-hub-token-storage-README.md
---
# Configuration export and import

`kdcube config export` writes everything needed to bootstrap the
configuration of another deployment: the descriptors, the secrets wherever
they are stored, and, with a flag, the users' own secrets and props. The same
writer produces the same output for every secrets provider, so an export from a
`secrets-file` runtime and one from a `secrets-service`, `host-vault` or
`aws-sm` runtime are interchangeable.

```bash
kdcube config export \
  --tenant <tenant> --project <project> \
  --out-dir <out-dir> \
  --include-platform-descriptors \
  --include-user-data
```

The export never carries what the runtime acquires on its own: the tokens
Connection Hub obtains for connected accounts, Connection Hub's records about
them, and runtime records such as login attempts. The deployment re-acquires
those on use.

## 1) What each flag adds

| Flag | Adds | Read from |
| --- | --- | --- |
| *(none)* | `bundles.yaml` and `bundles.secrets.yaml` (`secrets-file` only; a provider-backed export needs `--include-platform-descriptors`) | the runtime's descriptor folder (`<workdir>/config`) |
| `--include-platform-descriptors` | `assembly.yaml`, `gateway.yaml`, `economics.yaml`, `bundles.yaml`; `secrets.yaml` with the `platform` secrets; `bundles.secrets.yaml` with every app's secrets; the `secrets/` folder with the app secrets | descriptors from `<workdir>/config`; secret values from the selected secrets provider (section 2) |
| `--include-user-data` (needs `--include-platform-descriptors`) | per-user secrets in `secrets/<bundle>/users/<user>/` and in the `users` root of `secrets.yaml`; the user props in `user-data/user_bundle_props.json` | per-user secrets from the provider; user props from PostgreSQL (`user_bundle_props`), read by the running processor |

Without `--include-user-data`, no user secret and no user prop is read or
written; a legacy `users` section of `secrets.yaml` is left out too.

## 2) Where the values come from, per provider

The provider is the deployment's `secrets.provider` in `assembly.yaml`. The CLI
picks the source from it; the output layout is the same either way.

### `secrets-file`: the CLI reads the runtime secrets folder

No server round trip and no browser approval. The CLI runs on the host that
holds the runtime, and reads the runtime secrets folder
(`<workdir>/config/secrets`, or the host folder of a declared
`secrets.runtime.root`) directly, byte for byte.

```text
 host                                             running runtime (docker compose)
 ─────────────────────────────────────────        ──────────────────────────────────
 kdcube config export
   │
   ├─ <workdir>/config/*.yaml ───────────────▶  assembly / gateway / economics /
   │    (secrets.yaml filtered while copied)    bundles / secrets / bundles.secrets
   │
   ├─ <workdir>/config/secrets/ ─────────────▶  secrets/   (+ users root, secrets: blocks)
   │    skip: runtime-record folders,
   │          acquired-token keys,
   │          per-user records without the flag
   │
   └─ (--include-user-data)
        docker compose exec chat-proc ────────▶  chat-proc ── its own pool ──▶ PostgreSQL
          rows on stdout, never printed                                     user_bundle_props
          prefixes passed in; the SELECT     ◀── rows ───────────────────────
          leaves Connection Hub props out
                                                user-data/user_bundle_props.json
```

The runtime must be started for the props (they are read with the
processor's own database connection, so no database credential reaches the
host).

### `secrets-service`, `host-vault`, `aws-sm`: one browser-approved transaction

The values live in the provider, which only the runtime can read. The CLI asks
the runtime for one `bootstrap` export transaction, a person approves it in
the browser, and the CLI exchanges the approval for the values over the
loopback callback. The deployment needs `management.secret_export.enabled:
true`.

```text
 kdcube config export                runtime (chat-proc)                       provider
 ────────────────────                ─────────────────────────────────         ────────────────
 1. start  ───────────────────────▶  /management/v1/secrets/export/start
    selection=bootstrap,             inventory: list every key (no values) ──▶ list keys
    user_data=<flag>, PKCE           drop before parsing: user keys without     (secrets-service,
                                       the flag, acquired-token keys             host-vault vault,
                                     freeze the manifest (targets, user_props)    AWS Secrets Manager)
 2. browser opens the approval page ◀ shows: secret count, "User props: included / not included"
    person approves (session confirmation)
 3. loopback callback ◀─────────────  one-time code
 4. exchange (code + verifier) ────▶  read exactly the frozen targets ─────────▶ read values
                                     (--include-user-data) read user_bundle_props,
                                       skip Connection Hub props
                        ◀───────────  values + user props, once
 5. the same writer as secrets-file: secrets/, secrets.yaml, bundles.secrets.yaml, user-data/
```

Descriptors other than the secret pair are copied from `<workdir>/config` as
for `secrets-file`; the pair itself is written from the approved values, so a
stale local `secrets.yaml` never reaches the export. A user secret that belongs
to no app has no folder home; it goes only to `users.<user>.secrets` in
`secrets.yaml`.

## 3) What is never exported, and why

| Left out | Where it lives | Why |
| --- | --- | --- |
| Acquired tokens: per-user keys under `connections.accounts.`, `delegated_to_kdcube.`, `remote_mcp.`, `linkedin.accounts.` | the provider's user scope | Connection Hub obtains them from the person's own consent; a copy elsewhere would be a credential nobody granted there. They are re-acquired on use. See [connection-hub-token-storage-README.md](../../sdk/solutions/connections/connection-hub-token-storage-README.md). |
| Connection Hub's props about those tokens: user props under `delegated_to_kdcube.` (connected accounts, account index, consent demands) | PostgreSQL `user_bundle_props` | They describe the tokens above; without the tokens they would point at accounts that are not connected. |
| Runtime-record folders: `login-attempts`, `card-credentials`, `oauth-refresh-tokens` | the runtime secrets folder | Runtime state of this deployment, not configuration. |
| Per-user secrets and user props without `--include-user-data` | provider; PostgreSQL | Users' data leaves only when asked for. |

Where each filter runs:

- **Secret keys** (both paths): in a provider-backed export the runtime drops
  the left-out keys from the inventory before the manifest is frozen, so
  nothing left out is ever read; in a `secrets-file` export the CLI checks each
  record's name before reading it. The CLI's writer applies the same rules
  again.
- **User props** (both paths): the `SELECT` on `user_bundle_props` itself leaves
  Connection Hub's props out (`WHERE NOT starts_with(key, <prefix>)`, the
  prefixes passed as a parameter), so those rows never leave the database and
  never count towards the 20000-row limit. The runtime and the CLI drop them
  again in Python whatever the database returns.

The CLI prints counts only, never a secret name or value.

## 4) Output layout

```text
<out-dir>/
├── assembly.yaml
├── gateway.yaml
├── economics.yaml
├── bundles.yaml
├── secrets.yaml                         platform secrets, nested
│                                        + users root (only with --include-user-data):
│                                          users.<user>.bundles.<bundle>.secrets
│                                          users.<user>.secrets       (user secrets of no app)
├── bundles.secrets.yaml                 bundles.items[]: { id, secrets: <nested app secrets> }
├── secrets/                             0700 folders, 0600 files, the file store's own layout
│   └── <bundle>/
│       ├── <key>.json                   {"value": "..."}  one app secret
│       └── users/<user>/<key>.json      one per-user secret (only with --include-user-data)
└── user-data/                           only with --include-user-data
    └── user_bundle_props.json           {schema: kdcube.export.user_bundle_props.v1,
                                          rows: [{user_id, bundle_id, key, subsystem, value_json}]}
```

The same values appear in `secrets/` and in the two YAML files. Both YAML files
are nested (`provider.token` is written as `provider: {token: ...}`), the shape
every secrets reader resolves. Two keys where one is below the other
(`token` and `token.child`) cannot both be nested: the export names the two
keys, removes the `secrets/` folder it created, and refuses, rather than drop
one. An existing `secrets/` in the out dir, a symbolic link in the source
folder, or an existing descriptor pair (unless `--replace-descriptor-files`)
also refuses (`--replace-descriptor-files` applies to a provider-backed export only).

`kdcube config export --aws-region ... --aws-sm-prefix ...` without an
initialized workdir is a separate, older path: it reads only the live bundle
descriptors (`bundles.yaml`, `bundles.secrets.yaml`) from AWS Secrets Manager.

## 5) How the export is consumed

```text
                         <out-dir>  (one export)
                              │
        ┌─────────────────────┼──────────────────────────────┐
        ▼                     ▼                              ▼
 kdcube config import   copy secrets/ by hand          cloud secrets intake
 (another local         (secrets-file target)          (ECS provisioning)
  deployment)                                          
        │                     │                              │
 descriptors applied;   cp -Rp secrets/. into          SECRETS_YAML         = secrets.yaml
 provider-backed:       <workdir>/config/secrets/      BUNDLES_SECRETS_YAML = bundles.secrets.yaml
   secrets.yaml +       before kdcube start,                 │
   bundles.secrets.yaml owned by the runtime user            ▼
   upserted into the                                   AWS Secrets Manager, prefix kdcube/<tenant>/<project>:
   store (--yes);                                        platform keys  → one secret per key
 secrets-file:                                           users subtree  → <prefix>/users/.../secrets
   the pair is staged                                    each bundle    → <prefix>/bundles/<id>/secrets
   as file authority                                                      (the secrets: block as JSON)
                                                       the aws-sm reader resolves a.b inside a document
```

- **`kdcube config import`** applies the descriptors. On a provider-backed
  target, `--yes` writes `platform`, `users` and every `secrets:` block into the
  store as governed upserts; on a `secrets-file` target the pair is staged as
  file authority (run it without `--yes`). Import does not place `secrets/` or
  the props.
- **`secrets/` on a `secrets-file` target**: copy it with its modes before
  `kdcube start` (`cp -Rp "$OUT_DIR/secrets/." "$WORKDIR/config/secrets/"`),
  owned by the user the runtime runs as.
- **The cloud secrets intake** reads only the two YAML files. It accepts a
  `secrets.yaml` with only the `platform` and `users` roots, a user entry with
  only `secrets` and `bundles`, and `bundles.secrets.yaml` items with a unique
  `id` and a `secrets` mapping; the export writes exactly that shape.
- **User props** have no cloud intake. They reach a cloud deployment through
  a governed import into its running runtime after deploy, which inserts rows
  without overwriting and reports equal and different rows; that import is
  not built yet, so today the props are not bootstrapped in the cloud.

## 6) Code

| Part | Code |
| --- | --- |
| CLI command, source selection | `kdcube_cli/cli.py` (`config export`, `_export_runtime_secrets`, `_write_bootstrap_export`) |
| The one writer (folder, nesting, users root, filters) | `kdcube_cli/runtime_secrets_export.py` |
| Bootstrap transaction, inventory filter, props read | `kdcube_ai_app/apps/chat/proc/rest/management/secret_export_routes.py`, `secret_runtime.py` |
| Import of the pair | `kdcube_cli/management/secret_descriptors.py` |
