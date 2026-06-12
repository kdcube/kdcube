---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-accounting-README.md
title: "Claude Code Accounting"
summary: "How Claude Code runs are emitted into KDCube accounting as accountable LLM usage, including model resolution, usage extraction, cost fallback, and calculator compatibility."
tags: ["sdk", "agents", "claude", "claude-code", "accounting", "economics"]
keywords: ["track_llm", "cost_usd", "stream-json", "claude-sonnet-4-6", "claude-opus-4-6", "runtime=claude_code"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-workspace-bootstrap-README.md
---
# Claude Code Accounting

Claude Code invocations executed through the KDCube SDK are accounted as standard LLM service usage.

This means:

- `service_type = "llm"`
- `provider = "anthropic"`
- `model_or_service = <resolved Claude model>`
- `metadata.runtime = "claude_code"`

The accounting implementation lives in:

- [src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/agent.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/agent.py)
- [src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/streaming.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/claude_code/streaming.py)
- [src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py](../../../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py)

## Event shape

Claude Code accounting events look like normal accountable LLM events.

Important fields:

- `service_type: "llm"`
- `provider: "anthropic"`
- `model_or_service`
- `usage`
- `success`
- `error_message`
- `metadata.runtime: "claude_code"`

Typical metadata includes:

- `agent_name`
- `turn_kind`
- `resume_existing`
- `claude_session_id`
- `workspace_path`
- `allowed_tools`
- `additional_directories`
- `permission_mode`
- `conversation_id`
- `exit_code`
- `delta_count`
- `duration_ms`
- `api_duration_ms`
- `cost_usd`
- `model_resolution`

## How usage is extracted

The Claude CLI is run in `stream-json` mode. The SDK parses the emitted events and accumulates:

- `input_tokens`
- `output_tokens`
- `thinking_tokens`
- `cache_creation_tokens`
- `cache_read_tokens`
- `cache_creation` bucket detail
- `requests`
- `cost_usd`

The resolved model name is also extracted from the same stream.

Because the CLI may emit both incremental message events and a final result event, the parser avoids double-counting by accumulating message usage separately from the final result usage.

## Success and failure accounting

Both successful and failed Claude Code runs emit accountable events.

- successful runs are written with `success=true`
- failed runs are written with `success=false`
- `error_message` is populated when available

If the CLI reports usage before failing, that usage is still preserved in the accounting event.

## Model naming

The actual Claude CLI stream may emit exact resolved model names such as:

- `claude-sonnet-4-6`
- `claude-opus-4-6`

KDCube also supports alias-aware pricing lookup for Anthropic models, so the calculator can resolve:

- `sonnet`
- `opus`
- `haiku`
- short Claude names like `claude-sonnet` and `claude-opus`

to their priced canonical entries.

## Pricing and calculator compatibility

The accounting calculator supports Claude Code in 2 ways:

1. priced model lookup
2. direct `cost_usd` fallback

That means Claude Code spend remains visible even if:

- the model is emitted under an alias
- the CLI resolves a name that does not yet have a pinned entry in the price table

When exact pricing is known, KDCube uses the price table. When it is not, the system can still preserve the provider-reported `cost_usd`.

## Bundle integration

A bundle can surface Claude Code spend per turn by reading:

- `ClaudeCodeRunResult.model`
- `ClaudeCodeRunResult.usage`
- `ClaudeCodeRunResult.cost_usd`

and storing those values in conversation message metadata or bundle-specific turn payloads.

This is the recommended path for UI chips such as:

- selected/resolved model
- input/output tokens
- cache read/write tokens
- per-turn USD cost
