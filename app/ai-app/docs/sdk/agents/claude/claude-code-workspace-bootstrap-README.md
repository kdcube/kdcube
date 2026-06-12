---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-workspace-bootstrap-README.md
title: "Claude Code Workspace Management"
summary: "How KDCube manages Claude Code session continuity through a bundle-controlled local root and an optional git-backed per-conversation session store."
tags: ["sdk", "agents", "claude", "claude-code", "workspace", "git", "bootstrap"]
keywords:
  [
    "Claude Code workspace",
    "Claude session store",
    "claude_session_id",
    "git-backed Claude session",
    "bundle-controlled workspace root",
    "run_claude_code_turn",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-accounting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/runtime.py
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/agent.py
---
# Claude Code Workspace Management

KDCube separates two concerns:

- `ClaudeCodeAgent` is the generic runner
- the caller or bundle owns workspace/bootstrap policy

That split matters because Claude continuity is not restored from KDCube's own
conversation JSON. Continuity comes from Claude's own local session files plus
the stable `claude_session_id`.

The current runtime support for that lives in:

- [src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/agent.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/agent.py)
- [src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/runtime.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/runtime.py)
- [src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/types.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/types.py)

## Core model

Claude Code workspace management is defined by these rules:

- the bundle chooses the local root Claude should use
- the agent runner does not hardcode that root
- Claude continuity is anchored to:
  - `user_id`
  - `conversation_id`
  - `agent_name`
- KDCube may optionally bootstrap the chosen local Claude root from git before a turn
- after the turn, KDCube may publish the mutated Claude root back to git

So the authoritative continuity substrate is:

- the Claude-created files under the bundle-chosen local Claude root

not:

- KDCube conversation message history
- the final assistant transcript stored by the bundle
- accounting events

## Local vs git session store

Claude session storage supports two implementations:

- `local`
- `git`

Env vars:

```text
CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION=local|git
CLAUDE_CODE_SESSION_GIT_REPO=<remote git repo>
```

Meaning:

- `local`
  - continuity depends on local disk persistence
  - no bootstrap or publish is performed

- `git`
  - each Claude conversation/agent gets its own remote branch
  - the local Claude root is bootstrapped from that branch before a regular turn
  - changes are published back after the regular turn

## Bundle-controlled local root

The runner remains generic:

- `ClaudeCodeAgent.from_current_context(..., workspace_path=...)`
- `ClaudeCodeAgentConfig.workspace_path`

So the SDK does not declare one global Claude root such as `/var/lib/claude/...`.

Instead:

- the bundle decides the local path
- the runtime bootstrap layer hydrates that exact path
- the publish layer persists that same path

Example valid choices:

- `<workspace_root>/.claude`
- `<workspace_root>/runtime/claude`
- a whole caller-owned Claude workspace root

The important rule is determinism: the bundle must consistently point Claude at
the same logical local root for the same continuity boundary.

For cron/background pipelines this root should be tied to the service-owned
identity and logical conversation, for example:

```text
<bundle-storage-root>/_news/claude-code-session
```

The same service-owned identity should also be used in
`ClaudeCodeBinding.user_id` and `ClaudeCodeSessionStoreConfig.user_id`. This is
what keeps a scheduled pipeline from accidentally sharing Claude session state
with an interactive user conversation.

## Branch identity

The git-backed session store uses one branch per:

- tenant
- project
- user
- conversation
- agent name

Shape:

```text
refs/heads/kdcube/claude/<tenant>/<project>/<user_id>/<conversation_id>/<agent_name>
```

This matches the same continuity boundary used for `claude_session_id`.

## Turn lifecycle

The high-level runtime entry is:

```python
result = await run_claude_code_turn(
    agent=agent,
    prompt=prompt,
    kind="regular",
    resume_existing=False,
    session_store=ClaudeCodeSessionStoreConfig(...),
    refresh_support_files=refresh_fn,
)
```

Behavior:

### Regular turn

If `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION=git`:

1. bootstrap the local Claude root from the conversation branch
2. optionally refresh bundle-owned support files inside that root
3. run Claude
4. publish the mutated Claude root back to the same branch

Bootstrap is rerun-safe:

- the local Claude session checkout can already exist
- the workspace branch can already be checked out there
- bootstrap refreshes that dedicated local checkout from the stored lineage branch

If Claude still fails with a stale local-session error such as "Session ID ...
is already in use", the runtime resets that dedicated local checkout and
retries the turn once in resume mode.

### Followup / steer

Current default behavior:

- no git bootstrap
- no git publish
- the local root is reused as-is inside the current live environment

This keeps followup/steer cheap for the active runtime. If a future product flow
needs cold-start followup/steer on another node, the caller can widen the
bootstrap/publish turn-kind policy.

## What goes into the git branch

Only the Claude continuity substrate should be published there.

Good contents:

- Claude session files needed by `--resume`
- minimal KDCube companion files inside that same root, if the bundle requires them

Do not put these there:

- the full product conversation JSON
- accounting events
- unrelated bundle storage
- general project output artifacts

The purpose of this branch is:

- preserve Claude continuity

not:

- replace KDCube conversation storage

## Isolation requirements

The Claude session git store follows the same security principle as React's
git-backed workspace storage:

- local runtime should only see the assigned conversation branch
- it must not expose a broad shared repo view with other users' branches

So the local bootstrapped root visible to the Claude run should correspond only
to its own:

- tenant
- project
- user
- conversation
- agent

## Assembly descriptor support

The installer reads Claude session-store settings from `assembly.yaml`:

```yaml
storage:
  claude_code_session:
    type: local   # local | git
    repo: ""      # used only when type=git
```

It maps those values into `.env.proc`:

- `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `CLAUDE_CODE_SESSION_GIT_REPO`

This makes Claude session-store policy deployable at the same layer as React's
git-backed workspace settings.

## Relationship to the core Claude runner

The runner and the workspace/bootstrap layer are intentionally separate.

`ClaudeCodeAgent` is responsible for:

- building Claude CLI args
- binding deterministic `claude_session_id`
- running the subprocess
- streaming deltas and steps
- optionally extracting framed structured JSON events from streamed assistant text
- enforcing optional per-turn timeout
- returning structured usage/model/cost/failure results

The runtime/bootstrap layer is responsible for:

- local vs git session-store policy
- branch naming
- bootstrap before the turn
- publish after the turn
- self-healing refresh of the dedicated local Claude session checkout

That separation keeps bundles flexible while still giving the platform a
standard continuity mechanism.

## Related docs

- session identity and runner behavior:
  - [claude-code-README.md](claude-code-README.md)
- Claude accounting:
  - [claude-code-accounting-README.md](claude-code-accounting-README.md)
- service env reference:
  - [docs/configuration/service-runtime-configuration-mapping-README.md](../../../configuration/service-runtime-configuration-mapping-README.md)
- assembly schema:
  - [docs/configuration/assembly-descriptor-README.md](../../../configuration/assembly-descriptor-README.md)
