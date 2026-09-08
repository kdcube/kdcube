---
id: repo:kdcube-ai-app/agents/langgraph/README.md
title: "Run the LangGraph Agent"
summary: "Keep a LangGraph workflow while adding durable user conversations, earlier-work recall, isolated code and file work, and inspectable model usage through the KDCube Harness."
tags: ["agents", "langgraph", "langchain", "harness", "accounting", "standalone", "web-search", "web-fetch", "conversation-search"]
keywords: ["KDCubeChatModel", "KDCube Web Search", "KDCube Web Fetch", "conversation_tools.search", "stream_model_text_tracked", "AsyncPostgresSaver", "ChatCommunicator"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/agents/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Run the LangGraph Agent

## What it is

This directory runs LangChain `create_agent` through KDCube's
`KDCubeChatModel`. Model streaming is accounted by the harness, while Postgres
stores both the conversation index and LangGraph checkpoints. It runs directly
as a Python process from this checkout.
Use it to keep a LangGraph workflow and checkpoints while adding the harness's
files, isolated code execution, cross-conversation recall, and usage evidence.
The graph remains yours to shape; the harness supplies the reusable boundaries
around it so the workflow can produce files, resume work, and explain its cost.

## Run it

```bash
cd agents/langgraph
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_local.py --provider anthropic
cp config.template.yaml config.local.yaml
docker compose --env-file .env -f compose.yaml up -d --wait
cd ../..
docker build -t py-code-exec:latest -f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec app/ai-app
cd agents/langgraph
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

The default run uses `agent.input.user_id: demo-user`,
`agent.input.conversation_id: langgraph-demo`, and
`agent.input.recall_conversation_id: langgraph-recall-demo`. Run it again with
those values to continue the same conversation and graph checkpoint, or
override them:

```bash
.venv/bin/python agent.py \
  --user-id alice \
  --conversation-id release-research \
  --session-id terminal-1 \
  --recall-conversation-id release-research-recall
```

The provider key prompt is hidden. Local secrets and generated descriptors are
ignored by Git.

The first command creates this runner's `.venv`; no prebuilt environment is
shipped. Installing `requirements.txt` installs the SDK and this runner's Python
dependencies. Chromium is required by the enabled PDF renderer. The Docker
build creates the `py-code-exec:latest` image required by the enabled isolated
Python tool. `--infra-check` verifies both prerequisites before model spend.

LangGraph uses the same descriptor-owned model route as the Native agent. For an
on-host model, run `setup_local.py --provider none`, set
`models.default_llm_provider: custom` and the exact
`models.default_llm_model_id` in `descriptors.local/assembly.yaml`, then start
the KDCube models gateway at the configured `services.llm.custom.endpoint`.
The full commands and capacity notes are in the
[executable recipe](../../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md#use-an-on-host-model).

## What the demo shows

Turn one hosts a research-request attachment, searches with
[KDCube Web Search](../../mcp/web-search/README.md), and inspects a selected
result with KDCube Web Fetch. Turn two resumes the same Postgres-backed graph
thread. The model authors Python using `openpyxl`; the program makes an
additional Web Search call through `agent_io_tools.tool_call` and creates an
XLSX and HTML in the isolated turn workspace. It consumes Web Search rows from
the tool's `ret` result, and the runner verifies that a returned title and URL
reached the workbook. The trusted supervisor executes
the Web call under the same descriptor-selected tool policy. `write_pdf` then
renders the HTML into a polished PDF.

The recall check starts `langgraph-recall-demo`, a different conversation for
the same user, and must call `conversation_search` to recover the earlier research.
That LangChain tool is a small adapter over the descriptor-selected SDK tool
`conversation_tools.search`; caller identity comes from the harness, not from
model arguments.

The same YAML enables `write_docx` for Markdown and `write_pptx` for
section-based HTML. A successful run proves live events, accounted model calls,
durable KDCube turns, Postgres graph checkpoints, isolated code execution, and
document rendering; it ends with `demonstration: PASS`.

Inspect `output/runs/<user>/<conversation>/<run>/evidence.json`; it points to
durable records in `output/kdcube-storage`, including the execution ZIP
containing `pkg/user_code.py` and the separate recall-conversation turn.

## Change the demo

Edit `config.local.yaml` to change the `workspace-files` instruction profile,
`additional_instructions`, local ingress, run directory, tools, skills, topic,
or limits. The
`agent.input` section selects the local caller session, durable conversation,
and recall conversation. Tenant and project come from
`descriptors.local/assembly.yaml`;
the private LangGraph checkpoint key adds this runner's stable `langgraph`
agent ID. The profile teaches the current-turn artifact workspace; selected skill text and
enabled capability guidance are composed before the administrator override.
Web Search and Web Fetch share the allowlist, blocklist, and SSRF policy under
`agent.tools[id=web].settings`. Edit
`descriptors.local/assembly.yaml` for model provider, model ID, and
infrastructure; edit `descriptors.local/secrets.yaml` for credentials. The shipped model is
`claude-haiku-4-5-20251001`. Add a Python source through the canonical
`agent.tools` `module`/`ref`, `alias`, and `allowed` fields; add only the small
LangChain schema adapter in `tools.py`. The built-in execution and renderer
wrappers are turn-bound, so new file-producing adapters should preserve the
same current-turn path contract. Canonical discovery and admission are in
[Tool Subsystem](../../app/ai-app/docs/sdk/tools/tool-subsystem-README.md).
The exact composition is documented in
[Direct Agent Instruction Profiles](../../app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md).
