---
id: repo:kdcube-ai-app/agents/README.md
title: "Build and Run On-Premises Agents with the KDCube Harness"
summary: "Give the KDCube Native ReAct agent, a LangGraph agent, Claude Code, or your own agent durable conversations, web and file work, isolated code execution, reusable skills, and inspectable usage on infrastructure you control."
tags: ["agents", "harness", "native-react", "langgraph", "claude-code", "quickstart", "web-search", "web-fetch"]
keywords: ["agent examples", "DirectAgentHarness", "KDCube Web Search", "KDCube Web Fetch", "Redis", "Postgres", "PDF", "XLSX"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
  - repo:kdcube-ai-app/mcp/web-search/README.md
---
# Build and Run On-Premises Agents with the KDCube Harness

The KDCube Agent Harness gives an agent an isolated turn workspace and
code-execution sandbox; configurable models, instructions, tools, and skills;
durable conversations; local or S3 file storage; Git-backed project
workspaces; cross-conversation search; and detailed usage evidence. The agent
can continue work across sessions, research current information, create files,
run code without host access, recover earlier work, and show what it did and
what it cost.

The harness exists so those capabilities stay reusable when you change the
model or agent loop. You can start from a working agent, add only the pieces a
workflow needs, and keep the same conversation, file, safety, and evidence
boundaries as the agent evolves.

Start with your own agent, Claude Code, LangGraph, or the included **KDCube
Native ReAct agent** (**Native agent** below).
This is how an agent meets its harness:

```text
Agent implementation
  Native agent | LangGraph agent | Claude Code | your adapter
                                |
                                v
KDCube Agent Harness, configured from YAML
  model | instructions | skills | tool sources + allowed operations
  conversation identity | local/S3 storage
                                |
          +---------------------+---------------------+
          |                     |                     |
          v                     v                     v
  durable conversations   isolated turn workspace   streamed evidence
  and earlier recall      and code execution         usage and cost
```

Here you can try different agents wrapped in the KDCube Harness from your terminal:

- [Native agent](native/README.md)
- [Claude Code](claude/README.md)
- [LangGraph](langgraph/README.md)

> All examples require Redis/Postgres Compose to run (included in each directory).

Each example researches a topic, verifies sources, creates a filterable XLSX
with agent-authored Python in isolated code execution, renders a PDF, and then
finds the earlier research from a different conversation for the same user.

What is in each example:

1. The adapter where the agent clicks into the harness. Change it as you
   experiment, or use it to plug in your own agent.
2. The configuration to run it with Redis, Postgres, local or S3 storage, and
   either an on-host model or a provider API.

This is a constructor: each directory already runs, and its YAML lets you
select or replace one piece at a time. Begin with a working example, then
change the model, instructions, tools, skills, storage, or agent implementation
independently.

## Choose your starting agent

| Start here when...                                                                         | Ready implementation                                                                            | Model path                                    | Run it                            |
|--------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|-----------------------------------------------|-----------------------------------|
| You want a complete first agent, especially with a small on-host model                     | Native agent controls its own observe/reason/act loop and tool protocol                          | Provider API or on-host endpoint              | [Native agent](native/README.md)  |
| You are comfortable with LangGraph and want to construct the workflow as a LangGraph graph | LangGraph `create_agent` with durable checkpoints, KDCube tools, streaming, and accounting      | Provider API or a compatible on-host endpoint | [LangGraph](langgraph/README.md)  |
| You want coding agent powered by Claude Code's own loop                                    | `ClaudeCodeAgent` with a Git-backed transcript, workspace, KDCube tools, and harness evidence   | Claude Code's Anthropic model path            | [Claude Code](claude/README.md)   |

## What the harness lets an agent do

Choose the pieces your agent needs. A **tool** is a named capability the agent
can call during a turn; the YAML inventory determines which tools the agent
can see and use.

- **Continue where it left off:** stable user and conversation identity lets
  the agent resume a durable conversation instead of starting from an empty
  prompt each time.
- **Find earlier work:** the agent can search the same user's other
  conversations and bring relevant findings into the current task. The Native
  agent exposes `react.memsearch`; LangGraph and Claude use framework adapters
  over the same SDK search. The canonical descriptor-selected callable is
  `conversation_tools.search`.
- **Research current information:** use YAML-selected
  [KDCube Web Search and Web Fetch](../mcp/web-search/README.md) with explicit
  source policy, so claims in a report remain traceable to inspected evidence.
- **Receive and return real files:** preserve user attachments and generated
  files in local or S3 storage instead of squeezing them into chat text.
- **Run generated code without host access:** the agent generates Python and
  calls the enabled code-execution tool. A trusted supervisor starts a separate
  executor in an isolated turn workspace and retains the exact program and
  declared outputs. The executor has no outbound network and receives neither
  model/provider credentials nor the Web Search credential; it receives only
  the scoped supervisor IPC needed for declared nested tool calls.
- **Transform many tool results without filling the model context:** generated
  Python can call an enabled catalog tool through `agent_io_tools.tool_call`.
  The trusted supervisor resolves that call from the same descriptor-selected
  tool catalog and enforces the same per-callable allow policy.

  This lets generated code process many tool results without placing all of
  them in the model context. Here the code runs two searches and writes every
  result into a separate, filterable Excel sheet:

  ```python
  from pathlib import Path

  from openpyxl import Workbook

  topics = {
      "Geopolitics": "current geopolitics developments",
      "Entertainment": "current entertainment industry developments",
  }

  workbook = Workbook()
  workbook.remove(workbook.active)

  for sheet_name, query in topics.items():
      result = await agent_io_tools.tool_call(
          fn=web_tools.web_search,
          params={
              "queries": query,
              "objective": f"Collect sources about {query}",
              "n": 20,
              "use_llm": False,
              "fetch_content": False,
          },
          call_reason=f"Collect {sheet_name} sources for the workbook",
          tool_id="web_tools.web_search",
      )
      if not result["ok"] or not result["ret"]:
          raise RuntimeError(result.get("error") or f"No {sheet_name} results")

      sheet = workbook.create_sheet(sheet_name)
      sheet.append(["Title", "URL", "Snippet"])
      for row in result["ret"]:
          sheet.append([
              row.get("title", ""),
              row.get("url", ""),
              row.get("snippet") or row.get("text", ""),
          ])
      sheet.freeze_panes = "A2"
      sheet.auto_filter.ref = sheet.dimensions

  output = Path(OUTPUT_DIR) / "files/research/search-results.xlsx"
  output.parent.mkdir(parents=True, exist_ok=True)
  workbook.save(output)
  ```

  `agent_io_tools` and `web_tools` are injected by the isolated execution
  runtime; generated code does not import them. The returned envelope contains
  `ok`, `error`, and `ret`, and the program should fail rather than continue
  with unverified data when the nested tool call fails.
- **Deliver polished documents without rewriting conversion code:** the agent
  creates HTML or Markdown and calls an enabled rendering tool. The harness
  converts it into PDF, DOCX, or PPTX.
- **Teach repeatable ways of working:** select a maintained instruction
  profile, add product instructions, and enable reusable `SKILL.md` procedures
  from YAML.
- **See what happened and what it cost:** stream communicator events and
  record model usage, cost, tool activity, conversation turns, files, and
  execution evidence.

The examples connect terminal and local Telegram ingress adapters to the
harness.

The agent implementation decides what to do. The harness supplies the
conversation, tool, workspace, file, streaming, and accountability contracts
around those decisions. YAML selects the capabilities available in each run.

## Use the ready composition

The included **research and report** flow exercises the pieces together. It
searches the web, inspects a source, carries findings into another turn,
authors Python, creates an XLSX workbook, and renders a PDF. Change the topic,
instructions, source policy, tools, skill, and output contract for your own
domain workflow.

The shell runner is a normal command-line job that can be invoked from a
scheduler or queue. A KDCube app carries the same composition into an
on-premises multi-user runtime with authenticated ingress, governed tool
execution, durable jobs, and administrator/user policy.

## Resource profile

The selected model and enabled tools determine the machine profile:

| Component | Resource use | Selection |
| --- | --- | --- |
| Model endpoint | VRAM or provider API capacity; weights, quantization, and context length define the local footprint | `descriptors.local/assembly.yaml` |
| Agent process | Host Python process for the selected Native agent, LangGraph, or Claude adapter | selected agent directory |
| Conversation and accounting services | Redis and Postgres preserve conversation and usage records; conversation files and artifacts use local filesystem or S3 storage | `compose.yaml` and `storage.kdcube` in `descriptors.local/assembly.yaml` |
| Isolated code execution | The agent generates Python and calls `exec_tools.execute_code_python`; the trusted supervisor starts the executor with the configured image, CPU, RAM, network, and isolated turn-workspace limits | select `execute_code_python` in the `exec_tools` source under `agent.tools`; configure execution under `platform.services.proc.exec` |
| Document production | The agent creates HTML or Markdown and calls `rendering_tools.write_pdf`, `write_docx`, or `write_pptx`; PDF and PPTX conversion uses Playwright/Chromium | select the required callable names in the `rendering_tools` source under `agent.tools` |

Enable the resource-bearing components required by the deployment. Isolated
code execution and rendering are selected through YAML tool sources; the model,
service, storage, and executor limits are selected through platform
descriptors. The Native agent carries its action protocol in instructions and
parsing, which lets a capable text-generation model use configured tools
through an on-host endpoint.

## How do I try it?

Start with the complete Native agent and validate its local composition:

```bash
cd agents/native
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_local.py --provider none
cp config.template.yaml config.local.yaml
.venv/bin/python agent.py --check
```

This prepares ignored local descriptors and validates the composition before a
model call. Next, select and start the on-host model endpoint, start Redis and
Postgres, and run the agent. The exact copyable sequence is in the
[Native agent README](native/README.md), including the provider-API alternative, and
in the complete
[Run the Agent Harness from Python recipe](../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md).

For any of the three examples, the operating sequence is:

1. Create that directory's `.venv` and install its `requirements.txt`.
2. Run `setup_local.py` and copy `config.template.yaml` to the ignored
   `config.local.yaml`.
3. Select an on-host model or provider model in standard platform descriptors.
4. Select instructions, tools, tool settings, and skills in
   `config.local.yaml`.
5. Start Redis and Postgres. Build the isolated code executor and install
   Chromium when their tool sources are enabled.
6. Run `agent.py`, then inspect conversation, file, execution, event, usage,
   and generated-code evidence in the configured storage backend. The
   templates use `output/` on the local filesystem.

Choose the model through the standard platform descriptor:

| Agent | Model path | Start here |
| --- | --- | --- |
| Native agent | Provider API or on-host model through the KDCube model gateway | `descriptors.local/assembly.yaml` |
| LangGraph | Provider API or on-host model through the KDCube model gateway | `descriptors.local/assembly.yaml` |
| Claude Code | Claude Code's Anthropic model path | `descriptors.local/assembly.yaml` |

Each agent directory contains its runner, requirements, agent YAML, standard
platform descriptors, example skill, Redis/Postgres Compose file, and exact
commands. The command in each README executes that directory's visible
`agent.py` directly.

Create a `.venv` in the selected agent directory and install its
`requirements.txt`. The default research-and-report demonstration also uses
two explicit preparations:

- install Playwright Chromium with `.venv/bin/python -m playwright install chromium`
  because the PDF/PPTX renderers use it; and
- build `py-code-exec:latest` with the documented `docker build` command because
  model-authored Python runs in the isolated executor image.

Both commands are included in every runner's copyable setup block. A smaller
search-and-summary configuration can select only the Web tool source.

Every runner receives its caller and conversation explicitly:

```yaml
agent:
  input:
    user_id: demo-user
    user_type: regular
    session_id: local-session
    conversation_id: native-demo
    recall_conversation_id: native-recall-demo
```

Run the same example again with the same `user_id` and `conversation_id` to
continue that durable conversation. Use another `conversation_id` to start a
separate conversation. The shared conversation key is tenant, project, user,
and conversation; each adapter adds its stable `agent_id` to its private
checkpoint or transcript key. `session_id` identifies the calling session and
accounting lineage while the durable conversation key remains stable. The
values can also be overridden with `--user-id`, `--conversation-id`,
`--session-id`, and `--recall-conversation-id`.

From the selected agent directory, run:

```bash
.venv/bin/python agent.py \
  --user-id alice \
  --conversation-id release-research \
  --session-id terminal-1 \
  --recall-conversation-id release-research-recall
```

This command replaces the four values under `agent.input` for that process.
Running it again continues `release-research` for `alice`; changing the user or
conversation selects another durable history. Changing only `session-id`
records a different calling/accounting session while keeping the same durable
conversation.

The built-in demonstration runs the **research and report** flow in two turns,
then checks recall from another conversation:

```text
research request
      |
      v
Web Search tool -> Web Fetch tool -> inspected source evidence
                                      |
                                      v
                         retained conversation context
                                      |
                                      v
agent authors Python -> isolated code-execution tool -> XLSX + HTML
                              |
                              +-> generated code calls an enabled Web tool
                                  through the trusted supervisor
                              |
                              v
                PDF rendering tool (write_pdf) -> polished PDF
                                      |
                                      v
                    another conversation for the same user
                                      |
                                      v
                  Conversation Search tool -> earlier findings
```

The YAML-selected renderer family also exposes HTML-to-PPTX and
Markdown-to-DOCX. Each run records communicator events, accounted model calls,
attachments, output files, conversation turns, and the execution ZIP that
contains the model-authored `pkg/user_code.py`.

The agent authors research, code, data, HTML, and Markdown. The isolated
executor runs its program, and KDCube's document tools own repeatable PDF,
DOCX, and PPTX conversion. This produces a concrete research-and-file agent
while keeping the selected agent loop replaceable.

Each YAML also selects an SDK-owned instruction profile. The Native agent uses the
standard ReAct `lite:core` body plus blocks for its enabled tools. LangGraph and
Claude use the framework-neutral `workspace-files` body. Product behavior goes
in `additional_instructions`, after the workspace, capability, and skill
teaching. See
[Direct Agent Instruction Profiles](../app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md).

After the first run, change these constructor inputs:

| Change | File |
| --- | --- |
| Research subject or workflow | `config.local.yaml#agent.topic` and `agent.additional_instructions` |
| Enabled capability and its policy | The exact `config.local.yaml#agent.tools[id=...]` row |
| Reusable procedure | `skills/<skill-id>/SKILL.md` and `agent.skills.enabled` |
| On-host or provider model | `descriptors.local/assembly.yaml` |
| Local filesystem or S3 storage | `storage.kdcube` in `descriptors.local/assembly.yaml` |
| Conversation identity | `agent.input` or the corresponding CLI flags |
| Example ingress adapter | CLI mode or `agent.ingress.telegram` |

`agent.tools` declares tool sources, rather than one row for every callable.
For a Python source, `module` or `ref` identifies the implementation, `alias`
defines its tool-ID prefix, `allowed` selects exact callable names, and
`runtime` selects where each callable executes. `ToolSubsystem` introspects the
declared source and builds the catalog dynamically. Removing a callable from
`allowed` removes it from the model catalog and from generated-code supervisor
admission.

To add your own Python tool, put its module or bundle-relative file in one
source row and select the callable names the agent may use:

```yaml
agent:
  tools:
    - id: market-data
      kind: python
      ref: ./market_tools.py
      alias: market_tools
      discovery: semantic_kernel
      allowed: [latest_prices, supplier_snapshot]
      runtime:
        latest_prices: local
        supplier_snapshot: local
```

That row is the canonical discovery and execution policy. The Native agent reads
the resulting catalog directly. LangGraph also needs a small `BaseTool` schema
adapter in `langgraph/tools.py`, and Claude Code needs an MCP adapter that
presents the callable to Claude. Those adapters translate model-facing names
and argument schemas; they do not create a second allowlist or bypass
`ToolSubsystem` enforcement.

The canonical discovery, naming, binding, and isolated-supervisor contract is
documented in [Tool Subsystem](../app/ai-app/docs/sdk/tools/tool-subsystem-README.md).
The complete shared command sequence is in
[Run the Agent Harness from Python](../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md).
All three runners use the implementation surfaced by the
[Web Search and Web Fetch MCP package](../mcp/web-search/README.md). The Native agent and
LangGraph bind its SDK functions in-process; Claude starts that package's
public launcher as a local stdio MCP server. Their recall tools all call the
shared SDK conversation search: the Native agent through `react.memsearch`, LangGraph
through `conversation_search`, and Claude through the local harness MCP tool
of the same name.

## Talk to the agent

Keep one durable conversation open in the terminal:

```bash
.venv/bin/python agent.py --interactive \
  --user-id alice \
  --conversation-id terminal-chat \
  --session-id terminal-1
```

Or point one Telegram bot at the local development hook:

```text
Telegram webhook + verified secret
              |
              v
      direct inline callback
              |
              v
    selected agent + DirectAgentHarness
              |
              +--> Postgres conversation
              +--> configured storage and files
              +--> Telegram text/file response
```

The Telegram hook uses `agent.ingress.telegram` and the ignored local secrets
descriptor. It maps the Telegram sender to `user_id=telegram_<sender-id>` and
the chat to `conversation_id=telegram_chat_<chat-id>`. It reuses KDCube's
Telegram update, attachment, and delivery SDK inside the standalone agent
process. Follow the exact setup in
[Run the Agent Harness from Python](../app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md#10-connect-a-local-telegram-bot).

This local experiment performs inline processing in one process. The process
prevents turns from overlapping; concurrent webhook arrival order remains
unspecified, update claims have process lifetime, and the HTTP request remains
open while the agent runs. KDCube's hosted chat ingress, or a durable queue
built around the callback, supplies ordering, retry recovery, asynchronous
execution, live controls, and multiple workers. Connection Hub links identity
and governs delegated tools in the hosted product; the app and chat runtime own
transport ingress.

## Serve the configured agent to users

Place the tested composition in a KDCube app and declare a chat, API, job, or
messaging surface. The hosted runtime supplies authenticated user and
conversation IDs, tool-execution enforcement, consent, rate/spend policy,
durable jobs, and multi-user ingress. Follow
[Settle Your Solution in KDCube](../app/ai-app/docs/recipes/apps/settle-your-solution-in-kdcube-README.md)
and the [KDCube Quick Start](../app/ai-app/docs/quick-start-README.md).
