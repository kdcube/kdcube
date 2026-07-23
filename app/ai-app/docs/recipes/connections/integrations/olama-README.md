---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/olama-README.md
title: "Ollama Integration (Locally Served Models)"
summary: "Recipe for serving a locally hosted model (Ollama) to KDCube agents through the models gateway: prerequisites, the custom-provider connector protocol, platform streaming and accounting, thinking-model handling, multimodal input, and descriptor wiring."
status: active
tags: ["recipes", "integrations", "local-models", "ollama", "custom-provider", "streaming", "models-gateway", "multimodal"]
updated_at: 2026-07-21
see_also:
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/models_gateway/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-stream-events-README.md
---
# Ollama Integration (Locally Served Models)

Serve a model running on your own machine — via Ollama — as a selectable
brain for KDCube agents: the react agent's decision head, JSON-producing
agent heads, and content-generation tools all stream from it through the
platform's normal model-service path.

The integration is descriptor-only for the app: the platform's existing
`provider: custom` role path talks to a small standalone **models gateway**
that translates to Ollama, and `services.llm.custom` is a reserved
platform-interpreted app prop — the platform applies it exactly like
`role_models`. Turning the feature on for any app means editing
`bundles.yaml`, zero app code.

```text
agent role / composer pick {provider: custom}
  ModelRouter._mk_custom → CustomModelClient          platform (unchanged)
    POST {endpoint}/generate  {"inputs","parameters"}   custom protocol
      models gateway (host, :11500)                    apps/models_gateway
        POST :11434/api/chat  {"model","messages",...}  Ollama
```

## 1. Prerequisites

- **Ollama** on the host that runs the local KDCube install.
  - macOS: the Ollama app (menu-bar; starts the server on `:11434`,
    auto-starts at login, self-updates). macOS 14+.
  - Any OS: `ollama serve` for a foreground/headless server.
  - Verify: `curl -s localhost:11434/api/version`.
- **A model that fits the machine.** Guidance for a 36–48 GB Apple Silicon
  workstation:

  | Model | Shape | Size | Why |
  | --- | --- | --- | --- |
  | `qwen3.6:35b` | MoE, ~3B active/token | 24 GB | fast interactive brain; 256K ctx; vision + tools |
  | `qwen3.6:27b` | dense | 17 GB | highest local coding quality; slower tokens/s |
  | `qwen3:0.6b` | tiny | 0.5 GB | pipeline smoke tests only |

  ```bash
  ollama pull qwen3.6:35b
  ```

- **The models gateway**, run on the host (containers reach it via
  `host.docker.internal`):

  ```bash
  cd app/ai-app/src/kdcube-ai-app
  GATEWAY_MODEL=qwen3.6:35b \
    python3 -m uvicorn kdcube_ai_app.apps.models_gateway.app:app --port 11500
  ```

## 2. How the connector is implemented

### 2.1 The platform side: `provider: custom`

`ModelRouter` (`infra/service_hub/inventory.py`) resolves each role's
`{provider, model}`; `provider: custom` constructs a `CustomModelClient`
bound to the configured endpoint. That client speaks a small HTTP protocol:

- request — `POST {endpoint}` with

  ```json
  {"model": "qwen3.6:35b",
   "inputs": [{"role": "system|user|assistant", "content": "... or blocks"}],
   "parameters": {"max_new_tokens": 1024, "temperature": 0.7, "top_p": 0.9,
                   "stream": true}}
  ```

  plus optional `Authorization: Bearer <key>`. The model comes from the
  selected role or composer option; the endpoint is shared by every custom
  model configured for the app.
- non-stream response — `{"response": text, "usage": {...}, "id": ...}`;
- stream — SSE `data: {"delta": "..."}` per chunk, one final event
  `data: {"final": true, "usage": {...}}`, then `data: [DONE]`.

### 2.2 The gateway: protocol translation

`kdcube_ai_app/apps/models_gateway/app.py` (~250 lines, FastAPI) accepts
that protocol on `/generate` and forwards to Ollama `/api/chat`:

| Custom protocol | Ollama |
| --- | --- |
| `inputs[].role/content` | `messages[].role/content` (+ `images`, §5) |
| `parameters.max_new_tokens` | `options.num_predict` |
| `parameters.temperature` / `top_p` | `options.temperature` / `top_p` |
| `parameters.stream` | `stream` |
| top-level `model` (gateway env `GATEWAY_MODEL` only when absent) | `model` |
| — (gateway env `GATEWAY_KEEP_ALIVE`, default 30m) | `keep_alive` |
| — (gateway env `GATEWAY_THINK`, default off) | `think` (§4) |
| `parameters.num_ctx` (descriptor `services.llm.custom.num_ctx`; gateway env `GATEWAY_NUM_CTX` as standalone fallback) | `options.num_ctx` |

