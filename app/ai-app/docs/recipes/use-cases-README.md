---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/use-cases-README.md
title: "Recipe Index: What Problem Does KDCube Solve?"
summary: "Problem-first index of KDCube use cases for agents and builders: sixteen practitioner problems — scheduling vs judgement, credential lifecycle, multi-user isolation, context bloat, lineage, memory across sessions, delegated mail/Slack, notebook-to-production, machine-checkable coding agents, disposable automation tokens, trusted generated code, publishable agent output, per-user agent tuning, attributable cache costs, shared workspace pages, mid-run answers — each answered with the concrete mechanism and links to the deep docs."
status: current
tags: ["recipes", "use-cases", "index", "problems", "platform", "agents"]
updated_at: 2026-07-08
keywords:
  [
    "what problem does kdcube solve",
    "does kdcube support",
    "cron or agent",
    "agent credentials break",
    "multi-user agent workspace",
    "context window bloat",
    "where did the context come from",
    "agent memory across sessions",
    "agent gmail slack delegated",
    "notebook to production",
    "coding agent in pipeline",
    "automation token revoke",
    "run generated code safely",
    "publish agent output web",
    "per-user tools model pick",
    "prompt cache cost attribution",
    "internal dashboard frontend",
    "answer arrives mid-run",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
---
# Recipe Index: What Problem Does KDCube Solve?

Problem in, solution out. Each entry states a problem the way practitioners
state it, names the mechanism that answers it, and links the deep docs for the
HOW. Use the index to find the problem; use the entry to confirm the fit.

## Problem index

| # | The problem, as stated | The KDCube answer | Deep docs |
| --- | --- | --- | --- |
| 1 | "A cron calling an LLM covers it — why an agent?" | The trigger decides: clock+known path is a cron; open intent, events, and mid-work walls become agent turns — same app, same budget | [scheduled jobs](../sdk/bundle/bundle-scheduled-jobs-README.md) · [conversation events](../sdk/bundle/bundle-conversation-events-and-react-output-README.md) |
| 2 | "I built agent credential handling — it breaks weekly" | Connection Hub: tools resolve claims, never keys; refresh/deny/reconnect is the broker's loop | [Connection Hub](../sdk/solutions/connections/connection-hub-solution-README.md) |
| 3 | "One agent demo is easy; ten colleagues sharing it is not" | Identity (tenant/project/user) travels every layer; per-user data, budgets, settings inside an admin grant | [scene](components/scene-README.md) · [economics](../sdk/bundle/bundle-economics-integration-README.md) |
| 4 | "Context fills with tool defs and copied payloads" | Handles in, bytes out: ~ten generic ops, lean hits + refs, signed URLs, cached instructions + uncached tail | [named services for agents](kdcube_for_agents/named-services-mcp-README.md) · [context caching](../sdk/agents/react/context-caching-README.md) |
| 5 | "When an answer is wrong, the lineage is already lost" | Context enters as addressable refs; the per-turn timeline is the ledger | [canvas](../sdk/solutions/canvas/canvas-sdk-solution-README.md) · [conversation search](../sdk/solutions/conversation/search-README.md) |
| 6 | "Every session starts from zero" | `conv:` episodic recall + `mem:` co-managed durable notes; reads aggregate across the linked identity family | [memory overview](../sdk/memory/user-memories-overview-README.md) · [reconciliation](../sdk/memory/user-memories-reconcilation-README.md) |
| 7 | "The assistant should use my Gmail/Slack — without an all-powerful token" | Grants at the door, provider claims inside; unmet claims drop tools per turn, never block it | [mail](connections/integrations/mail-named-service-README.md) · [Slack](connections/integrations/slack-README.md) |
| 8 | "Production makes the AI part the smallest problem" | One app unit carries agents, APIs, cron, MCP, widgets, secrets; the platform is the rest of the checklist | [developer guide](../sdk/bundle/bundle-developer-guide-README.md) · [delivery](../sdk/bundle/bundle-delivery-and-update-README.md) |
| 9 | "A pipeline can't review a coding agent's work by eye" | Declared-output harness: the agent declares its edits; validators check exactly what was declared | [Claude Code agent](../sdk/agents/claude/claude-code-README.md) |
| 10 | "A nightly script shouldn't hold a person's session" | Automation tokens bound to resources + grants; one-click revoke logs out the bound session | [automation access](connections/create-delegated-automation-access-README.md) |
| 11 | "The only way to run generated code is a shell — security says no" | Execution is a contracted tool call in an isolated runtime; outputs are declared, hosted, delivery-verified | [ISO runtime](../exec/README-iso-runtime.md) · [custom tools](../sdk/tools/custom-tools-README.md) |
| 12 | "Agent reports live in chat where no search engine reaches them" | A public-content provider: crawlable HTML, sitemaps, `410` on retract, stable URLs | [public content](../sdk/solutions/cdn-pub/public-content-solution-README.md) · [publish recipe](resource_sharing/publish-discoverable-content-README.md) |
| 13 | "Every user wants a different agent; we ship one config" | The config is an inventory; each user narrows it (deny-lists + model pick), applied per turn as configured ∩ chosen | [construct a ReAct agent](../sdk/agents/react/how/how-to-construct-react-agent-README.md) · [user settings](../sdk/solutions/user-settings/user-settings-solution-README.md) |
| 14 | "Cache rebuilds are invisible; nobody can attribute the cost" | Placement by lifecycle; the `[CACHE]` cold-turn marker joins the rebuild premium to its cause; the user holds the policy | [context caching](../sdk/agents/react/context-caching-README.md) |
| 15 | "Every internal tool becomes its own frontend project" | A scene is config + content over a shared host; widgets are served by the apps that own them | [scene configuration](../sdk/solutions/scene/config/README.md) · [scene recipe](components/scene-README.md) |
| 16 | "The answer arrives while the agent is still working" | Every conversation carries an event lane; answers fold into the live turn or wait as next-turn context | [conversation events](../sdk/bundle/bundle-conversation-events-and-react-output-README.md) |

## 1. Scheduled pipelines that sometimes need judgement

Problem: "Most agent use cases can be done with a cron that calls an LLM."

Correct — the **trigger** decides:

- **Clock + known path → cron.** `@cron` / `@on_job` run fixed pipeline steps
  (gather, rank, publish) in production.
- **A person asks → agent turn.** One open intent across domains; the plan is
  made mid-turn.
- **An event fires → agent turn.** Alerts and callbacks land in the app's
  `@on_reactive_event` hook; they start work or extend a live turn.
- **A wall appears → dialogue.** Missing consent becomes a one-click question —
  and dialogue is not schedulable.

Both run in the same app, on the same budget line; a scheduled step that starts
needing judgement becomes an agent turn without a rewrite.

Docs: [scheduled jobs](../sdk/bundle/bundle-scheduled-jobs-README.md) ·
[app lifecycle](../sdk/bundle/bundle-lifecycle-README.md) ·
[conversation events](../sdk/bundle/bundle-conversation-events-and-react-output-README.md)

## 2. Agents and the keys to your accounts

Problem: "I built credential handling for my agents myself — and every week
something breaks."

```text
BUILD IT YOURSELF                          WITH KDCUBE
OAuth per provider · token store           Connection Hub: a claims broker
refresh scheduling · live-401 recovery  →  tools resolve claims, never keys
scope upgrades · revocation · failure UX   structured denials with the fix
```

- **Tools resolve claims, never keys** — the broker returns a short-lived
  credential handle; client secrets live in descriptors tool code cannot read.
- **Live 401 → refresh once → retry once** → only then mark the account and
  show a reconnect card with the provider's real error.
- **Every denial is structured:** reason, labeled account candidates, fix URL,
  retry hint.
- **External agents get their own grants** (`mail:read`, `slack:write`) —
  separate from provider claims, revocable, consent-snapshot-bound.

Docs: [how agents connect to KDCube](quickstart/explore-how-agents-connect-to-kdcube-README.md) ·
[Connection Hub](../sdk/solutions/connections/connection-hub-solution-README.md) ·
[delegate to an external client](connections/delegate-kdcube-service-to-external-client-README.md) ·
[props & secrets](../sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md)

## 3. One workspace, many humans

Problem: ten colleagues sharing agents, data, budgets, and integrations — with
each person's things staying theirs.

- **Per-user everything:** conversations, memories, boards, connected accounts,
  budgets — identity (tenant/project/user) travels through every layer.
- **Per-user tuning inside an admin grant:** users toggle tools and pick models
  from the chat composer; nothing widens beyond the grant (entry 13).
- **The scene is the shared room:** summonable widgets, auth-gated, with
  cross-widget drag resolving under the dragging user's identity.
- **Per-user economics from day one:** budgets, live usage, who spent what on
  which agent.

Docs: [scene](components/scene-README.md) ·
[economics](../sdk/bundle/bundle-economics-integration-README.md)

## 4. Context that stays lean as capability grows

Problem: the context fills with tool definitions, stale history, and copied
payloads — quality drops before the window overflows.

```text
BUILD IT YOURSELF                          WITH KDCUBE
tool-list pruning · payload truncation     one fixed grammar (~10 generic ops)
base64 handling · window management     →  lean hits + refs · bytes as signed
summarizer pipeline · cache-aware layout   URLs · cached front + uncached tail
```

- **Tools:** ~ten generic operations replace one-tool-per-integration; domains
  self-describe at runtime.
- **Payloads:** search returns lean hits (ref · title · snippet); `get` fetches
  on demand; binaries travel as signed URLs.
- **History:** past turns are searchable (`conv:`), never replayed; working
  summaries compress each turn.
- **Cache:** durable instructions stay cached; the per-turn ANNOUNCE tail
  carries only what changed (entry 14 for the cost story).

Docs: [named services for agents](kdcube_for_agents/named-services-mcp-README.md) ·
[context caching](../sdk/agents/react/context-caching-README.md) ·
[file hosting](resource_sharing/hosting-README.md)

## 5. Knowing where the context came from

Problem: when an answer is wrong, the lineage of what the agent saw is the
first thing needed — and usually the first thing lost.

- **Everything is a ref** (`conv:fi:…`, `mem:…`, `cnv:…`) that both sides
  resolve later; context enters as addressable objects, never pasted text.
- **Search hits carry their paths** back to the turn and event they came from.
- **Board pins keep provenance;** sharing a board shares it.
- **The per-turn timeline is the ledger:** what the agent read, produced, and
  executed is a query.

Docs: [canvas / pin board](../sdk/solutions/canvas/canvas-sdk-solution-README.md) ·
[conversation search](../sdk/solutions/conversation/search-README.md)

## 6. Memory that survives the session

Problem: every session starts from zero; the agent asked yesterday and asks
again today.

- **Episodic recall** (`conv:`): the temporal record — every turn indexed with
  its production context. "What did we decide in May" is one search.
- **Durable knowledge** (`mem:`): co-managed typed notes — the agent proposes,
  a reconciler merges, the user edits in the memory widget.
- **Reads can aggregate across the user's linked identity family** — one
  person, several sign-ins, one memory.
- **Both feed the turn** as retrieved, cited context under the lean-hits
  discipline.

Docs: [memory overview](../sdk/memory/user-memories-overview-README.md) ·
[reconciliation](../sdk/memory/user-memories-reconcilation-README.md) ·
[conversation recall](../sdk/solutions/conversation/search-README.md)

## 7. Working the company's mail and Slack on delegated trust

Problem: the assistant should search my Gmail and post to our Slack; an
all-powerful token is out of the question.

- **Two consents, explicit:** the user connects the provider once, choosing
  exact claims; the agent receives only the namespace grants approved for it —
  revocable, never wider.
- **A missing account never blocks the turn:** tools with unmet claims drop for
  that turn and the agent learns it in ANNOUNCE
  (`[INACTIVE TOOLS THIS TURN]`), with a connect hint naming the provider and
  tools.
- **Multi-account is explicit:** labeled results; actions never pick an account
  silently.
- **Files cross the boundary** through signed download URLs and upload slots —
  bytes over HTTP, never inside tool calls.

Docs: [mail over MCP](connections/integrations/mail-named-service-README.md) ·
[Slack integration](connections/integrations/slack-README.md) ·
[file hosting](resource_sharing/hosting-README.md)

## 8. From notebook to production app, updated live

Problem: production means REST, streaming, MCP in and out, jobs, widgets,
secrets, zero-downtime upgrades — the AI part becomes the smallest problem.

- **One unit carries it all:** agents, API operations, cron jobs, MCP surfaces,
  widgets with their build pipeline, config/secrets contract.
- **The platform supplies the rest:** ingress, auth, per-user accounting,
  isolation, hot reload, CI/CD.
- **Upgrades are descriptor changes,** applied to a live deployment.
- **Products compose as apps of apps,** each upgraded independently.

Docs: [app developer guide](../sdk/bundle/bundle-developer-guide-README.md) ·
[delivery & update](../sdk/bundle/bundle-delivery-and-update-README.md)

## 9. A coding agent inside automation, trusted by machines

Problem: a human reviews a coding agent's work with their eyes; a pipeline
can't.

- **Dedicated, mapped workspace** per session — the agent works only there.
- **Framed structured output:** the agent declares what it did, in which files.
- **Validators inspect exactly the declared edits;** only validated work is
  committed.
- **Deterministic session binding** to user + conversation — follow-up and
  steer turns resume the same context.

Docs: [Claude Code agent](../sdk/agents/claude/claude-code-README.md) ·
[app agent integration](../sdk/bundle/bundle-agent-integration-README.md)

## 10. Machine access that is born disposable

Problem: a script files tasks every night; nobody wants it holding a person's
session.

- **A token bound to declared resources and grants,** with an expiry, created
  and revoked in the Connection Hub UI.
- **Grant checks use the matching resource entry** — never a global union of
  every grant on the token.
- **Revocation is immediate:** record deleted, bound session logged out, next
  call rejected.
- **Same guard rails as human access** — per-operation grants, accounting.

Docs: [delegated automation access](connections/create-delegated-automation-access-README.md)

## 11. Generated code you can trust like a tool

Problem: the agent writes useful code — and the only way to run it is a shell
tool with the agent's hands on it.

- **Declare, then run:** the agent submits code and declares the expected
  output files — user-facing deliverables vs internal intermediates.
- **Run in isolation:** Docker ISO runtime with a split container strategy,
  quotas, and a monitor; isolation level is deployment config, never agent
  choice.
- **Outputs honor the contract:** user-facing files are hosted and shown —
  delivery verified, never assumed (`delivery_failed.file_hosting` fires
  otherwise); everything, including failures, lands in the turn timeline.
- **Bounded I/O composes with tool guards** — accounting, inventories, user
  toggles.

Docs: [ISO runtime](../exec/README-iso-runtime.md) ·
[app runtime](../sdk/bundle/bundle-runtime-README.md) ·
[custom tools & declared files](../sdk/tools/custom-tools-README.md)

## 12. Agent output the web can actually find

Problem: the agent produces excellent reports — and they live in chat, where no
search engine, shared link, or newsletter reaches them.

- **The app declares a public-content provider** (`@public_content`): the
  platform renders crawlable HTML with JSON-LD, canonical and OG tags at stable
  URLs.
- **Sitemaps come from the registry:** publish and retract update the per-alias
  sitemap; a retracted item serves `410`.
- **Publishing is an operation** — batched, so a nightly pipeline and a Publish
  button are the same code path.
- **A CDN fronts the origin through rewrite mapping;** the app remains the
  single source of truth.

Docs: [public content solution](../sdk/solutions/cdn-pub/public-content-solution-README.md) ·
[public content provider](../sdk/bundle/public-content-provider-README.md) ·
[publish recipe](resource_sharing/publish-discoverable-content-README.md)

## 13. One agent config, many users

Problem: "Every user wants a different agent — fewer tools, a cheaper model, no
MCP — and we ship one config."

```text
BUILD IT YOURSELF                          WITH KDCUBE
per-user feature flags · allow-list UI     admin grant ∩ user pick
per-user model routing · storage        →  composer "+" menu · per turn
permission clamping · staleness            fail-open, never wider
```

- **Config grants the inventory:** `surfaces.as_consumer.agents.<id>` declares
  the tools/skills; the admin-allowed model list is `supported_models`.
- **The chat "+" menu narrows it:** deny-lists for tools, skills, MCP servers,
  and namespaces, plus one model pick — stored per (user, app, agent).
- **Applied per turn as `configured ∩ chosen`,** clamped on write, fail-open —
  a broken selection never breaks the agent.
- **New config entries default ON for everyone;** system tools stay locked on.

Docs: [construct a ReAct agent](../sdk/agents/react/how/how-to-construct-react-agent-README.md) ·
[user settings](../sdk/solutions/user-settings/user-settings-solution-README.md) ·
[chat with a ReAct agent](components/chat-with-react-agent-README.md)

## 14. Prompt-cache costs nobody can attribute

Problem: long conversations get expensive, and cache rebuilds are invisible —
nobody can say which action caused what spend.

- **Placement by lifecycle:** durable teaching lives in the cached
  instructions; turn-local state in the uncached ANNOUNCE tail — the volatile
  costs nothing.
- **Every deliberate invalidation is marked:** the `[CACHE]` cold-turn marker
  and `cache_cold_turn` accounting metadata join the rebuild premium to the
  causing action — one identifiable component within the turn's spend sum.
- **The user holds the policy:** accept · confirm · defer-cold ·
  defer-conversation (admin default + bounds); under confirm the composer turns
  a costly switch into an inline choice.
- **Both switches are named, neither silent:** a model pick rebuilds in a
  per-model cache namespace; a capability toggle colds one turn, then caching
  resumes.

Docs: [context caching](../sdk/agents/react/context-caching-README.md) ·
[construct a ReAct agent](../sdk/agents/react/how/how-to-construct-react-agent-README.md) ·
[user settings](../sdk/solutions/user-settings/user-settings-solution-README.md)

## 15. Every new internal tool is a new frontend project

Problem: every team needs its workspace page — chat here, tasks there, usage
somewhere — and each one becomes its own web app.

- **Components are served widgets** iframed from their owning apps — declared
  in `surfaces.as_consumer.ui.scene.components` (`bundle_id` +
  `widget_alias`).
- **Behavior is config:** `placement: docked|floating`, `rail`, `gated` auth,
  `drop` patterns routed to a `target_surface`.
- **Context moves across surfaces:** cross-surface drag and provider-resolved
  open actions are built into the host.
- **The reusable host is `@kdcube/components-react/scene`** — a new alias
  mounts any deployed app's widget.

Docs: [scene configuration](../sdk/solutions/scene/config/README.md) ·
[scene recipe](components/scene-README.md)

## 16. The answer arrives while the agent is still working

Problem: the agent asks for something — an approval, a callback, a human — and
the answer shows up mid-run.

- **Every conversation carries an ordered event lane;** systems the agent
  triggers answer back into it.
- **A live turn folds arriving events mid-work;** otherwise they wait as the
  context the next turn opens with. The consent flow is the worked story: the
  grant lands as an event and the agent circles back and finishes.
- **Events inform; reactive hooks act:** a lane event never starts a turn by
  itself — starting work is the explicit `@on_reactive_event` contract.
- **Folding is total:** every consumed event advances the turn's bookkeeping —
  an unreadable event can never wedge a completion.

Docs: [conversation events](../sdk/bundle/bundle-conversation-events-and-react-output-README.md) ·
[app events](../sdk/bundle/bundle-events-README.md) ·
[Connection Hub](../sdk/solutions/connections/connection-hub-solution-README.md)

## The pattern behind the answers

Four moves recur: **identity travels everywhere** · **context holds handles,
the platform holds bytes** · **domains describe themselves behind a fixed
grammar** · **every denial explains its own fix**.
