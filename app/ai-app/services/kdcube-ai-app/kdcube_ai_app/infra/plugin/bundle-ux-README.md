# Bundle UX (Widgets + Operations)

This doc describes how an AI bundle can expose custom UI widgets (TSX/HTML) and route widget actions back to the bundle via integrations operations.

## 1) Exposing a widget from a bundle

Bundles can expose a widget by implementing an entrypoint method that returns a list of HTML strings. The SDK will embed the widget in an iframe on the client.

Example pattern (see `kdcube_ai_app/apps/chat/api/integrations/AIBundleDashboard.tsx` for a widget):
- Resolve bundle root from `ai_bundle_spec`.
- Load a TSX/HTML asset from bundle resources.
- Use `ClientSideTSXTranspiler` to compile TSX into HTML.

```python
from kdcube_ai_app.apps.chat.sdk.viz.tsx_transpiler import ClientSideTSXTranspiler

class MyEntrypoint(BaseEntrypoint):
    def price_model(self, user_id: Optional[str] = None, **kwargs):
        bundle_root = self._bundle_root()
        if not bundle_root:
            return ["<p>No price model.</p>"]

        dashboard_path = self.configuration.get("subsystems").get("price-model").get("dashboard")
        with open(os.path.join(bundle_root, dashboard_path), "r", encoding="utf-8") as f:
            content = f.read()
        html = ClientSideTSXTranspiler().tsx_to_html(content, title="Price Model")
        return [html]
```

## 2) Widget config in bundle configuration

Widgets are typically declared in the bundle configuration to map a subsystem key to a TSX asset:

```python
@property
def configuration(self):
    return {
        "subsystems": {
            "ai-bundles": {"dashboard": "service/integrations/AIBundleDashboard.tsx"},
            "price-model": {"dashboard": "service/price/PriceModel.tsx"},
        }
    }
```

This allows the entrypoint to locate widget resources relative to the bundle root.

## 3) Auth + backend calls from widgets

Widgets use a configuration handshake (see `ConversationBrowser.tsx` and `AIBundleDashboard.tsx`) to receive:
- `baseUrl`
- `accessToken` / `idToken`
- `idTokenHeader`
- default tenant/project

From the widget, build API URLs as:

```
${baseUrl}/api/...
```

and attach auth headers. This ensures cookies or tokens are applied correctly in the iframe.

## 4) Bundle operations endpoint (loop-back)

Widgets can call bundle-defined operations via the integrations endpoint:

```
POST /api/integrations/bundles/{tenant}/{project}/operations/{op}
```

The `{op}` is a method name on the bundle entrypoint (e.g., `suggestions`, `price_model`, or any custom op). The SDK resolves the bundle and calls the operation with the user context.

This allows UI → backend → bundle round-trips without exposing a separate service.

## 5) Reading bundle props from cache

Bundles can store UI config or parameters in bundle props. The admin UI writes props to Redis (KV cache), and the bundle reads them at runtime. Use `bundle_props_defaults` + `bundle_props` on `BaseEntrypoint` to read merged defaults/overrides.

See: `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`.
