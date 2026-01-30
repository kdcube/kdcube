# MCP Demo

This folder contains small runnable examples for MCP client usage.

## 1) stdio (on‑demand server)

The MCP client spawns the web_search server process locally.

Steps:
1. Export env:
```
export MCP_SERVICES='{
  "mcpServers": {
    "web_search": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server", "--transport", "stdio"],
      "env": {
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "DEFAULT_LLM_MODEL_ID": "o3-mini",
        "ROLE_MODELS_JSON": "",
        "REDIS_URL": "",
        "TENANT_ID": "",
        "DEFAULT_PROJECT_NAME": "",
        "WEB_SEARCH_CACHE_TTL_SECONDS": "3600"
      }
    }
  }
}'
```

2. Run the demo:
```
python kdcube_ai_app/apps/chat/sdk/runtime/mcp/demo/mcp_with_stdio.py
```

## 2) HTTP/SSE (remote server)

Run the server separately:
```
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server --transport sse --port 8787
```

Then export env:
```
export MCP_SERVICES='{
  "mcpServers": {
    "web_search": { "transport": "sse", "url": "http://127.0.0.1:8787/sse" }
  }
}'
```

Run the demo:
```
python kdcube_ai_app/apps/chat/sdk/runtime/mcp/demo/mcp_with_http_stream.py
```

## Notes

- For stdio, you can omit the `env` block to inherit the parent process env.
- For HTTP/SSE, the server is responsible for its own env configuration.
