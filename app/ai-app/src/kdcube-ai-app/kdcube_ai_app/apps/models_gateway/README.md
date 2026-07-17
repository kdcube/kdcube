# Models Gateway

Locally served models behind the platform's `provider: custom` role path,
with zero changes to platform modules. The gateway accepts the custom-model
protocol that `CustomModelClient` already speaks and translates it to a local
inference runtime — Ollama first.

```text
agent role {provider: custom}          this gateway (host)         Ollama (host)
  proc container                       :11500/generate             :11434/api/chat
  CUSTOM_MODEL_ENDPOINT=http://host.docker.internal:11500/generate
```

One gateway instance serves one model (`GATEWAY_MODEL`); the platform client
does not transmit a model name. Run a second instance on another port for a
second model.

## Run

```bash
# 1. Ollama serving + a model
ollama serve &
ollama pull qwen3.6:35b        # M3 Max/48GB default; qwen3.6:27b = dense alt

# 2. The gateway, on the host
cd app/ai-app/src/kdcube-ai-app
GATEWAY_MODEL=qwen3.6:35b GATEWAY_NUM_CTX=65536 \
  uvicorn kdcube_ai_app.apps.models_gateway.app:app --port 11500
```

The context window is sized per call. Ollama's default (32768 tokens)
silently truncates longer prompts from the front — the system instruction
goes first, and agent-platform decision prompts run 40-60K tokens. The
deployment descriptor owns the value (`services.llm.custom.num_ctx`,
arriving as `parameters.num_ctx` on each request); `GATEWAY_NUM_CTX` is the
standalone-run fallback when a request carries none. Set it above your
largest prompt; the only truncation symptom is a `truncating input prompt`
WARN in the Ollama server log.

Smoke:

```bash
curl -s localhost:11500/health
curl -sN localhost:11500/generate -H 'Content-Type: application/json' -d '{
  "inputs": [{"role":"user","content":"Say hi in one word."}],
  "parameters": {"stream": true, "max_new_tokens": 32}
}'
```

## Wire into the local install

Proc containers must see the custom endpoint (deployment env, not code):

```bash
CUSTOM_MODEL_ENDPOINT=http://host.docker.internal:11500/generate
CUSTOM_MODEL_NAME=qwen3.6:35b          # display/accounting label
# CUSTOM_MODEL_API_KEY=...             # only if GATEWAY_API_KEY is set
```

Then map any agent role to the local model in the app's `bundles.yaml`
config:

```yaml
role_models:
  <role>:
    provider: custom
    model: qwen3.6:35b
```

The role's streaming path (`stream_model_text_tracked` → `provider: custom`
branch → `CustomModelClient.astream`) receives normal `{"delta"}` chunks and
a final event with real token usage mapped from Ollama's eval counts.

## Protocol served

`POST /generate`, optional `Authorization: Bearer $GATEWAY_API_KEY`:

```json
{"inputs": [{"role": "user", "content": "..."}],
 "parameters": {"stream": true, "temperature": 0.7, "top_p": 0.9, "max_new_tokens": 1024}}
```

- non-stream → `{"id", "response", "model", "usage": {prompt_tokens, completion_tokens, total_tokens}}`
- stream → SSE `data: {"delta": "..."}` per chunk, then
  `data: {"delta": "", "final": true, "usage": {...}}`, then `data: [DONE]`.

Legacy parameters from the historical models-hub protocol (`min_p`,
`skip_cot`, `fabrication_awareness`, `prompt_mode`) are accepted and ignored.
