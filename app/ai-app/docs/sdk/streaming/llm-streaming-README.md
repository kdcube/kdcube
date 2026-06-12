---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/llm-streaming-README.md
title: "LLM Streaming And Accountable Invocation"
summary: "Provider-agnostic LLM invocation interface: role-mapped clients, streaming deltas/events, normalized usage, accounting, tool-call events, citations, and custom endpoint compatibility."
tags: ["sdk", "streaming", "llm", "accounting", "service-hub", "models"]
keywords:
  - "ModelServiceBase"
  - "ModelRouter"
  - "role_models"
  - "stream_model_text_tracked"
  - "track_llm"
  - "ServiceUsage"
  - "tool-call streaming"
  - "provider-agnostic model invocation"
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/channeled-streamer-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-state-machine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-README.md
---
# LLM Streaming And Accountable Invocation

This document describes the LLM invocation layer used by KDCube agents and
applications. It is the boundary between application code and concrete model
providers.

The purpose of this layer is to let applications call a model by **role** and
receive a stable stream/result contract while provider-specific details remain
inside the runtime.

Core implementation:

- [inventory.py](../../../src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/inventory.py)

Related implementation:

- [usage.py](../../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py)
- [accounting package](../../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting)
- [OpenAI message normalization](../../../src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/openai.py)
- [Gemini client](../../../src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/gemini.py)
- [service hub errors](../../../src/kdcube-ai-app/kdcube_ai_app/infra/service_hub/errors.py)

## What It Is

The LLM streaming layer provides:

- role-to-provider/model resolution
- provider client construction and caching
- non-streaming structured model calls
- non-streaming freeform model calls
- streaming model calls
- normalized text/thinking/tool/citation/final events
- usage normalization
- accounting hooks
- custom model endpoint compatibility
- provider-independent error reporting back to the caller

It is not a UI streamer by itself. UI/channel streaming is layered above it by
the channeled streamer and agent runtimes.

## Architectural Boundary

Applications should not treat provider SDKs as their primary integration
surface. The runtime boundary is:

```text
application / agent / application tool
  |
  | role + messages + optional tools + callbacks
  v
ModelServiceBase
  |
  | role lookup
  v
ModelRouter
  |
  | provider/model client
  v
OpenAI / Anthropic / Gemini / Custom endpoint
  |
  | provider stream / provider response
  v
normalized events + usage + accounting + service_error
```

This keeps application code independent from provider-specific:

- SDK object types
- message block formats
- stream event names
- usage metadata formats
- tool-call streaming formats
- citation/search event formats
- retry/error metadata

## Main Types And Responsibilities

### `ConfigRequest`

Input shape for constructing runtime model configuration.

Important fields:

- `role_models`
- provider keys: `openai_api_key`, `claude_api_key`, `google_api_key`
- custom endpoint fields: `custom_model_endpoint`,
  `custom_model_api_key`, `custom_model_name`
- embedding fields
- tenant/project/application bundle context

### `Config`

Resolved runtime configuration object.

Responsibilities:

- read settings/secrets defaults
- hold provider credentials
- hold embedder configuration
- hold role-to-provider/model mapping
- provide default roles for backward compatibility
- normalize custom endpoint and KB/search settings

Default base roles:

- `classifier`
- `query_writer`
- `reranker`
- `answer_generator`
- `format_fixer`

Applications can add additional role names through `role_models`.

### `ModelRouter`

Provider-aware client factory and cache.

Responsibilities:

- resolve role -> `{provider, model}`
- construct provider clients lazily
- cache by `(provider, model, role, temperature)`
- expose `describe(role)` for accounting metadata

Provider branches:

- OpenAI via `ChatOpenAI`
- Anthropic via Anthropic SDK
- Gemini via KDCube Gemini client
- custom HTTP endpoint via `CustomModelClient`

### `ModelServiceBase`

Application-facing model service.

Main entrypoints:

- `get_client(role, temperature)`
- `embed_texts(texts)`
- `call_model_with_structure(...)`
- `call_structured_role(...)`
- `call_model_text(...)`
- `stream_model_text(...)`
- `stream_model_text_tracked(...)`

