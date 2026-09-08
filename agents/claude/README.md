---
id: repo:kdcube-ai-app/agents/claude/README.md
title: "Run the Claude Code Agent"
summary: "Run Claude Code through the KDCube harness with a Git-backed transcript and durable conversation."
tags: ["agents", "claude-code", "harness", "conversation", "standalone", "web-search", "web-fetch", "mcp"]
keywords: ["ClaudeCodeAgent", "KDCube Web Search MCP", "KDCube Web Fetch", "Claude transcript", "Git session store", "ChatCommunicator", "accounting"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/agents/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Run the Claude Code Agent

## What it is

This directory runs the local Claude Code CLI through `ClaudeCodeAgent`. The
harness stores its conversation in Postgres and configured storage; the SDK
stores its resumable CLI transcript on a per-conversation Git branch. It runs
directly as a Python process from this checkout.

## Run it

Install and authenticate Claude Code, then run:

```bash
cd agents/claude
claude --version
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_local.py --provider none
cp config.template.yaml config.local.yaml
docker compose --env-file .env -f compose.yaml up -d --wait
cd ../..
docker build -t py-code-exec:latest -f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec app/ai-app
cd agents/claude
.venv/bin/python agent.py --check
.venv/bin/python agent.py --infra-check
.venv/bin/python agent.py
```

For an ongoing terminal conversation, run:

```bash
.venv/bin/python agent.py --interactive \
  --user-id alice --conversation-id terminal-chat --session-id terminal-1
```

For the development-only Telegram webhook, add the bot token and webhook
secret to `descriptors.local/secrets.yaml`, expose local port `8787` through an
HTTPS tunnel, register that URL with Telegram, then run:

```bash
.venv/bin/python agent.py --telegram-local
```

The complete webhook registration and process-local delivery boundary are in the
[executable recipe](../../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md#10-connect-a-local-telegram-bot).

The default run uses `agent.input.user_id: demo-user` and
`agent.input.conversation_id: claude-demo`. Run it again with those values to
continue the same durable conversation and Git-backed Claude transcript, or
override them:

```bash
.venv/bin/python agent.py \
  --user-id alice \
  --conversation-id release-research \
  --session-id terminal-1
```

For API-key execution, use `--provider anthropic`; the setup command writes the
key to `platform.services.anthropic.api_key` in the generated secrets
descriptor. The runner projects that descriptor field to the standard
`ANTHROPIC_API_KEY` subprocess input without making environment variables a
user-facing configuration surface.

The first Python command creates this runner's `.venv`; no prebuilt environment
is shipped. Installing `requirements.txt` installs the SDK and this runner's
Python dependencies. Chromium is required by the enabled PDF renderer. The
Docker build creates the `py-code-exec:latest` image required by the enabled
isolated Python tool. `--infra-check` verifies both prerequisites before model
spend, along with the Git transcript store.

## What the demo shows

Turn one reads a hosted research-request attachment, searches through the local
[KDCube Web Search MCP](../../mcp/web-search/README.md), and inspects a selected
result with its Web Fetch operation. The generated Claude workspace denies
`WebSearch`, `WebFetch`, and `Bash`, so search and code execution use the
configured KDCube boundaries.

Turn two resumes the same Git-backed Claude session. Claude authors Python
using `openpyxl`; that program makes an additional KDCube Web Search call
through `agent_io_tools.tool_call` and creates an XLSX and HTML in the isolated
turn workspace. It consumes Web Search rows from the tool's `ret` result, and
the runner verifies that a returned title and URL reached the workbook. The
trusted supervisor executes the Web call under the same
descriptor-selected tool policy. A second MCP operation calls
`rendering_tools.write_pdf` to render the HTML into a polished PDF. The same
server exposes Markdown-to-DOCX and section-HTML-to-PPTX operations.

The harness independently records both turns and accounting in KDCube storage.
Inspect `output/runs/<user>/<conversation>/<run>/evidence.json`; it points to
`output/kdcube-storage`, the execution ZIP containing `pkg/user_code.py`, and
the Claude transcript branch. A successful run ends with `demonstration: PASS`.

## Change the demo

Edit `config.local.yaml` to change Claude's command and timeout, the
`workspace-files` instruction profile, `additional_instructions`, local
ingress, run directory, tools, skills, or task. Its `agent.input` section selects the local caller
session and durable conversation. Tenant and project come from
`descriptors.local/assembly.yaml`; the Git transcript branch and Claude session
ID add this runner's stable `claude` agent ID. The profile becomes generated
`CLAUDE.md`; selected KDCube skills become native `.claude/skills` entries. Web
Search and Web Fetch share the egress policy under
`agent.tools[id=web].settings`. Edit
`descriptors.local/assembly.yaml` to select the Anthropic model and a private
Git transcript remote, and put its HTTPS token in
`descriptors.local/secrets.yaml` at `platform.services.git.http_token`.
The YAML source rows use the same canonical `module`, `alias`, `allowed`, and
`runtime` contract as the other examples. The Claude adapter translates the
sample's selected canonical tool IDs into its local stdio MCP names and writes
those exact permissions into Claude's workspace at run time. A new domain tool
also needs a Claude-facing MCP schema adapter; the descriptor `allowed` list
still controls whether the trusted runtime may execute it. The shared descriptor template ships with
`claude-haiku-4-5-20251001` selected. The Claude Code
subprocess requires `default_llm_provider: anthropic`; direct agents that use
KDCube `ModelServiceBase`, including the Native and LangGraph examples, can
instead select `provider: custom`. Tool and skill selection stays
in `config.local.yaml`; executor, storage, and Git settings stay in the
standard descriptors.
The exact composition and process-workspace/artifact-workspace distinction are
documented in
[Direct Agent Instruction Profiles](../../app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md).
