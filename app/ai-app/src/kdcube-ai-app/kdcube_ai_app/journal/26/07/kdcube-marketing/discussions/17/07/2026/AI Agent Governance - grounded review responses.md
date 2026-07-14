# AI Agent Governance: Grounded Review Responses

Date: 2026-07-14

Source document:
`AI Agent Governance_ Why Policy Layers Aren't Enough.docx`

Each thread below is self-contained. Paste the response into the corresponding
DOCX comment, then use the supplied replacement copy for the anchored passage.

## Editorial Decision

The content creator's DOCX has a useful core argument, but three claims needed
a more exact subject before publication:

1. Structural data isolation applies to generated code in the reusable isolated
   workspace/executor used by ReAct and integrated agent frameworks. Trusted app
   and tool code uses authenticated authorization and scoped storage contracts.
2. Tools are trusted runtime code with configurable execution modes. In split
   execution, tool implementations run in the supervisor behind the
   authenticated executor socket.
3. Runtime records provide evidence for review. Immutability, retention, and
   regulatory sufficiency depend on deployment controls and the reviewer.

The AWS statement was also softened. A major vendor's runtime offering is one
market signal, not proof that every product or category claim succeeds.

## Thread 1: Tenant, User, Authority, And Generated-Code Isolation

Comments 0-2 are attached to the two paragraphs under “Tenant isolation that's
architectural,” including the paragraph beginning “Permission-based isolation
has bugs.”

### Reply to paste into the comment thread

Confirmed after checking the runtime, workspace, storage, isolation, and
Connection Hub documentation against the implementation. This passage needs
four explicit scopes because KDCube enforces them at different boundaries.

One running KDCube deployment is bound to one tenant/project. PostgreSQL,
Redis, object storage, and filesystem infrastructure may be dedicated or
shared; shared deployments preserve scope through PostgreSQL schemas, Redis
namespaces, and object/file prefixes. Users inside one deployment share proc
workers, processor capacity, clients, and filesystem infrastructure.

Each request is bound to an authenticated actor and user. KDCube's
cross-runtime context preserves tenant, project, identity, routing, resolved
authority provenance, and accounting facts across supported runtime
transitions. Protected storage, APIs, tools, named services, and economics
surfaces enforce their own resource rules. Connection Hub resolves explicit
connection edges, delegated grants, and provider-account claims at authority
boundaries.

The strong physical-visibility claim applies to generated code in split
execution. KDCube ReAct uses logical refs and a sparse workspace; the LangGraph
reference app binds the same isolated workspace/executor as a normal
`run_python` tool. The agent may propose any ref string, including one produced
by compromised behavior. That string is only a locator. It does not choose the
tenant, project, user, authority, or storage root.

Trusted runtime code resolves the locator under `RuntimeCtx`, which was bound
before the model ran. Conversation history lookup always supplies the bound
`user_id`; the ref may choose a conversation and turn, but it cannot choose a
different user. Git lineage is rooted by tenant/project/user/conversation.
External owner refs go through a trusted owner resolver under the same carried
identity. A locator outside that scope yields no artifact, so no bytes enter the
workspace.

The restricted executor receives the resulting workspace, bounded output/log
paths, and the supervisor socket. Platform storage, app storage, deployment
descriptors, provider credentials, and other users' workspace roots remain on
the trusted side.

Please replace both commented paragraphs with the complete text below.

### Replacement for “Tenant isolation that's architectural” and the following paragraph

#### Isolation is architectural at every boundary

A running KDCube deployment is bound to one tenant and one project. Its
PostgreSQL, Redis, object storage, and filesystem services may be dedicated or
shared with other deployments. Shared topologies preserve tenant/project scope
through PostgreSQL schemas, Redis namespaces, and object/file prefixes.

Inside that deployment, users share workers, processor capacity, client pools,
and filesystem infrastructure. KDCube binds every request to an authenticated
actor and user. Its cross-runtime context preserves tenant, project, identity,
routing, authority provenance, and accounting facts as work moves through
async tasks, threads, subprocesses, isolated supervisors, app calls, and Data
Bus handlers.

Protected storage, API, tool, named-service, and economics surfaces enforce the
rule for their resource. Connection Hub verifies proofs and resolves explicit
connection edges, delegated grants, and connected-account claims when a call
crosses an authority boundary. Trusted custom application code that opens a
shared backend directly owns the corresponding authorization and scoping rule.

KDCube's isolated workspace and execution runtime add a structural boundary
around model-generated code. ReAct begins each turn with a sparse workspace and
operates on logical object references. The agent may propose any reference
string, including one produced by compromised behavior. That reference is an
untrusted locator; it grants no access and does not select the runtime user.