`stream_model_text_tracked(...)` is the recommended high-level streaming
entrypoint because it combines provider streaming, event normalization,
accounting, diagnostics, and final result construction.

## Role-Mapped Model Resolution

Applications call by role, not by hard-coded provider/model.

Example role map:

```json
{
  "answer_generator": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-5"
  },
  "classifier": {
    "provider": "openai",
    "model": "gpt-4.1-mini"
  },
  "report_writer": {
    "provider": "gemini",
    "model": "gemini-2.5-pro"
  }
}
```

Resolution flow:

```text
role
  -> Config.ensure_role(role)
  -> provider/model spec
  -> ModelRouter.get_client(role, temperature)
  -> provider client
```

If a role is missing, `Config.ensure_role(...)` fills it from the default role
spec. `format_fixer` is special-cased to prefer the configured format-fixer
model.

## Message Input Contract

The service accepts LangChain message objects:

- `SystemMessage`
- `HumanMessage`
- `AIMessage`
- `BaseMessage`

The helper functions support normalized multimodal blocks:

- `create_cached_system_message(...)`
- `create_cached_human_message(...)`
- `create_modal_message(...)`
- `create_document_message(...)`
- `create_image_message(...)`

Supported block categories:

- text
- image
- document/PDF

Provider-specific conversion is handled inside the service layer, not by
application code.

## Streaming Entry Point

Recommended high-level call:

```python
ret = await model_service.stream_model_text_tracked(
    client,
    messages,
    role="answer_generator",
    on_delta=emit_text_delta,
    on_event=emit_event,
    on_tool_result_event=emit_tool_event,
    on_complete=handle_complete,
    tools=allowed_tools,
)
```

Important parameters:

- `client`: provider client from `get_client(...)`
- `messages`: normalized message list
- `role`: logical role used for model/accounting metadata
- `temperature`
- `max_tokens`
- `max_thinking_tokens`
- `tools`
- `tool_choice`
- callback hooks

Callback hooks:

- `on_delta(text)`
- `on_thinking(event)`
- `on_event(event)`
- `on_tool_result_event(event)`
- `on_complete(result)`

## Normalized Event Contract

The streaming layer normalizes provider-specific stream output into event
objects.

Event types:

- `text.delta`
- `thinking.delta`
- `tool.start`
- `tool.arguments_delta`
- `tool.use`
- `tool.search`
- `citation`
- `final`

### Text Delta

```json
{
  "event": "text.delta",
  "text": "partial text"
}
```

### Thinking Delta

```json
{
  "event": "thinking.delta",
  "text": "partial reasoning/status text",
  "stage": 0
}
```

### Tool Call Lifecycle

```text
tool.start -> tool.arguments_delta* -> tool.use
```

`tool.start`:

```json
{
  "event": "tool.start",
  "id": "toolu_xxx",
  "name": "tool_name",
  "index": 0
}
```

`tool.arguments_delta`:

```json
{
  "event": "tool.arguments_delta",
  "index": 0,
  "delta": "{\"partial\":"
}
```

`tool.use`:

```json
{
  "event": "tool.use",
  "id": "toolu_xxx",
  "name": "tool_name",
  "input": {},
  "index": 0
}
```

The model service reports model-side tool requests. The agent/application
decides which tools are allowed and how tool execution is handled.

### Citation Event

```json
{
  "event": "citation",
  "title": "source title",
  "url": "https://...",
  "start": 10,
  "end": 40
}
```

### Final Event

