# Models Gateway

Locally served models behind the platform's `provider: custom` role path. The
gateway accepts the custom-model protocol from `CustomModelClient` and
translates it to a local inference runtime — Ollama first.

```text
agent role {provider: custom}          this gateway (host)         Ollama (host)
  proc container                       :11500/generate             :11434/api/chat
  services.llm.custom.endpoint=http://host.docker.internal:11500/generate
```

One gateway instance serves every model pulled into the Ollama runtime. The
platform client sends the selected role/composer model on each request.
`GATEWAY_MODEL` is only a fallback for direct requests that omit `model`.

## Run

```bash
# 1. Ollama serving + the models you want to offer
ollama serve &
ollama pull qwen3.6:35b
ollama pull mistral:7b-instruct-v0.2-q4_K_M

# 2. The gateway, on the host
cd app/ai-app/src/kdcube-ai-app
GATEWAY_MODEL=qwen3.6:35b GATEWAY_NUM_CTX=65536 \
  uvicorn kdcube_ai_app.apps.models_gateway.app:app --port 11500
```

The context window is sized per call. Ollama's default (32768 tokens)
silently truncates longer prompts from the front — the system instruction
goes first, and agent-platform decision prompts run 40-60K tokens. The
deployment descriptor owns the shared fallback
(`services.llm.custom.num_ctx`) and any exact-model override
(`services.llm.custom.model_overrides.<model-tag>.num_ctx`). The effective value
arrives as `parameters.num_ctx` on each request. `GATEWAY_NUM_CTX` is the
standalone-run fallback when a request carries none. The only truncation
symptom is a `truncating input prompt` WARN in the Ollama server log.

Smoke:

```bash
curl -s localhost:11500/health
curl -sN localhost:11500/generate -H 'Content-Type: application/json' -d '{
  "model": "qwen3.6:35b",
  "inputs": [{"role":"user","content":"Say hi in one word."}],
  "parameters": {"stream": true, "max_new_tokens": 32}
}'
```

## Wire into the local install

Configure the shared endpoint and selectable models in the app's
`bundles.yaml` entry:

```yaml
config:
  services:
    llm:
      custom:
        endpoint: http://host.docker.internal:11500/generate
        num_ctx: 65536
        model_overrides:
          qwen3:8b:
            num_ctx: 40960
          mistral:7b-instruct-v0.2-q4_K_M:
            num_ctx: 32768
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

There is no `model_name` under `services.llm.custom`. The selected
`supported_models[].model` or `role_models.<role>.model` is sent with every
request. `model_overrides` contains only exceptions to the shared settings,
not the selectable-model list. Put `services.llm.custom.api_key` in
`bundles.secrets.yaml` only when the gateway uses `GATEWAY_API_KEY`.
The effective context window is selected in this order: picker-row `num_ctx`,
exact-model service override, shared service fallback.

The role's streaming path (`stream_model_text_tracked` → `provider: custom`
branch → `CustomModelClient.astream`) receives normal `{"delta"}` chunks and
a final event with real token usage mapped from Ollama's eval counts.

## Protocol served

`POST /generate`, optional `Authorization: Bearer $GATEWAY_API_KEY`:

```json
{"model": "qwen3.6:35b",
 "inputs": [{"role": "user", "content": "..."}],
 "parameters": {"stream": true, "temperature": 0.7, "top_p": 0.9, "max_new_tokens": 1024}}
```

- non-stream → `{"id", "response", "model", "usage": {prompt_tokens, completion_tokens, total_tokens}}`
- stream → SSE `data: {"delta": "..."}` per chunk, then
  `data: {"delta": "", "final": true, "usage": {...}}`, then `data: [DONE]`.

Legacy parameters from the historical models-hub protocol (`min_p`,
`skip_cot`, `fabrication_awareness`, `prompt_mode`) are accepted and ignored.
