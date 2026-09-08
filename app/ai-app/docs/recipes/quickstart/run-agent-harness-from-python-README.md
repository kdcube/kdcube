---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
title: "Recipe: Run the Agent Harness from Python"
summary: "Run native ReAct, LangGraph, or Claude Code from SDK source with terminal or local Telegram input, Web Search and Web Fetch, isolated code execution, document rendering, durable conversations, and accounting."
status: current
tags: ["recipe", "quickstart", "agent-harness", "python", "native-react", "langgraph", "claude-code", "self-hosted", "terminal", "telegram", "web-search", "web-fetch", "rendering"]
keywords: ["run agent harness", "direct SDK agent", "terminal chat", "local Telegram webhook", "KDCube Web Search", "KDCube Web Fetch", "standard descriptors", "Redis", "Postgres", "Git transcript", "isolated code execution", "write_pdf", "write_docx", "write_pptx"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/agents/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/settle-your-solution-in-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/telegram-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Recipe: Run the Agent Harness from Python

Run an agent from a shell or IDE and give it KDCube Harness capabilities. The
sample process imports KDCube SDK source directly and runs as its own Python
process. Redis and Postgres are independent support services.

Choose one complete example:

```text
agents/
  native/       KDCube ReactSolverV2
  langgraph/    LangGraph through KDCubeChatModel
  claude/       Claude Code through ClaudeCodeAgent
```

Each directory owns its agent, requirements, YAML behavior, platform
descriptors, skill, Compose services, and setup command.

## The user journey

```text
Open agents/
     |
     +---- native
     +---- langgraph
     +---- claude
              |
              v
       create .venv
       install requirements + Chromium
              |
              v
       create local descriptors
       select user + conversation input
       start Redis + Postgres
       build isolated executor image
              |
              v
       --check -> --infra-check -> run
              |
              v
       research request is stored as an attachment
              |
              v
       Web Search -> Web Fetch -> inspected evidence
                              |
                              v
                    next-turn continuity
              |
              v
       agent-authored Python -> isolated code execution
              |                         |
              |                         +-> enabled Web tool via supervisor
              |                         +-> XLSX + renderer-ready HTML
              |                         +-> archived pkg/user_code.py
              v
       rendering_tools.write_pdf -> polished PDF
              |
              v
       inspect conversation, files, execution, and spend evidence

or select a direct conversation channel
              |
              +--> --interactive      terminal input/output
              |
              +--> --telegram-local   verified webhook, files, final reply
```

The model writes the research and Python. KDCube supplies the reusable
execution, rendering, persistence, accounting, and communicator boundaries.
This is the important document boundary: the model authors the content and
visual structure, while stable tools own PDF, DOCX, and PPTX conversion. The
agent can produce polished files without inventing a document-generation
program from scratch on every turn.

## 0. Prerequisites

Install Git, Python 3.11, and Docker Engine or Docker Desktop with Compose.

You also need one model interface:

- Native or LangGraph uses a provider API key or an on-host model behind the
  KDCube models gateway.
- Claude uses an authenticated Claude Code CLI or an Anthropic key.

## 1. Get the source

```bash
git clone https://github.com/kdcube/kdcube.git
cd kdcube
export KDCUBE_SOURCE="$PWD"
```

`KDCUBE_SOURCE` is only a shell path used by this recipe. Runtime behavior is
descriptor-owned.

## 2. Choose one agent

```bash
export AGENT=native
cd "$KDCUBE_SOURCE/agents/$AGENT"
```

Set `AGENT` to `native`, `langgraph`, or `claude`. All remaining commands run
inside that selected directory unless a step says otherwise.

## 3. Install the Python process and renderer

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp config.template.yaml config.local.yaml
```

No virtual environment is provided: the first command creates the selected
runner's local `.venv`, and its `requirements.txt` installs this repository's
SDK in editable mode plus that adapter's dependencies. Chromium is
required by the default PDF demonstration and other browser-backed render
paths. DOCX consumes Markdown; PPTX consumes section-based HTML.

## 4. Create local descriptors

For Native or LangGraph with a hosted provider:

```bash
.venv/bin/python setup_local.py --provider anthropic
```

Enter the provider key at the hidden prompt. For Claude authenticated through
its CLI:

```bash
.venv/bin/python setup_local.py --provider none
```

The setup command creates ignored local files:

```text
descriptors.local/
  assembly.yaml       model, Redis, Postgres, storage, executor, optional Git
  secrets.yaml        provider, Redis, Postgres, optional Git credentials
  economics.yaml      model prices and economics policy
  gateway.yaml        empty for this direct process
.env                   matching credentials for the two Compose services
```

Claude setup also initializes `output/claude-session-store.git`. These are
ordinary app-agnostic platform descriptors; this direct process does not need
`bundles.yaml`.

### Use an on-host model

Native and LangGraph use the SDK's shared descriptor-to-`ModelServiceBase`
route. Prepare either example without a provider secret:

```bash
.venv/bin/python setup_local.py --provider none
```

Select one exact model in `descriptors.local/assembly.yaml`:

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

`default_llm_model_id` is passed through unchanged. Choose a model your local
runtime already serves; the sample carries no model allowlist or
model-specific override map. `num_ctx` is one shared request context budget.
Set it to a value supported by both the selected model and the available
memory. If the gateway is protected, put its key at
`platform.services.llm.custom.api_key` in
`descriptors.local/secrets.yaml`.

Start the inference runtime in one terminal:

```bash
ollama serve
```

Load the selected model and start the protocol gateway in a second terminal:

```bash
cd "$KDCUBE_SOURCE/agents/$AGENT"
ollama pull <model-tag-loaded-by-your-local-runtime>
.venv/bin/python -m uvicorn \
  kdcube_ai_app.apps.models_gateway.app:app \
  --host 127.0.0.1 --port 11500
```

From the terminal that will run the agent, verify the gateway before
construction:

```bash
curl -s http://127.0.0.1:11500/health
.venv/bin/python agent.py --check
```

The agent process runs on the host, so it uses `127.0.0.1`. A KDCube
processor running inside Docker uses `host.docker.internal` for the same host
gateway.

Native ReAct does not require provider-native tool calling. Its instruction
profile and parser own the action protocol. The chosen model still needs to
follow that protocol, preserve structured action arguments, and fit the
instructions, tool catalog, conversation, and requested output inside
`num_ctx`. LangGraph forwards native tool specifications through the model
service, so its chosen model endpoint must emit the corresponding tool events.

Claude Code reads its model ID from the same `models` descriptor section, but
its subprocess owns an Anthropic-specific model protocol. Keep
`default_llm_provider: anthropic` for the Claude example. This is an adapter
constraint, not a second model-configuration location.

### Plan deployment capacity

The model runtime dominates GPU memory. Model weights, quantization, context
length, and KV cache determine its local footprint. Size the remaining
components from the enabled capabilities and expected concurrency:

| Component | Resource driver |
| --- | --- |
| Direct agent process | selected adapter, active turns, and retained in-process state |
| Redis and Postgres | concurrent turns, conversation volume, checkpoints, and accounting retention |
| Local filesystem or S3 storage | attachments, generated files, turn records, and execution archives |
| Playwright/Chromium rendering | concurrent PDF/PPTX renders, page complexity, and embedded media |
| Isolated Python execution | concurrent containers plus configured CPU, RAM, network, and workspace limits |
| On-host inference | model weights, quantization, context/KV cache, and CPU spill |

Use the model server's measurements for inference sizing. Configure executor
limits under `platform.services.proc.exec`, and scale Redis, Postgres, and the
selected storage backend for the expected retention and concurrency. Tool rows
in `config.local.yaml` select whether execution and rendering are part of the
agent composition.

## 5. Build the isolated Python executor

The demonstration asks the model to author Python using `openpyxl`. That code
runs inside the executor image, not in the sample process.

```bash
cd "$KDCUBE_SOURCE"
docker build \
  -t py-code-exec:latest \
  -f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec \
  app/ai-app
docker image inspect py-code-exec:latest >/dev/null
cd "$KDCUBE_SOURCE/agents/$AGENT"
```

The shipped descriptor sets `network_mode: none`. Research happens through
the governed KDCube Web Search and Web Fetch tools; the agent passes selected
findings into its generated program.

## 6. Start Redis and Postgres

```bash
docker compose --env-file .env -f compose.yaml up -d --wait
docker compose --env-file .env -f compose.yaml ps
```

Both services must report healthy. The harness creates its conversation
tables. LangGraph also creates its checkpoint tables.

## 7. Inspect the agent YAML

`config.local.yaml` selects behavior, tools, skills, and the local output root.
This is the Native shape; LangGraph and Claude use framework-specific tool IDs
for the same capabilities:

```yaml
agent:
  input:
    user_id: demo-user
    user_type: regular
    session_id: local-session
    conversation_id: native-demo
    recall_conversation_id: native-recall-demo
  ingress:
    telegram:
      host: 127.0.0.1
      port: 8787
      path: /telegram/webhook
      bot_token_ref: platform.services.telegram.bot_token
      webhook_secret_ref: platform.services.telegram.webhook_secret
  topic: "the current stable Python release and its release date"
  instructions:
    profile: lite:core
  additional_instructions: |
    You are a research agent. Preserve public source URLs and follow enabled skills.
  run_directory: ./output
  tools:
    - id: web
      kind: python
      module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
      alias: web_tools
      discovery: semantic_kernel
      allowed: [web_search, web_fetch]
      runtime:
        web_search: local
        web_fetch: local
      settings:
        filter:
          allowlist:
            - python.org
          blocklist: []
          ssrf_guard: true

    - id: code
      kind: python
      module: kdcube_ai_app.apps.chat.sdk.tools.exec_tools
      alias: exec_tools
      discovery: semantic_kernel
      allowed: [execute_code_python]
      runtime:
        execute_code_python: docker

    - id: documents
      kind: python
      module: kdcube_ai_app.apps.chat.sdk.tools.rendering_tools
      alias: rendering_tools
      discovery: semantic_kernel
      allowed: [write_pdf, write_docx, write_pptx]
      runtime:
        write_pdf: local
        write_docx: local
        write_pptx: local
  skills:
    root: ./skills
    enabled:
      - demo.research-brief
```

`user_id` and `conversation_id` select the durable conversation. Run with the
same pair again to continue it; change `conversation_id` to start another
conversation for that user. Tenant and project come from `assembly.yaml`.
Each runner contributes its stable agent ID, so framework-private checkpoints
and transcripts use this complete scope:

```text
tenant / project / user_id / conversation_id / agent_id
```

The shared conversation itself remains keyed by tenant, project, user, and
conversation. The final `agent_id` segment prevents two agent implementations
serving that conversation from colliding in private state. `session_id`
identifies the current calling session and accounting lineage. Native's
`recall_conversation_id` is the separate conversation used by its explicit
cross-conversation search demonstration.

Postgres transcript persistence and text recall work without an embedding
credential. With a configured embedding provider, the search path combines
semantic, lexical, and trigram retrieval. Without one, it uses lexical and
trigram retrieval and does not contact a default provider. Each runner prints
the active search mode at startup.

The descriptor uses the same canonical tool IDs for every agent. Framework
adapters translate those IDs only at their model-facing boundary:

| Capability | Native | LangGraph | Claude Code |
| --- | --- | --- | --- |
| Web Search | `web_tools.web_search` | `web_search` | `mcp__kdcube_web_search__web_search` |
| Web Fetch | `web_tools.web_fetch` | `web_fetch` | `mcp__kdcube_web_search__web_fetch` |
| Isolated Python | `exec_tools.execute_code_python` | `execute_python` | `mcp__kdcube_harness__execute_python` |
| PDF | `rendering_tools.write_pdf` | `write_pdf` | `mcp__kdcube_harness__write_pdf` |
| DOCX | `rendering_tools.write_docx` | `write_docx` | `mcp__kdcube_harness__write_docx` |
| PPTX | `rendering_tools.write_pptx` | `write_pptx` | `mcp__kdcube_harness__write_pptx` |

Unknown tool or skill IDs fail during `--check`. Settings stay on their source
row. Web Search and Web Fetch run from the same
[KDCube Web Search MCP implementation](../../../../../mcp/web-search/README.md);
the `agent.tools[id=web]` source owns their shared domain allowlist, blocklist, and SSRF
policy. Native and LangGraph call the SDK implementation in-process. Claude
starts the public `mcp/web-search/server.py` launcher as a stdio MCP server.
Change the allowlist when changing the sample topic.

The tracked descriptors select DuckDuckGo, which needs no search credential.
To select Brave, change the standard assembly descriptor and set its secret in
the ignored secret descriptor:

```yaml
# descriptors.local/assembly.yaml
platform:
  services:
    proc:
      tools:
        web_search:
          web_search_primary_backend: brave
          web_search_backend: brave
```

```yaml
# descriptors.local/secrets.yaml
platform:
  services:
    brave:
      api_key: "<BRAVE_API_KEY>"
```

For a generated-code Web Search call, the trusted supervisor resolves this
descriptor-backed credential and performs the network request. The isolated
code executor receives the tool result through authenticated, scoped
supervisor IPC. Its container remains network-isolated and receives neither
the search credential nor model/provider credentials.

The Native profile is `lite:core`; the SDK adds the existing ReAct exec,
rendering, and web blocks only when their tool families are enabled. LangGraph
and Claude use `workspace-files`, a framework-neutral profile that teaches the
direct current-turn artifact workspace without ReAct channel syntax. In all
three examples, `additional_instructions` is product-specific administrator
text appended after the standard profile, enabled-capability guidance, and
skills. It does not replace them.

```text
profile -> workspace/conduct -> enabled tool guidance -> skills
                                                        |
                                                        v
                                      additional_instructions (last)
```

Claude writes the composed profile to `CLAUDE.md` and materializes selected
skills under `.claude/skills`. LangGraph includes selected skill text in its
system prompt. Native keeps the normal ReAct skill gallery and strict protocol.
See
[Direct Agent Instruction Profiles](../../runtime/harness/direct-agent-instruction-profiles-README.md)
for supported Native profile IDs and the exact composition contract.

`descriptors.local/assembly.yaml` selects independent services and durable
storage. The templates select a local filesystem path:

```yaml
storage:
  kdcube: ../output/kdcube-storage

models:
  default_llm_provider: anthropic
  default_llm_model_id: claude-haiku-4-5-20251001

services:
  llm:
    custom:
      endpoint: http://127.0.0.1:11500/generate
      num_ctx: 32768

platform:
  services:
    proc:
      exec:
        py_code_exec_image: py-code-exec:latest
        py_code_exec_network_mode: auto
```

`auto` configures the trusted supervisor's network. From a host process it uses
Docker host networking; from a processor container it shares that processor's
network namespace, so descriptor-selected tools can reach provider APIs and
private services. The split executor that runs model-authored Python always
starts in a separate container with `--network none`. It reaches an allowed
tool only through the authenticated supervisor socket and never receives the
tool's credentials.

To store conversation files, artifacts, turn records, and execution archives
in S3, select an S3 URI instead:

```yaml
storage:
  kdcube: s3://<bucket>/<prefix>
```

The direct harness passes this URI to KDCube's shared storage backend. The
process identity must have access to the selected bucket and prefix.

Credentials live in `descriptors.local/secrets.yaml`. Model prices and
economics policy live in `descriptors.local/economics.yaml`; add a matching
`provider: custom` price row when a local model needs non-zero cost conversion.
Token usage remains accountable even when that conversion price is zero. The
three samples use separate default ports, so their Compose projects can run
independently.

## 8. Check and run

```bash
.venv/bin/python agent.py --check
.venv/bin/python agent.py --infra-check
.venv/bin/python agent.py
```

The YAML values can be selected from the command line without editing the
file:

```bash
.venv/bin/python agent.py \
  --user-id alice \
  --conversation-id release-research \
  --session-id terminal-1
```

Running the same command again continues `release-research` for `alice`.
Choosing another user or conversation selects a separate durable history.
Native also accepts `--recall-conversation-id` for its third-turn recall test.
Every construction check prints the resolved input and full private-state
scope before any model call.

`--check` constructs the adapter, exact tool inventory, and skills without
contacting a provider or support service. `--infra-check` verifies Redis,
Postgres tables, KDCube storage, the executor image, and Playwright Chromium
without model spend. Claude also verifies its Git transcript store.

The full two-turn scenario:

1. Stores `research-request.md` as a user attachment, performs Web Search, and
   fetches at least one selected result page before accepting it as evidence.
2. Continues the conversation, authors Python, and invokes isolated code
   execution.
3. The generated program calls `web_tools.web_search` through
   `agent_io_tools.tool_call`, validates its `{ok, error, ret}` result envelope,
   and consumes the returned rows, then creates an XLSX evidence table and
   print-ready HTML. The runner verifies that a returned title and URL reached
   the workbook.
4. The agent invokes `write_pdf`; KDCube renders and hosts the final PDF.

A successful run ends with:

```text
demonstration: PASS
```

## 9. Keep a conversation open in the terminal

Start a text conversation without changing the built-in demonstration:

```bash
.venv/bin/python agent.py --interactive \
  --user-id alice \
  --conversation-id terminal-chat \
  --session-id terminal-1
```

Enter one message at each `you>` prompt. The completed answer is printed after
`assistant>`. Enter `/exit` or `/quit` to stop. Each message is a normal
accounted harness turn under the explicit user, conversation, session, tenant,
project, and stable runner agent ID. Starting the command again with the same
user and conversation continues the durable conversation and the adapter's
private checkpoint or transcript.

## 10. Connect a local Telegram bot

Each example can expose a minimal Telegram webhook directly from its Python
process. This mode is for development from one shell and runs the complete
turn inline through `DirectAgentHarness`. Telegram transport identity and
credentials are sufficient for this local path.

Put the bot credentials in the ignored
`descriptors.local/secrets.yaml` file:

```yaml
platform:
  services:
    telegram:
      bot_token: "<TELEGRAM_BOT_TOKEN>"
      webhook_secret: "<RANDOM_WEBHOOK_SECRET>"
```

The corresponding non-secret references and endpoint stay in
`config.local.yaml`:

```yaml
agent:
  ingress:
    telegram:
      host: 127.0.0.1
      port: 8787
      path: /telegram/webhook
      bot_token_ref: platform.services.telegram.bot_token
      webhook_secret_ref: platform.services.telegram.webhook_secret
```

Start the selected runner:

```bash
.venv/bin/python agent.py --telegram-local
```

Verify it from another shell:

```bash
curl -sS http://127.0.0.1:8787/healthz
```

Telegram requires a public HTTPS endpoint. Expose port `8787` with the HTTPS
tunnel you use for development. For example, if `ngrok` is installed:

```bash
ngrok http 8787
```

Register the resulting HTTPS URL. A Telegram bot has one active webhook, so
first record the current webhook when reusing a bot from an existing KDCube
app. `descriptors.local/` is ignored by this example:

```bash
read -r -s -p "Telegram bot token: " TELEGRAM_BOT_TOKEN; echo
read -r -s -p "Telegram webhook secret: " TELEGRAM_WEBHOOK_SECRET; echo
read -r -p "Public HTTPS URL, without trailing slash: " PUBLIC_HTTPS_URL

curl -sS \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" \
  > descriptors.local/telegram-webhook-before-local.json

curl -sS -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${PUBLIC_HTTPS_URL}/telegram/webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"

unset TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET
```

These shell reads keep the two secret values out of shell history; KDCube
itself reads them from the descriptor, not from shell variables. Registering
the local URL temporarily replaces the existing application webhook. Run one
direct sample at a time when its examples share a bot.

Send the bot text or a file. The adapter performs this flow:

```text
Telegram update
  -> verify X-Telegram-Bot-Api-Secret-Token
  -> parse update and download Telegram attachments with the Telegram SDK
  -> user_id         = telegram_<sender-id>
  -> session_id      = telegram_chat_<chat-id>
  -> conversation_id = telegram_chat_<chat-id>
  -> invoke one direct harness turn inline
  -> read the persisted turn log
  -> send its answer and external files through the Telegram SDK
```

The Telegram sender ID is a transport-authenticated local storage identity.
KDCube platform identity and delegated authority begin when the agent is
hosted as an app and configured for those facilities. The process owner selects
tools in `config.local.yaml`. Connection Hub can link identities and govern
delegated tool use in that hosted product; the app and chat runtime provide
ingress.

The local process serializes turn execution so state is mutated by one turn at
a time. Concurrent HTTP request order is unspecified. Update claims have
process lifetime, and the webhook request remains open for the complete agent
run. A KDCube app with hosted chat ingress supplies ordered admission, retry
recovery, multiworker coordination, and a live steer/follow-up lane. For a
custom standalone host, build a durable queue around the direct callback to
provide the ordering and recovery guarantees your product requires.

Restore the previous application webhook when the bot is shared. The saved
Telegram response contains the URL, not the webhook secret, so supply the same
descriptor-held secret again:

```bash
read -r -s -p "Telegram bot token: " TELEGRAM_BOT_TOKEN; echo
read -r -s -p "Telegram webhook secret: " TELEGRAM_WEBHOOK_SECRET; echo

PREVIOUS_WEBHOOK_URL="$(
  jq -r '.result.url // empty' \
    descriptors.local/telegram-webhook-before-local.json
)"