```json
{
  "event": "final",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

## Final Result Contract

`stream_model_text_tracked(...)` returns:

```json
{
  "text": "full final text",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "provider_message_id": null,
  "model_name": "model-id",
  "thoughts": [],
  "tool_calls": [],
  "citations": [],
  "service_error": null
}
```

Fields:

- `text`: concatenated visible text deltas
- `usage`: normalized usage dict
- `provider_message_id`: provider message id when available
- `model_name`: resolved model id
- `thoughts`: grouped thinking deltas
- `tool_calls`: complete model-side tool calls
- `citations`: normalized citation/search references
- `service_error`: normalized service error payload on failure

## Accounting Boundary

LLM calls use `track_llm(...)` where accounting is required.

Accounting metadata comes from:

- provider extractor
- model extractor
- role metadata
- provider usage metadata
- fallback approximated token counts

Normalized usage type:

- `ServiceUsage`

The accounting boundary records:

- provider
- model name
- logical role
- request count
- prompt/completion/total tokens
- success/failure state
- structured metadata when available

Related docs:

- [accounting-README.md](../../accounting/accounting-README.md)

## Provider Handling

### OpenAI

OpenAI-compatible chat models are constructed through `make_chat_openai(...)`.

Important behavior:

- enables response API mode when needed
- requests stream usage when supported
- skips temperature for models that do not support it
- normalizes message blocks before provider call

### Anthropic

Anthropic calls use the Anthropic SDK and support:

- streaming text
- thinking deltas when configured/supported
- native tool-call streaming
- citation/search-style events when provider output includes them

### Gemini

Gemini calls are routed through the KDCube Gemini client and can use configured
cache behavior:

- `gemini_cache_enabled`
- `gemini_cache_ttl_seconds`

### Custom Endpoint

`CustomModelClient` implements the minimum local contract for configured custom
model endpoints:

- `ainvoke(messages, **kwargs)`
- `astream(messages, **kwargs)`

Supported streaming shape:

- `text/event-stream`
- JSON `data:` chunks
- `[DONE]` terminator
- final JSON usage payload when provided

Usage may come from:

- response JSON body
- response headers
- normalized fallback

## Error Handling

Provider exceptions are converted into service-level error payloads where the
streaming path can do so safely.

On streaming failure, the final return object includes:

```json
{
  "text": "Model call failed: ...",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "provider_message_id": null,
  "model_name": "model-id",
  "thoughts": [],
  "tool_calls": [],
  "citations": [],
  "service_error": {
    "type": "...",
    "stage": "stream_loop",
    "service_name": "StreamTracker"
  }
}
```

The caller should surface `service_error` when present instead of treating the
result as a normal answer.

## Relationship To Channeled Streaming

This document describes provider/model streaming.

The channeled streamer is a higher-level protocol for asking the model to emit
multiple logical channels in a single stream.

Layering:

```text
ModelServiceBase.stream_model_text_tracked
  -> raw normalized text/tool/citation/final events
  -> channeled streamer parses <channel:name> blocks
  -> agent/runtime emits UI/application deltas
```

See:

- [channeled-streamer-README.md](channeled-streamer-README.md)

## Common Failure Modes

- Missing role mapping: runtime falls back to default role spec unless the
  provider itself is unavailable.
- Missing provider credential: provider client construction fails.
- Unsupported provider id: `ModelRouter` raises `Unknown provider`.
- Provider stream format drift: stream may return a `service_error`.
- Tool-call JSON drift: tool argument deltas may fail downstream validation.
- Provider usage missing: accounting uses normalized fallback/approximation.
- Custom endpoint missing SSE/final usage: stream still completes when text
  chunks are valid, but usage may be empty or approximated.

## Operational Notes

- Role/model mapping belongs in runtime/application configuration, not in
  business logic.
- Provider credentials must come from the runtime settings/secrets layer.
- Applications should pass a logical `role` on every model call so accounting
  and diagnostics remain meaningful.
- Streaming callers should handle both deltas and final `service_error`.
- Tool execution remains outside this model-service layer; this layer only
  streams model-side tool requests.

## Related Docs

- [channeled-streamer-README.md](channeled-streamer-README.md)
- [streaming-widget-README.md](streaming-widget-README.md)
- [accounting-README.md](../../accounting/accounting-README.md)
- [bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [react-state-machine-README.md](../agents/react/react-state-machine-README.md)
- [claude-code-README.md](../agents/claude/claude-code-README.md)