**Size the context window to your agent prompts — this is load-bearing.**
Ollama's runner defaults to a 32768-token window (0.24) and SILENTLY
truncates longer prompts from the FRONT — exactly where the system
instruction lives. An agent-platform decision prompt easily exceeds that
(the react instruction alone is ~40K tokens; with a tool catalog and admin
customization ~60K), so the model receives the tail of its prompt, loses
the channel protocol entirely, and answers as plain text that the platform
cannot route. The only symptom is a
`msg="truncating input prompt" limit=... prompt=...` WARN line in the
Ollama server log. Set the shared fallback `num_ctx` in the app's
`services.llm.custom` block above your largest prompt plus generation room.
Override it under
`services.llm.custom.model_overrides.<exact-model-tag>.num_ctx` when one model
has a smaller supported context. The platform sends the effective value per request,
and the gateway prefers it over `GATEWAY_NUM_CTX`, which is only the
standalone-run fallback. Watch that log line when in doubt.

Coming back, Ollama's JSONL chunks (`{"message":{"content": piece}}`) become
SSE `{"delta": piece}` events, and the terminal chunk's
`prompt_eval_count`/`eval_count` become
`usage.{prompt_tokens, completion_tokens, total_tokens}` on the final event.
Legacy parameters from the historical models-hub protocol (`min_p`,
`skip_cot`, `fabrication_awareness`, `prompt_mode`) are accepted and
ignored.

One gateway instance serves every model already pulled into its Ollama
runtime. Each platform request names the selected model; `GATEWAY_MODEL` is
used only by direct requests that omit it.

Optional auth: set `GATEWAY_API_KEY` on the gateway and put the same value
in the app's secrets (`services.llm.custom.api_key`, §6).

## 3. How the platform streams from it

`ModelServiceBase.stream_model_text` (and `stream_model_text_tracked`, which
adds accounting and thought-group handling) dispatches per client type. For
`CustomModelClient` it consumes `client.astream(...)` directly; the chunk
contract at that seam is:

```text
{"delta": str}                                    zero or more
{"event": "final", "usage": {...}, "model_name"}  exactly once
```

The final usage is normalized into the platform accounting shape
(`input_tokens`, `output_tokens`, cache fields zeroed), so economics rows
appear for `provider=custom` like for any hosted provider — with the token
counts Ollama actually measured.

Everything that rides `stream_model_text_tracked` therefore works with the
local model with no additional wiring:

- the react v3 decision head (channeled workspace streamer) — the local
  model's deltas drive the same channel protocol as hosted models;
- JSON-producing agent heads (the composite JSON artifact streamers);
- content tools (`generate_content_llm`-style backends).

Structured output: custom clients have no server-side JSON mode; agents rely
on prompt-driven JSON plus the platform's format-fixer pass. In practice
strong local models follow the channel/JSON conventions; watch the
format-fixer engagement rate when evaluating a model.

## 4. Thinking models

Qwen3-family (and other hybrid reasoners) emit their reasoning into a
separate `thinking` field in Ollama's response — NOT into content. Two
consequences:

- with thinking enabled, a short `max_new_tokens` can be consumed entirely
  by thought, returning a syntactically valid but EMPTY completion — no
  error anywhere, the platform just sees an empty answer;
- the platform's custom protocol carries a single delta channel, and the
  platform's channel streamers drive their own output structure through
  prompts — interleaved model-native thinking would corrupt that framing.

The gateway therefore requests `think: false` by default. `GATEWAY_THINK=1`
re-enables model-native thinking for raw experiments (thinking text is
dropped, never streamed). If a future wave wants visible local-model
thinking, the seam is the gateway: map Ollama's `thinking` chunks onto the
platform's thinking-delta events — a gateway + streaming-contract decision,
not an Ollama limitation.

## 5. Multimodal input

Qwen3.6 has vision, and the chain supports it end to end. Image blocks
produced by the platform's modal message helpers pass through
`CustomModelClient` untouched; the gateway splits each message's content
into text plus base64 images and forwards Ollama's per-message `images`:

- Anthropic shape: `{"type": "image", "source": {"type": "base64",
  "media_type": ..., "data": ...}}`
- OpenAI shape: `{"type": "image_url", "image_url": {"url":
  "data:image/png;base64,..."}}`

Verified: image + "what single color dominates?" answers correctly, with
image tokens counted in `prompt_tokens`.

Constraint worth knowing: Qwen3-VL's image processor requires ≥32px per
side; Ollama (observed on 0.24) panics its model runner on smaller images
instead of rejecting them. Real attachments never hit this; synthetic
test pixels can.

