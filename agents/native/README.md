---
id: repo:kdcube-ai-app/agents/native/README.md
title: "Run the Native ReAct Agent"
summary: "Run KDCube native ReAct directly from Python with YAML-selected tools and skills."
tags: ["agents", "native-react", "harness", "standalone", "demonstration", "web-search", "web-fetch"]
keywords: ["ReactSolverV2", "DirectAgentHarness", "KDCube Web Search", "KDCube Web Fetch", "Postgres conversation", "ChatCommunicator", "accounting"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/agents/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Run the Native ReAct Agent

## What it is

This directory runs KDCube's `ReactSolverV2` in your Python process.
`config.local.yaml` selects agent behavior and tool settings;
`descriptors.local/` selects the model, storage, and support services. It runs
directly as a Python process from this checkout.

## Run it

```bash
cd agents/native
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_local.py --provider anthropic
cp config.template.yaml config.local.yaml
docker compose --env-file .env -f compose.yaml up -d --wait
cd ../..
docker build -t py-code-exec:latest -f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec app/ai-app
cd agents/native
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
`agent.input.conversation_id: native-demo`. Run it again with those values to
continue that conversation, or choose them explicitly:

```bash
.venv/bin/python agent.py \
  --user-id alice \
  --conversation-id release-research \
  --session-id terminal-1
```

The provider key prompt is hidden. Local secrets and generated descriptors are
ignored by Git. `setup_local.py` is this one-time preparation command; it does
not participate in a turn or own agent configuration.

The first command creates this runner's `.venv`; no prebuilt environment is
shipped. Installing `requirements.txt` installs the SDK and this runner's Python
dependencies. Chromium is required by the enabled PDF renderer. The Docker
build creates the `py-code-exec:latest` image required by the enabled isolated
code-execution tool. `--infra-check` verifies both prerequisites before model
spend.

To use an on-host model, prepare the same directory with no provider secret:

```bash
.venv/bin/python setup_local.py --provider none
cp config.template.yaml config.local.yaml
```

Then select the exact model tag in `descriptors.local/assembly.yaml`:

```yaml
models:
  default_llm_provider: custom
  default_llm_model_id: <model-tag-loaded-by-your-local-runtime>

services:
  llm:
    custom:
      endpoint: http://127.0.0.1:11500/generate
      num_ctx: 32768
```

Start Ollama in one terminal:

```bash
ollama serve
```

Load the selected model and start the KDCube protocol gateway in a second
terminal:

```bash
cd agents/native
ollama pull <model-tag-loaded-by-your-local-runtime>
.venv/bin/python -m uvicorn \
  kdcube_ai_app.apps.models_gateway.app:app \
  --host 127.0.0.1 --port 11500
```

`agent.py --check` must print the exact `custom/<model-tag>`, endpoint, and
context budget before a model call is made. Native ReAct supplies its action
protocol through instructions and parsing; the model provider does not need a
provider-native tool-calling API. The selected model must still follow that
protocol reliably within the configured context window.

## What the demo shows

Turn one hosts a research-request attachment, searches with
[KDCube Web Search](../../mcp/web-search/README.md), and inspects a selected
result with KDCube Web Fetch. Turn two makes the model author Python that uses
`openpyxl`. That generated program calls the enabled Web Search tool through
`agent_io_tools.tool_call` for an additional verification query, then creates
an XLSX and HTML. It consumes Web Search rows from the tool's `ret` result, and
the runner verifies that a returned title and URL reached the workbook. The
generated program runs in the isolated turn workspace;
the Web tool runs in the trusted supervisor under the same YAML allow policy.
The agent then calls `rendering_tools.write_pdf` to turn the HTML into a
polished PDF. It does not generate PDF bytes in Python.

The YAML also enables `write_docx` for Markdown and `write_pptx` for
section-based HTML. The default demonstration calls only `write_pdf`; alter the
second prompt to exercise either sibling operation.

Native adds a third turn in another conversation and uses `react.memsearch` to
recover its earlier research. A successful run ends with `demonstration: PASS`.
The second conversation is the explicit
`agent.input.recall_conversation_id`. Inspect
`output/runs/<user>/<conversation>/<run>/evidence.json`; it points to durable
records in `output/kdcube-storage`, including the execution ZIP containing
`pkg/user_code.py`.

## Change the demo

Edit `config.local.yaml` to change the `lite:core` instruction profile,
`additional_instructions`, local ingress, run directory, tools, skills, topic,
or limits.
Its `agent.input` section selects the local caller session, durable
conversation, and recall conversation. Tenant and project come from
`descriptors.local/assembly.yaml`; this runner's stable agent ID is `native`.
The SDK preserves the ReAct protocol and automatically adds standard blocks for
the enabled exec, rendering, and web tool families. Web Search and Web Fetch
share the allowlist, blocklist, and SSRF policy under
`agent.tools[id=web].settings`. Edit
`descriptors.local/assembly.yaml` for the model provider and ID, storage, and isolated executor;
edit `descriptors.local/secrets.yaml` for credentials. The shipped model is
`claude-haiku-4-5-20251001`.

For an on-host run, `services.llm.custom.endpoint` is the shared model gateway
and `num_ctx` is the context budget sent on every request. Model-specific
override lists are not part of this example; change the single selected model
and shared context budget directly.

To add a local Python tool, point a new `agent.tools` source row at an
importable `module` or a local `ref`, give it an `alias`, and list the exact
callable names under `allowed`. The SDK `ToolSubsystem` introspects that source
and uses the same selection for the model catalog, direct calls, and
generated-code supervisor admission. The canonical `ToolSubsystem` is the
runner's tool registry.
See [Tool Subsystem](../../app/ai-app/docs/sdk/tools/tool-subsystem-README.md).

Executor image, timeout, network, and workspace limits live under
`platform.services.proc.exec` in `descriptors.local/assembly.yaml`. The full
storage and inspection procedure is in
[the executable recipe](../../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md).
Profile choices and composition order are in
[Direct Agent Instruction Profiles](../../app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md).
