---
id: ks:docs/sdk/storage/git-store-README.md
title: "Git Store"
summary: "Shared git subprocess transport helper: descriptor-backed auth, remote normalization, and the environment boundary used by platform workspaces and session stores."
tags: ["sdk", "storage", "git", "workspace", "session-store", "auth"]
keywords: ["git store", "git auth", "workspace repo", "claude session store", "GIT_HTTP_TOKEN", "GIT_SSH_COMMAND", "normalize git remote"]
see_also:
  - ks:docs/sdk/storage/sdk-store-README.md
  - ks:docs/sdk/storage/cache-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-storage-cache-README.md
  - ks:docs/README.md
---
# Git Store

This page describes the shared git subprocess helper used by the platform for:

- git-backed bundle materialization
- ReAct workspace lineage publishing
- Claude Code git-backed session stores
- bundle-side git workspaces that reuse the same platform helper

It is not a general VCS abstraction.
It is the platform contract for how git subprocesses receive auth and transport settings.

## Location

Shared helper:

`src/kdcube-ai-app/kdcube_ai_app/infra/git/auth.py`

Main exported helpers:

- `build_git_env(...)`
- `normalize_git_remote_url(...)`
- `ssh_url_to_https_url(...)`

## What It Does

The helper centralizes four concerns:

1. build a per-subprocess git env dict
2. resolve descriptor-backed HTTPS token auth
3. resolve descriptor-backed SSH transport settings
4. normalize SSH-style remotes to `https://` when PAT auth is configured

Typical use:

```python
from kdcube_ai_app.infra.git.auth import build_git_env, normalize_git_remote_url

repo_url = normalize_git_remote_url(runtime_ctx.workspace_git_repo)
env = build_git_env()
subprocess.run(["git", "fetch", "--prune", "origin"], env=env, check=True)
```

## Source Of Truth

Git auth should be treated as subprocess transport configuration.

Preferred resolution order:

- explicit call-site override
- descriptor-backed settings / secrets
- inherited process environment

In practice the helper resolves:

- HTTPS token:
  - `services.git.http_token`
  - `services.git.http_user`
- SSH transport:
  - `services.git.git_ssh_key_path`
  - `services.git.git_ssh_known_hosts`
  - `services.git.git_ssh_strict_host_key_checking`

These are exposed through `get_settings()` / `get_secret()`.

## Environment Boundary

This is the important runtime rule.

`build_git_env(...)` does **not** mutate the processor process environment.

It:

- copies a base env mapping
- overlays descriptor-backed git settings
- overlays explicit per-call overrides
- returns a subprocess-only env dict

That means:

- inherited processor `GIT_*` variables are shared by design across apps running in the same processor
- explicit helper overrides are local to the git subprocess only
- one app cannot make its git override “stick” globally unless it writes directly into `os.environ`, which platform code should not do

## HTTPS vs SSH

Accepted remote forms:

- `https://github.com/org/repo.git`
- `http://...` where intentionally used
- `git@github.com:org/repo.git`
- `ssh://git@github.com/org/repo.git`

All of these are valid inputs to the shared helper. The effective transport then
depends on which auth mode is configured.

### HTTPS token path

If a PAT is configured, the helper:

- prepares `GIT_ASKPASS`
- sets `GIT_HTTP_TOKEN`
- sets `GIT_HTTP_USER`
- disables terminal prompting

And if the configured remote is SSH-style, `normalize_git_remote_url(...)` rewrites:

- `git@github.com:org/repo.git`
- `ssh://git@github.com/org/repo.git`

to:

- `https://github.com/org/repo.git`

So a descriptor may still contain an SSH-style remote while the effective git
transport becomes HTTPS because PAT auth was configured. That is intentional.

### SSH path

If PAT auth is not selected, the helper can synthesize:

- `GIT_SSH_COMMAND`

from:

- `GIT_SSH_KEY_PATH`
- `GIT_SSH_KNOWN_HOSTS`
- `GIT_SSH_STRICT_HOST_KEY_CHECKING`

This is how host-key verification and SSH identity selection are passed into git subprocesses.

SSH therefore requires more than just an SSH-style repo URL. It also requires
the matching SSH identity and host-verification inputs to be configured and
available at runtime.

## What Uses It

Tracked platform users include:

- git bundle materialization:
  - `infra/plugin/git_bundle.py`
- ReAct workspace publish:
  - `apps/chat/sdk/solutions/react/v2/git_workspace.py`
  - `apps/chat/sdk/solutions/react/v3/git_workspace.py`
- Claude Code session-store runtime:
  - `apps/chat/sdk/solutions/claude_code/runtime.py`

Bundle code may also reuse the same helper instead of reimplementing PAT / SSH handling locally.

## What This Page Does Not Cover

This page does not define:

- where mutable bundle-local git checkouts should live
- how bundle local storage is allocated
- how conversation artifacts are stored

Use these for that:

- [bundle-storage-cache-README.md](../bundle/bundle-storage-cache-README.md)
- [sdk-store-README.md](sdk-store-README.md)

## Recommended Rule

If bundle or platform code needs git:

- do not invent a custom git auth helper
- do not mutate process-global `os.environ` to make git work
- build a subprocess env dict and pass it only to the git command
- normalize remotes through the shared helper so PAT and SSH behavior stay consistent across the platform