if [ -n "$PREVIOUS_WEBHOOK_URL" ]; then
  curl -sS -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    -d "url=${PREVIOUS_WEBHOOK_URL}" \
    -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
else
  curl -sS -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook"
fi

unset TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET PREVIOUS_WEBHOOK_URL
```

## 11. Inspect durable evidence

Find the run index and deliverables:

```bash
find output/runs -type f \
  \( -name evidence.json -o -name research-brief.pdf -o -name research-data.xlsx \) \
  -print
```

Each `output/runs/<user>/<conversation>/<run>/evidence.json` is a navigation
index. The final run segment is unique to one process invocation, so an old
deliverable cannot satisfy a new run's verification. The index points to
authoritative records under the configured `storage.kdcube` root:

```text
output/kdcube-storage/
  accounting/<tenant>/<project>/...                    durable usage events
  cb/tenants/<tenant>/projects/<project>/conversation/ turn-log payloads
  cb/tenants/<tenant>/projects/<project>/attachments/  inputs and produced files
  cb/tenants/<tenant>/projects/<project>/executions/   compressed turn workspaces
```

Inspect the generated program retained with the execution:

```bash
ARCHIVE="$(find output/kdcube-storage -path '*/executions/*' -name '*.zip' | sort | tail -1)"
unzip -l "$ARCHIVE"
unzip -p "$ARCHIVE" pkg/user_code.py | sed -n '1,220p'
```

The execution ZIP also contains the turn's `out/` tree. Postgres holds the
conversation index and turn metadata; LangGraph stores its graph checkpoints
there as well. Redis holds a live per-turn accounting mirror with a TTL. The
durable accounting JSON files remain in KDCube storage after that mirror
expires.

The local `communicator.jsonl` shows what the adapter streamed into the harness.
It is diagnostic evidence, not the durable conversation authority.

## 12. Try the other document operations

The default run demonstrates HTML-to-PDF because it gives an immediate visual
result. The same `rendering_tools` module is already selected in YAML:

- `write_pdf` renders HTML to PDF through Playwright and Chromium.
- `write_docx` renders structured Markdown to DOCX.
- `write_pptx` renders one HTML `<section>` per slide to PPTX.

To demonstrate DOCX or PPTX, alter the second prompt in `agent.py` so the
generated Python also contracts an internal `.md` or section-based `.html`
source, then call the matching renderer with an external output path. The
model authors content and structure; the reusable renderer owns the document
format instead of forcing every model to rebuild formatting code.

## 13. Understand continuity by adapter

Native adds a third turn in a different conversation and requires
`react.memsearch` to recover the earlier research for the same user.

LangGraph rebuilds its graph with turn-bound tools on every invocation while
reusing the same Postgres checkpoint thread. Its thread ID includes tenant,
project, user, conversation, and agent. The fresh tool binding prevents a later
turn from writing into an earlier turn's artifact root.

Claude derives its CLI session ID and Git transcript branch from that same
complete scope. Inspect the local branch with:

```bash
git --git-dir output/claude-session-store.git for-each-ref \
  --format='%(refname)' refs/heads/kdcube/claude/
