---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/artifact-resolution-and-materialization-README.md
title: "Artifact Resolution And Materialization"
summary: "How canonical conversation files and owner-domain refs become authorized bytes in an agent turn workspace."
tags: ["runtime", "harness", "events", "workspace", "artifacts", "namespaces"]
updated_at: 2026-07-18
keywords:
  [
    "artifact resolver",
    "namespace rehoster",
    "conv:fi",
    "owner namespace",
    "pull",
    "materialization",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
---
# Artifact Resolution And Materialization

Artifact resolution turns a logical locator into authorized bytes.
Materialization places those bytes into a concrete turn workspace.

```text
source ref
  conv:fi:... | mem:... | task:... | cnv:... | custom:
       |
       v
namespace owner resolves under carried request identity
       |
       v
destination chosen by artifact meaning
       |
       v
logical_path + physical_path + metadata
```

The generic harness never derives a provider's storage path from its ref.

## Two Resolution Paths

### Conversation files

`conv:fi:` is resolved by
`runtime.harness.events.resolver.read_event_ref_bytes(...)`.

The resolver uses:

- tenant and project from trusted runtime context;
- the bound user/owner;
- conversation and turn from the canonical ref;
- configured conversation storage.

The same resolver backs generic download actions and framework-neutral workspace
pull, so a supported ref has one byte identity across chat, canvas, and agent
adapters.

### Owner-domain objects

Refs such as `mem:`, `task:`, and `cnv:` remain opaque outside their provider.
The owner may expose:

- a named-service `object.get` stream;
- an object-action/download operation;
- an event-subsystem namespace rehoster;
- another explicit SDK/service resolver.

The owner checks grants and identity before returning content.

## Artifact Meaning Chooses Destination

| Source meaning | Workspace destination |
| --- | --- |
| Editable project/app state | `git/projects/<scope>/...` |
| Produced report/export/rendered file | `files/<scope>/...` |
| Story/canvas/wizard state | `git/snapshots/<scope>/...` |
| User upload | `attachments/<name>` |
| Event/domain evidence | `external/<kind>/attachments/<event_id>/<name>` |

Filename extension does not choose the area. Visibility does not choose it
either.

## Framework-Neutral Pull

`runtime.harness.workspace.pull_refs_into_dir(...)` resolves canonical refs and
writes bytes into a caller-supplied directory. It returns one result per ref and
does not abort the entire batch when one ref fails.

This primitive is suitable for ported-agent adapters that need plain local
files but do not expose ReAct tools.

## Namespace Rehosters

The SDK event subsystem can discover a registered rehoster:

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster


@artifact_namespace_rehoster(namespace="example")
async def rehost_example_ref(*, ref, key, ctx_browser, outdir, **_):
    ...
```

The handler is trusted app/SDK code. It must:

1. parse only its own namespace;
2. authorize under the carried request identity;
3. fetch the exact owner object;
4. choose a destination by object meaning;
5. use canonical harness path builders;
6. write bytes inside the supplied artifact root;
7. return the actual source, logical, and physical paths.

Rehosters are discovered only from loaded tool/event modules. Registration does
not make them model-callable.

For common named-service objects, prefer the generic named-service artifact
bridge over custom per-namespace storage code.

## ReAct Adapter

`react.pull` adds a model-facing orchestration layer:

```text
model selects ref
  -> ReAct adapter asks EventSourceSubsystem for owner rehoster
  -> trusted handler resolves and materializes
  -> adapter returns actual logical_path and physical_path
  -> model continues from returned paths
```

`react.checkout` is separate. It promotes historical `git/projects` state into
the current editable project tree. Ordinary pull does not silently make a file
editable project state.

## Ported-Agent Adapter

The ported LangGraph example demonstrates the framework-neutral path:

```text
turn input/event attachment ref
  -> runtime.harness workspace pull or event byte resolver
  -> adapter-owned turn directory
  -> graph/code execution reads local file
  -> resulting file is emitted with canonical conv:fi identity
```

It does not import a ReAct model protocol to get this behavior.

## Security Rules

- A ref is untrusted locator input.
- Runtime identity and grants do not come from the ref.
- Owner providers authorize their own objects.
- Parent traversal and absolute target paths are rejected.
- Failed resolution produces no bytes.
- A preview is not a substitute for complete file bytes.
- A logical path is not a download URL.
- Do not scan an object store to implement prefix pull; use owner metadata or
  provider APIs.

## Related Provider Documentation

The decorator, discovery, and module-loading details are documented in
[Event Source Subsystem](../../../sdk/events/event-subsystem-README.md).
Named-service `object.get` and streaming contracts are documented under
[Namespace Service Providers](../../../sdk/namespace-services/providers-README.md).