## 6. Descriptor wiring (no env vars)

All configuration is descriptor-driven. Two blocks in the app's entry in the
runtime `bundles.yaml` (workspace app shown; any app with agents can carry
the same):

```yaml
config:
  # 1) where provider "custom" routes — applied by the app onto its Config
  services:
    llm:
      custom:
        endpoint: http://host.docker.internal:11500/generate
        num_ctx: 65536                 # shared serving-window fallback
        model_overrides:
          qwen3:8b:
            num_ctx: 40960             # installed model's context limit
          mistral:7b-instruct-v0.2-q4_K_M:
            num_ctx: 32768             # exact-tag override
  # 2) offer it as a per-conversation composer pick (overrides role_models
  #    for the picking user only — nobody's defaults change)
  react:
    default_agent:
      supported_models:
        - model: qwen3.6:35b
          provider: custom
          label: Qwen3.6 35B (local)
          num_ctx: 65536
        - model: qwen3:8b
          provider: custom
          label: Qwen3 8B (local, fast)
          num_ctx: 40960
        - model: mistral:7b-instruct-v0.2-q4_K_M
          provider: custom
          label: Mistral 7B Instruct v0.2 (local)
          num_ctx: 32768
```

There is deliberately no descriptor-level `model_name`. The selected
`supported_models[].model` (or `role_models.<role>.model`) is the source of
truth and is sent to the shared gateway on every call. The optional
`model_overrides` map contains exceptions to the shared serving settings; it
is not another model allowlist. `num_ctx` is the active context window in
tokens: instructions, tool definitions, conversation/input, and generated
continuation share it. Runtime precedence is the selected picker row's
`num_ctx`, then the matching `model_overrides` value, then the shared custom
service `num_ctx`. The saved user preference contains only `provider` and
`model`; the runtime re-reads the current admin-configured row each turn.

Mistral 7B Instruct v0.2 has a 32K context window. In the workspace ReAct
agent, pair it with the **Extra Lite (local models)** instruction profile;
the full profile can exceed that context before user content is added. The
reference Extra Lite profile is single-action, omits the skill gallery, uses a
compact effective-tool catalog, and removes exec/web/rendering plus Workspace
memory/canvas teaching when the matching capability is disabled.

Secret (only when the gateway sets `GATEWAY_API_KEY`) — in
`bundles.secrets.yaml`:

```yaml
secrets:
  services:
    llm:
      custom:
        api_key: replace-in-real-deployment
```

That is the whole app-side integration. `services.llm.custom` is a reserved
platform-interpreted prop: on every turn door the platform runs
`refresh_bundle_props` (code `configuration_defaults()` deep-merged with the
Redis-staged descriptor props) and applies the reserved paths —
`role_models`, `embedding`, and this one — onto the per-instance `Config`,
rebuilding the model service when they changed. The model router then reads
`config.custom_model_endpoint` lazily at client creation, inside the turn.
The gateway key travels the secrets path, not props: the turn door resolves
`services.llm.custom.api_key` (bundle secret first, platform fallback) into
the per-turn config, the same way per-app OpenAI/Anthropic key overrides
resolve.

An app that needs to apply its OWN config-affecting props does it in the
public template hook `on_apply_props(props)` — see
[Bundle Properties And Secrets Lifecycle](../../../sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md).

Alternative to the composer pick: pin a role —
`role_models: {<role>: {provider: custom, model: qwen3.6:35b}}`.

Activation: reload the app (kdcube CLI bundle reload), like any descriptor
config change.

## 7. Verify

```text
1. curl -s localhost:11434/api/version         Ollama up
2. curl -s localhost:11500/health              gateway up; serving model named
3. curl -sN localhost:11500/generate \
     -H 'Content-Type: application/json' -d '{
       "model": "qwen3.6:35b",
       "inputs": [{"role":"user","content":"Say hi in one word."}],
       "parameters": {"stream": true, "max_new_tokens": 32}}'
                                               deltas, final usage, [DONE]
4. chat composer → pick the local model →      react turn streams; economics
   ask a coding question                       rows appear for provider=custom
```

## 8. Current limits (recorded, not hidden)

- A selected model must already be pulled in the Ollama runtime reached by the
  gateway; an unknown tag fails the request rather than silently switching.
- Per-model descriptor overrides currently cover `num_ctx`. Other generation
  parameters remain request/tool policy rather than deployment model metadata.
- Text + image input; no local embeddings through this path yet (the
  platform's custom-embeddings support is a separate seam).
- Structured output is prompt-driven (no server-side JSON schema mode).
- Prompt caching does not apply; cache token fields are always zero for
  `provider=custom` accounting rows.