Tenant, project, actor, user, and authority are bound by authenticated runtime
context before ReAct runs. For conversation history, trusted runtime code uses
the bound `user_id` together with the requested conversation and turn. For
git-backed project state, the lineage is rooted by
tenant/project/user/conversation. External owner references go through trusted
owner resolvers under the carried request identity. A locator that does not
resolve inside that scope produces a missing or denied result, and no bytes are
placed in the workspace.

After successful resolution, trusted runtime code materializes the returned
bytes into the current user/conversation workspace. The same executor is
reusable from other agent frameworks: the LangGraph reference app exposes it
as a normal `run_python` tool.

In split execution, the restricted executor receives that materialized
workspace, bounded artifact and log paths, and an authenticated supervisor
socket. Platform storage, app storage, deployment descriptors, provider
credentials, and other users' workspace roots remain on the trusted side. When
generated code calls an approved tool, the executor sends an authenticated
request over the supervisor socket. The trusted tool implementation executes
in the supervisor under the carried request identity, grants, provider claims,
and runtime policy. This combines tenant/project namespacing, preserved user
identity, guarded authority enforcement, and a narrow physical view for
generated code.

### Answers by comment

- Comment 0, “is this safe to claim?”: The claim is safe in the scoped form
  above. Tenant/project separation is a deployment and storage-namespace
  contract. The agent proposes a locator while trusted runtime context fixes
  the user and authority used to resolve it. The physical-view guarantee then
  applies to generated code in the split executor used by ReAct and available
  to integrated agent frameworks. Users inside a deployment share runtime
  machinery.
- Comment 1, architecture-note confirmation: The architecture note maps to the
  generated-code boundary used by ReAct and the reusable ISO execution tool.
  The precise public terms are platform storage, app storage, deployment
  descriptors, provider credentials, and other users' workspace roots. Those
  surfaces remain outside the restricted executor view.
- Comment 2, mechanics/code-search concern: The implementation exposes each
  mechanism directly: tenant/project Redis namespace builders, PostgreSQL
  schema and storage path contracts, request-context snapshot/restore,
  user-keyed conversation lookup, tenant/project/user/conversation git lineage,
  Connection Hub authority projection, ReAct pull/checkout materialization,
  executor-global stripping, and split-Docker mount/network construction.

## Thread 2: Credentials And Tool Execution

Comments 3-5 are anchored to the paragraph beginning “When that code needs to
do something privileged.”

### Reply to paste into the comment thread

Boris is right on both points. Trusted integration tools may resolve and use a
user's provider credential server-side. The generated-code executor does not
receive that credential.

"Trusted tool runtime" is the correct general term. Tools can run in the app
process, a local subprocess, or the networked supervisor of an isolated run,
according to runtime policy. In split Docker, executor stubs call through an
authenticated socket and the trusted implementation executes in the
supervisor. The portable runtime context carries the authenticated user/session
authority to the trusted side; user-scoped secret and connected-account helpers
resolve credentials there.

### Replace the paragraph beginning “When that code needs to do something privileged” with

When generated code needs a privileged operation, it calls an approved tool
through the authenticated supervisor bridge. Trusted tool implementations run
according to configured policy: in process, in a local subprocess, or in the
networked supervisor. A provider integration resolves the current user's
credential server-side and checks the required grants or provider claims.
Generated code receives the tool contract and bounded result, while the
provider credential remains on the trusted side.

### Replace “The security guarantee holds whether or not...” with

The executor's ambient-authority guarantee holds even when generated code is
adversarial: its network, mounts, environment, and credentials do not expand.
Approved tool calls remain a separate authority path governed by allowlists,
grants, claims, parameter validation, economics, and trusted tool code.

### Answers by comment

- Comment 3, credentials injected to tools: Correct in effect, with more exact
  wording: trusted tool code resolves a credential through the SDK under the
  bound user context. The raw provider token is not injected into the model or
  generated-code executor.
- Comment 4, “tool sandbox has controlled credential access”: The credential
  distinction is right; “tool sandbox” is too universal. Use “trusted tool
  runtime” or, for split Docker specifically, “trusted supervisor.”
- Comment 5, “I don't think the tools are sandboxed”: Correct. Tool execution
  mode is configurable. The split supervisor is a separate container, while
  many normal tools execute in process and selected tools use local subprocess
  isolation.

## Thread 3: Audit Evidence And Compliance

Comments 6-7 are anchored to “Audit trails built for compliance.”

### Reply to paste into the comment thread

Agreed. A surviving log entry is evidence; it does not establish that the
action satisfies a policy, control, or regulation. “Immutable” and “replayable”
were also too broad as platform-wide defaults.

