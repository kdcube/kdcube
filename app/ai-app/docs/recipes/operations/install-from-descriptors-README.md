---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/install-from-descriptors-README.md
title: "Install KDCube: From A Descriptor Set"
summary: "The CI/CD path: reproduce an environment from exported descriptors — export, align to the target machine, init, verify. Includes the field notes that bite real onboardings."
status: active
tags: ["operations", "install", "descriptors", "cicd", "export", "kdcube-cli"]
updated_at: 2026-07-07
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/install-clean-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/operate-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
---
# Install KDCube: From A Descriptor Set

Descriptors are the environment: `assembly.yaml` (topology, auth, infra,
host paths), `gateway.yaml`, `secrets.yaml`, `bundles.yaml`,
`bundles.secrets.yaml`, `economics.yaml`. An environment exported as a
descriptor set can be reviewed, versioned, aligned, and reproduced — that is
the CI/CD story. For a from-nothing start, use
[Clean Bootstrap](install-clean-README.md).

```text
source runtime                          target machine
$WORKDIR/config/*.yaml                  aligned descriptor set
        |                                       |
        v                                       v
kdcube config export --out-dir …   ->   kdcube init --descriptors-location …
        (review / edit / version)               |
                                                v
                                          kdcube start
```

## 1. Export from the source runtime

```shell
export OUT_DIR="$HOME/.kdcube/exports/${SOURCE_TENANT}__${SOURCE_PROJECT}-$(date +%Y%m%dT%H%M%S)"
kdcube config export --tenant "$SOURCE_TENANT" --project "$SOURCE_PROJECT" \
  --out-dir "$OUT_DIR" --include-platform-descriptors
```

Export translates local non-git bundle paths from runtime `/bundles/...`
back to host paths and keeps git-backed bundles as repo/ref/subdir. The full
export includes `secrets.yaml` and `bundles.secrets.yaml` with real values —
treat the directory as sensitive: never commit it, never paste from it.

With the `secrets-file` provider the full export also holds `secrets/`, the
runtime secrets folder in the layout the services read (`<bundle>/<key>.json`,
`<bundle>/users/<user>/<key>.json`), and repeats the app secrets as `secrets:`
blocks in `bundles.secrets.yaml`. Per-user secrets and the user props
(`user-data/user_bundle_props.json`) are included only with
`--include-user-data`; tokens Connection Hub acquires are never exported.

## 2. Align the set to the target machine

The exported files describe the ORIGIN machine. Before init on the target:

- **`assembly.yaml`**
  - `paths.host_bundles_path`: a directory that is a common ancestor of ALL
    local app checkouts on the target. It is bind-mounted as `/bundles`;
    every local `path:` in `bundles.yaml` MUST live under it.
  - infra topology: `postgres.host: postgres-db` / `redis.host: redis`
    means the CLI starts bundled compose infra; `localhost` (or a managed
    endpoint) means host-managed infra — the target must run its own
    Postgres/Redis (the platform ships a ready stack under
    `deployment/docker/local-infra-stack/`).
  - scan for origin-specific absolute paths beyond bundles (IdP DB paths
    and similar) and translate them — a foreign absolute path is a latent
    failure even for a disabled feature.
- **`bundles.yaml`**: rewrite every origin `path:` to the target's real
  checkout path (verify each directory exists), or disable apps the target
  does not need. Apps without `path:` and without `repo:` are built-in
  platform apps — they need neither and always work.
- **Local-bundle symlink audit (the #1 from-scratch breaker).** The
  platform copies bundle dirs with `copytree`, which FOLLOWS symlinks; an
  absolute-target symlink resolves on the host but dangles under the
  container's `/bundles` mount and kills the bundle's UI build and
  scheduler jobs with `[Errno 2] No such file or directory`. These links
  are often git-excluded, so `git status` is silent. For every local-path
  bundle:

  ```shell
  find "$BUNDLE_DIR" -type l -exec sh -c '
    for l; do t=$(readlink "$l");
      case "$t" in /*) echo "ABSOLUTE  $l -> $t";; *) echo "relative  $l -> $t";; esac
    done' _ {} +
  ```

  Repoint every ABSOLUTE link to a relative target inside the bundle
  (`ln -sfn ../../interface/x.yaml x.yaml`) or delete it if nothing needs
  it at runtime.
- **Secrets**: leave files as-is; prefer `--set-secret` overrides at init
  for service keys. Provider-bound secrets (Telegram bot tokens and
  similar) are origin-specific — the target needs its OWN, or those
  integrations simply stay dormant.
- Re-parse every edited YAML and re-grep the folder for leftover origin
  paths before init.

## 3. Init on the target, then start

```shell
kdcube init --path "$REPO" --descriptors-location "$OUT_DIR" \
  --tenant "$TENANT" --project "$PROJECT" --build
kdcube defaults --default-tenant "$TENANT" --default-project "$PROJECT"
# secrets-file export: place the runtime secrets folder before the first start
# (WORKDIR is the target runtime's workdir, the folder that holds config/)
[ -d "$OUT_DIR/secrets" ] && cp -Rp "$OUT_DIR/secrets/." "$WORKDIR/config/secrets/"
kdcube start
```

Neither `init` nor `config import` places `secrets/` yet, so copy it by hand
as above (modes kept, owned by the user the runtime runs as). With a declared
`secrets.runtime.root`, copy it into that root's host folder instead.

`init` refuses an already-initialized workdir: to reseed descriptors into
an EXISTING runtime use `kdcube config import --descriptors-location …
--include-platform-descriptors` (dry-run first), then `kdcube refresh`.

## 4. Verify like you mean it

Same three layers as the clean install — `kdcube info`, a real
`curl` 200 on `/platform/chat`, and a proc-log scan for
`widget:... build done` vs `build failed` / `copytree` / `No such file`
(see [Clean Bootstrap](install-clean-README.md), step "Start and verify").
A failing scan after a descriptor install is almost always a symlink missed
in step 2 — fix the link, `kdcube reload <app-id>`, re-scan.

## Field notes

Each of these cost a real onboarding:

- `init --build` builds images and STOPS. Nothing runs until `kdcube start`.
- `kdcube info` clean ≠ bundles built. Only the proc-log scan proves it.
- pgadmin restart-looping in bundled topology is the known invalid default
  email (`admin@kdcube.local` — pgadmin rejects the reserved `.local` TLD);
  fix the one line in `$WORKDIR/config/.env.postgres.setup`. It is an
  optional DB browser, never a reason to block "done".
- Repos and runtimes may pre-exist on a dev box. Preflight before cloning
  or initializing; a pre-existing runtime for the same tenant/project means
  `refresh` / `config import`, never `init`.
- Multiline shell paste: a trailing space after `\` breaks the
  continuation (`command not found: --project`). Prefer one-line commands.