```

To use a private remote, set `storage.claude_code_session.repo` in
`descriptors.local/assembly.yaml` and the matching
`platform.services.git.http_token` credential in `descriptors.local/secrets.yaml`.

## 14. Change the agent

Use `config.local.yaml` to change the instruction profile,
`additional_instructions`, topic, tool selection, skills, limits, and settings
attached to each tool source. Use the standard descriptors
to change model, infrastructure, storage, economics, executor, and secrets.

Every Python source row names a `module` or local `ref`, an `alias`, exact
callable names under `allowed`, and optional per-callable `runtime` values.
The SDK `ToolSubsystem` introspects those sources and applies the same selected
catalog to direct calls and generated-code supervisor calls. Native ReAct
consumes that catalog directly. LangGraph's `tools.py` and Claude's local MCP
servers are model-facing schema adapters over those canonical IDs. A new
domain callable therefore gets one descriptor row plus the corresponding
LangGraph `BaseTool` or Claude MCP schema adapter when that agent type will use
it. The adapter translates names and arguments; `allowed` remains the single
execution policy. Rerun `--check` after any tool or skill change. The full
contract is in [Tool Subsystem](../../sdk/tools/tool-subsystem-README.md).

## 15. Serve the same agent to users

This direct-host path gives one Python process model accounting, durable
conversation records, communicator events, skills, attachments, output
hosting, isolated Python, and document rendering. The process owner controls
the selected tools.

To serve the same agent through chat UI, API, or messaging, place the tested
agent implementation and its harness adapter in a KDCube app, then declare the
required surface. The hosted runtime obtains user and conversation identity
from authenticated ingress and adds a durable turn queue, ordering and retry
recovery, tool-execution enforcement, delegated consent, rate/spend policy,
and app hosting. Follow
[Settle Your Solution in KDCube](../apps/settle-your-solution-in-kdcube-README.md).
For Telegram ingress, also follow the
[Telegram integration recipe](../connections/integrations/telegram-README.md).

## 16. Stop services

```bash
docker compose --env-file .env -f compose.yaml down
```