KDCube produces structured records across accounting, conversation events and
timelines, tool calls/results, isolated execution, and delegated authority.
Those records support audit, incident response, cost review, and control
testing. The deployment owns retention, access, integrity protection, export,
and any append-only or WORM requirement. The reviewer determines compliance.

### Replace “Audit trails built for compliance” and its paragraph with

**Evidence that supports audit and compliance review**

KDCube records structured evidence across the action journey: the acting user
and authority context, conversation and event identity, tool calls and results,
generated source and execution diagnostics, and attributable usage. These
records make an action inspectable. Deployment policy determines retention and
integrity controls; a reviewer determines whether the evidence satisfies the
applicable requirement.

### Answers by comment

- Comment 6, logs surviving versus satisfying review: Correct. A log or runtime
  record is evidence available to a reviewer; the reviewer determines whether
  it satisfies the applicable control. Use the replacement heading and
  paragraph above.
- Comment 7, “updated”: Please use the replacement heading and paragraph above.
  It also removes automatic claims of immutability, replayability, and
  regulatory sufficiency.

## Thread 4: AWS As A Market Signal

Comments 8-9 are anchored to the AgentCore paragraph.

### Reply to paste into the comment thread

Agreed. A vendor launch is neither proof of product success nor proof that a
category is inevitable. It is reasonable to cite AgentCore as evidence that a
major vendor is investing in managed agent runtime infrastructure. Please
replace the AgentCore paragraph with the text below; it treats AWS as one
market signal and directs readers to compare concrete runtime boundaries.

### Replacement copy

Amazon Bedrock AgentCore is one visible example of a managed agent runtime. Its
existence shows that runtime infrastructure is becoming an explicit product
surface alongside orchestration and observability. Teams should still evaluate
the concrete boundary: deployment model, isolation, identity, storage,
economics, portability, and operational control.

### Answers by comment

- Comment 8, AWS products can fail: Correct. A launch shows investment in the
  problem space; it does not prove product success or validate the whole
  category. Use the replacement paragraph above.
- Comment 9, “updated”: Please use the replacement paragraph above. It treats
  AgentCore as one market signal and removes the stronger category-validation
  claim.

## Implementation Evidence

Primary docs:

- `docs/sdk/agents/react/react-realm-refs-and-workspace-paths-README.md`
- `docs/sdk/agents/react/workspace/workspace-model-README.md`
- `docs/sdk/agents/react/workspace/workspace-lifecycle-and-distribution-README.md`
- `docs/sdk/agents/react/workspace/git-backed-workspace-engineering-README.md`
- `docs/runtime/cross-runtime-context-README.md`
- `docs/exec/README-iso-runtime.md`
- `docs/exec/README-runtime-modes-builtin-tools.md`
- `docs/configuration/bundle-runtime-configuration-and-secrets-README.md`
- `docs/hosting/attachments-system.md`
- `docs/hosting/files-storage-system-README.md`

Implementation anchors:

- `sdk/solutions/react/tools/pull.py`: explicit ref materialization through the
  conversation/artifact path or a registered owner rehoster.
- `sdk/solutions/react/browser.py`: cross-conversation turn lookup keeps
  `RuntimeCtx.user_id` as the lookup user.
- `sdk/context/retrieval/ctx_rag.py`: turn materialization queries by bound
  user plus requested conversation and turn.
- `sdk/solutions/react/workspace.py`: lineage refs include tenant, project,
  user, and conversation.
- `sdk/solutions/react/git_workspace.py`: cross-conversation scoping replaces
  only the conversation segment and retains tenant/project/user.
- `sdk/solutions/react/solution_workspace.py`:
  `build_exec_snapshot_workspace(...)` builds a reduced execution snapshot from
  referenced files.
- `sdk/runtime/external/docker.py`: split executor receives the narrow mounts,
  `--network none`, read-only root, and no supervisor storage mounts.
- `sdk/runtime/isolated/executor_payload.py`: strips descriptor, storage,
  communicator, tool-module, and portable-context payloads before generated
  code starts.
- `sdk/runtime/comm_ctx.py`: captures and restores authenticated request context
  for trusted child runtimes.

Focused checks run on 2026-07-14:

```text
4 passed

test_split_executor_argv_is_networkless_and_does_not_mount_supervisor_data
test_build_executor_runtime_globals_strips_privileged_paths_and_descriptors
test_ensure_current_turn_git_workspace_bootstraps_lineage_branch
test_external_exec_requires_pull_for_unmaterialized_historical_file
```

Identity-scope checks added on 2026-07-14:

```text
2 passed

test_cross_conversation_turn_lookup_keeps_runtime_bound_user
test_cross_conversation_git_lineage_keeps_tenant_project_and_user
```
