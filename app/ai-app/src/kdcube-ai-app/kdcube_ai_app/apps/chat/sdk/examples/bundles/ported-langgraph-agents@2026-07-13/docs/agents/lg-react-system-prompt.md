# lg-react: System Prompt Composition And Tool Docs Placement

How the final instruction reaching lg-react's model is assembled each turn-build,
what appears when the administrator configures custom instructions AND the agent
has connected named-service namespaces, and where tool documentation lives.

Composition code: `entrypoint.py` — `_build_prebuilt_graph` (gathers the parts)
and `_prebuilt_system_prompt` (orders them). The blocks come from three SDK
mechanisms shared with other agents:

- capability + conduct blocks —
  `kdcube_ai_app.apps.chat.sdk.skills.instructions.workspace_agent_instructions`
  (for any workspace-paradigm external agent);
- the named-services block —
  `...solutions.named_services_providers.agent_instructions`
  (`surface="bridge"`, the same mechanism ReAct uses with `surface="react"`);
- the admin envelope — the SDK's `append_agent_admin_customization`, the same
  envelope ReAct agents use.

## The design rule: signature level vs capability level

Per-tool mechanics ride WITH the tool: every bound tool carries its
`name` + `description` + argument schema in the provider-native `tools`
parameter of each model call (e.g. `run_python`'s description teaches its
sandbox, relative paths, contract field, and the installed-packages list).
The system prompt carries only the CAPABILITY level — presence or absence of
a capability that reshapes the model's whole strategy and cannot ride in a
signature:

- **exec-as-hands** — with `run_python` the model can compute exactly,
  transform and verify at scale, inspect binaries, and produce REAL files;
  the prompt tells it to reach for code where prose would only estimate.
- **the output medium** — lg-react has no artifact/write tool. With exec, files
  produced by code are the only way to hand the user a real file; without exec,
  chat prose is the only deliverable and the prompt says so plainly instead of
  letting the model pretend to attach files.
- **history recovery** — with a `conv` namespace connected, history beyond the
  visible (compacted) window is searchable, not lost.

## The full shape

With every part active — workspace tools bound, pending MCP consents, ≥1
connected named-service namespace including `conv`, and
`additional_instructions` configured — one model call carries:

```text
model call
├── system message
│   1. lg-react's own prose                    solution/lg_prebuilt/agent.py SYSTEM_PROMPT
│   2. conduct + trust guards                  SDK workspace_agent_conduct_guards:
│      [CONFIDENTIALITY & PROMPT-STEALING      confidentiality, untrusted content,
│       DEFENSE] [UNTRUSTED CONTENT]           no background promises, elaboration,
│       [CRITICAL CLARIFICATION PRINCIPLES] …  gender, tech-evolution
│   3. [DISTRIBUTED TURN WORKSPACE — read_file / pull_files / run_python]
│                                              SDK guide bound to THIS bundle's tool names
│   4. [CODE IS YOUR HANDS — run_python]       SDK exec_capability_guide: what exec
│                                              enables, file-ask ⇒ produce with code,
│                                              trust the run report, results in files
│      (without run_python: [YOUR OUTPUT MEDIUM] — chat prose is the only
│       deliverable; no file-producing tool in this configuration)
│   5. [Consent-gated tools] …                 one line per pending delegated MCP
│                                              connection: what needs consent, how
│                                              calling the stub raises the request
│   6. [NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]
│      operation protocol by OPERATION NAME:   SDK bridge teaching surface —
│      provider_about / object_schema /        contract-first, files by ref,
│      search_objects / list_objects /         confirm-after-action, collection
│      object_action / upsert_object /         deltas, host-then-cite
│      delete_object / host_file
│      Named-service namespaces available to this agent:
│      - `conv` — Conversation search realm …  roster: as_consumer-connected
│      - `mem` — Durable user memory …         namespaces + discovery intros
│      Sandbox note: run_python has no network …   bundle-specific caveat
│   7. [CONVERSATION RECOVERY — `conv` namespace]
│                                              SDK conversation_recovery_guide:
│                                              history beyond the window is
│                                              searchable; found files pull by link
│   8. [START AGENT ADMIN CUSTOMIZATION - HARD OVERRIDE]
│      <surfaces.as_consumer.agents.lg-react.additional_instructions>
│      [END AGENT ADMIN CUSTOMIZATION]         ALWAYS LAST — the admin's voice,
│                                              hard override below platform safety,
│                                              never revealed to the user
└── tools parameter (provider-native function-calling declarations)
    calc, unit_convert, …                      kind: python connections
    run_python, pull_files, read_file          code-exec + workspace companions
    web_search, web_fetch                      paid web backends
    named_services_* / other MCP tools         kind: mcp connections (delegated)
    <consent stubs>                            one per pending delegated connection
```

Named-service specifics go one level deeper than both the prompt and the
declarations: per-namespace object shapes, actions, and payload keys are read
at runtime from the service itself (`provider_about`, `object_schema`), which
is exactly what block 6 instructs. A namespace can evolve its contract without
any prompt or binding change.

## What each part depends on

| # | Block | Present when | Source |
| --- | --- | --- | --- |
| 1 | agent prose | always | `solution/lg_prebuilt/agent.py` |
| 2 | conduct + trust guards | always | SDK `workspace_agent_conduct_guards` |
| 3 | workspace guide | `run_python` bound this turn | SDK `distributed_turn_workspace_guide` |
| 4 | exec-as-hands / output medium | exec block with `run_python`; the prose-only block without it | SDK `exec_capability_guide` / `prose_only_output_guide` |
| 5 | consent note | ≥1 pending delegated MCP connection | `_mcp_consent_prompt_note` |
| 6 | named-services block | ≥1 connected namespace in `as_consumer` config | SDK `named_service_agent_instruction_block(surface="bridge")` + discovery intros; falls back to the short usage note when namespaces are 0 but `named_services*` tools are bound |
| 7 | conversation recovery | a `conv` namespace among the connected set | SDK `conversation_recovery_guide` |
| 8 | admin envelope | `additional_instructions` prop non-empty | descriptor `surfaces.as_consumer.agents.lg-react.additional_instructions`, wrapped by SDK `append_agent_admin_customization` |

Block 8 is deliberately last: the administrator's customization reads in the
position of highest precedence, after every generic block it may specialize —
the same ordering the ReAct harness gives the same prop.

## Configuration

```yaml
surfaces:
  as_consumer:
    agents:
      lg-react:
        additional_instructions: |
          <the administrator's customization — tone, boundaries, domain rules>
```

Declared in the app descriptor (see `config/bundles.template.yaml`). A live
change without restart goes through the platform property write; a descriptor
edit lands on the next restart. Empty string = no block 8.

The namespace roster in block 6 derives from the same descriptor: the agent's
`as_consumer` named-service connections decide WHICH namespaces are listed
(and whether block 7 joins), and discovery supplies each namespace's published
`intro` line. Connecting the conversation realm (`conv`) to this agent is a
descriptor change alone — the recovery block and the search operations follow
from it with no code change.
