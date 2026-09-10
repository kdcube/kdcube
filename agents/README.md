---
id: repo:kdcube-ai-app/agents/README.md
title: "Build and Run On-Premises Agents with the KDCube Harness"
summary: "Run the KDCube Native agent, LangGraph, Claude Code, or your own agent with durable conversations, web and file tools, isolated code execution, reusable skills, and inspectable usage."
tags: ["agents", "harness", "native-react", "langgraph", "claude-code", "quickstart", "web-search", "web-fetch"]
keywords: ["agent examples", "KDCube Agent Harness", "KDCube Web Search", "KDCube Web Fetch", "Redis", "Postgres", "PDF", "XLSX"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Build and Run On-Premises Agents with the KDCube Harness

Run an agent from your terminal without starting a KDCube server. The examples
import the KDCube SDK from this checkout and run as normal Python processes on
infrastructure you control.

The KDCube Agent Harness gives an agent:

- durable conversations and search across earlier conversations;
- [Web Search and Web Fetch](../mcp/web-search/README.md);
- user attachments, generated files, and local or S3 storage;
- an isolated turn workspace and isolated code-execution sandbox;
- PDF, DOCX, and PPTX rendering;
- configurable instructions, tools, and reusable skills; and
- streamed activity, model/tool usage, cost, and execution evidence.

Use the included Native agent, or put the same harness around another agent
loop. The model and agent can change without rebuilding conversation, file,
tool, isolation, and evidence support.

## Choose an agent

| Agent | What you get | Start |
| --- | --- | --- |
| **KDCube Native ReAct agent** | A complete agent loop included in this repository. This is the shortest first run. | [Native agent](native/README.md) |
| **LangGraph** | A LangGraph agent using KDCube models, tools, conversations, streaming, and accounting. | [LangGraph](langgraph/README.md) |
| **Claude Code** | Claude Code with KDCube conversations, tools, files, skills, and evidence around its own loop. | [Claude Code](claude/README.md) |
| **Your agent** | Use the adapter boundary shown by the closest example and keep the harness services. | [Agent Harness architecture](../app/ai-app/docs/runtime/harness/README.md) |

Each agent directory contains its `agent.py`, Python requirements, YAML
configuration, standard platform descriptors, sample skill, and a Compose file
for Redis and Postgres.

## What you need

- Git and Python 3.11.
- Docker Engine or Docker Desktop with Compose for Redis, Postgres, and the
  isolated Python executor.
- A provider API key, an authenticated Claude Code CLI, or an on-host model
  endpoint.
- Playwright Chromium when PDF or PPTX rendering is enabled.

Redis and Postgres are support services. A running KDCube deployment is not a
prerequisite for these examples.

## Try the Native agent

From the repository root:

```bash
cd agents/native
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_local.py --provider anthropic
cp config.template.yaml config.local.yaml
docker compose --env-file .env -f compose.yaml up -d --wait

cd ../..
docker build -t py-code-exec:latest \
  -f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec \
  app/ai-app

cd agents/native
.venv/bin/python agent.py --check
.venv/bin/python agent.py --infra-check
.venv/bin/python agent.py --interactive \
  --user-id alice \
  --conversation-id first-research \
  --session-id terminal-1
```

`setup_local.py` asks for the provider key without echoing it and creates
ignored local descriptors and service credentials. For an on-host model, use
`--provider none` and select its model ID and endpoint in
`descriptors.local/assembly.yaml`; the [Native agent guide](native/README.md)
shows the exact descriptor shape.

Use the same setup sequence inside `agents/langgraph` or `agents/claude`. Their
READMEs name the model and transcript differences.

For every command, including local-model setup and Telegram, follow the
[step-by-step executable recipe](../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md).

## What the demo does

All three examples run the same research-and-report job:

```text
research request
  -> Web Search tool
  -> Web Fetch tool
  -> continue the conversation
  -> agent writes Python
  -> isolated code-execution tool creates XLSX + HTML
  -> PDF rendering tool creates the report
  -> another conversation searches and recovers the earlier work
```

The generated Python can call an allowed Web tool through the trusted
supervisor while building the workbook. That lets code filter and transform
many result rows without placing every row in the model context. The executor
receives no provider credentials and has no outbound network.

A successful built-in run ends with:

```text
demonstration: PASS
```

## Change the agent

| Change | File |
| --- | --- |
| User, conversation, instructions, enabled tools, tool settings, and skills | `config.local.yaml` |
| Model, Redis, Postgres, storage, and executor limits | `descriptors.local/assembly.yaml` |
| Provider and service credentials | `descriptors.local/secrets.yaml` |
| Agent/framework adapter | `agent.py` |
| Reusable workflow | `skills/<skill-id>/SKILL.md` |

Local generated state goes under `output/` by default. Conversation records,
attachments, generated files, execution archives, and run evidence use the
configured local or S3 storage. Postgres keeps durable conversation rows;
LangGraph also keeps its checkpoints there. Claude Code keeps its private
provider transcript in the configured Git repository.

Run the same user and conversation again to continue it:

```bash
.venv/bin/python agent.py --interactive \
  --user-id alice \
  --conversation-id first-research \
  --session-id terminal-2
```

Use another conversation ID for separate work. Conversation Search can still
find earlier conversations belonging to the same user.

## Run the automatic demonstration

```bash
.venv/bin/python agent.py
```

This command needs no input. It runs the configured research-and-report
scenario, prints the agent and tool activity as it happens, verifies the
conversation and generated files, prints `demonstration: PASS`, and exits.
With the template configuration, it researches the current stable Python
release and creates `research-data.xlsx`, `research-brief.html`, and
`research-brief.pdf`.

Inspect the run under
`output/runs/<user>/<conversation>/run_<id>/evidence.json`. That evidence file
points to the persisted conversation, attachment, generated files, execution
archive, tool activity, and accounting records.

## Talk to the agent

Use `--interactive` for an ongoing terminal conversation. Type any request at
the `you>` prompt; the agent replies at `assistant>`. Type `/exit` to stop.

Use `--telegram-local` to receive the same kind of requests through a Telegram
bot:

```bash
.venv/bin/python agent.py --telegram-local
```

This command does not run the automatic demonstration. It starts the local
webhook server, prints its listening URL, and waits. After configuring the bot,
HTTPS tunnel, and webhook as described in the executable recipe, send the bot
a request such as:

> Research the current stable Python release and its release date. Verify the
> sources, create an XLSX evidence table and a polished PDF brief, and send me
> both files.

Each Telegram chat becomes a durable conversation. The agent handles text and
supported attachments, uses the YAML-enabled tools, and sends its answer and
declared external files back to that chat. It keeps listening until `Ctrl-C`.

The local Telegram mode processes each verified webhook inline. For durable
ordering, retries, asynchronous turns, multiple workers, authenticated users,
and governed tool execution, place the tested agent in a KDCube app. Start with
[Settle Your Solution in KDCube](../app/ai-app/docs/recipes/apps/settle-your-solution-in-kdcube-README.md)
and the [KDCube Quick Start](../app/ai-app/docs/quick-start-README.md).
