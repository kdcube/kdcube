# Recipe: Scene Surface Command

A surface command lets one widget direct another through the scene host: summon
its window and hand it a payload it applies at runtime. This recipe is the
app-author walkthrough: declare the contract for your component, receive and
ack in your widget, emit from another widget with an ack-wait and a standalone
fallback.

Read [Scene Surface Commands](../../sdk/solutions/scene/scene-surface-commands-README.md)
first for the mechanism reference (envelope, routing, ack shapes). This recipe
shows the three code touchpoints and one shipped end-to-end flow.

## What You Build

```text
scene config     declare: contract id -> component alias + target surfaces
target widget    receive kdcube.surface.command, apply payload, post applied ack
emitter widget   compose command_id + ui_event payload, await ack, else fallback
```

## 1. Declare The Contract For Your Component

Plain-script host (`scene.surfaceCommandContracts` in the scene config):

```json
"connections.hub.open": {
  "alias": "connection_hub",
  "targetSurfaces": ["connection_hub.connections", "connection_hub.settings"],
  "action": "open"
}
```

Package host (`@kdcube/components-react/scene`): put the same surfaces on the
component spec — the scene runtime registers and routes them:

```ts
{
  alias: 'connection_hub',
  bundleId: 'connection-hub@1-0',
  widgetAlias: 'connections_settings',
  targetSurfaces: ['connection_hub.connections', 'connection_hub.settings'],
  gated: true,
  placement: 'floating',
}
```

Declaring is the whole capability story: an emitter learns the scene routes the
contract from the host's ack at command time.

## 2. Receive And Ack In Your Widget

Keep a small module that owns the contract constants, the parser, and the
applied ack (pattern:
`connection-hub@1-0/ui/widgets/connections/src/api/surfaceCommand.ts`):

```ts
export function parseMyOpen(data: unknown): MyOpenCommand | null {
  const raw = data as Record<string, unknown>
  if (raw?.type !== 'kdcube.surface.command') return null
  const target = String(raw.target_surface || '').toLowerCase()
  if (!MY_TARGET_SURFACES.includes(target)) return null
  // payload rides in ui_event; accept top-level keys for raw-relay hosts
  const payload = (raw.ui_event && typeof raw.ui_event === 'object' ? raw.ui_event : raw) as Record<string, unknown>
  return { targetSurface: target, commandId: String(raw.command_id || ''), /* …payload fields… */ }
}
```

In the app shell: listen on `window` `message`, parse, apply the SAME state
your URL deep-link path produces (switch tab, focus the card, preselect
values), then post the applied ack
(`{type: 'kdcube.surface.command.ack', target_surface, action, reason: 'applied', ts}`;
echo `command_id` when present). Applying at runtime keeps the widget's state —
a summon re-targets it with zero reload.

## 3. Emit From Another Widget

Pattern (`chat/ui/widget/src/host.ts`, `postConnectionsCommandAndAwaitAck`):

```ts
const commandId = `myflow_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
window.parent.postMessage({
  type: 'kdcube.surface.command',
  target_surface: 'connection_hub.connections',
  action: 'open',
  command_id: commandId,
  widget: 'chat',
  source: 'consent-card',
  ui_event: { tab: 'provider_connections', provider: 'slack', tiers: ['read', 'write'] },
}, '*')
// await {type:'kdcube.surface.command.ack', command_id, ok} for ~600 ms
// ok:true  -> the scene owns the open
// timeout / ok:false -> window.open(<served widget URL with the same params>)
```

The fallback URL carries the same intent as `ui_event` (query parameters of
the served widget), so both paths land on the same state.

## Worked Example: Consent → Connection Hub

Shipped on main; a chat consent banner lands the user on the hub's provider
card with the requested access tiers preselected.

```text
tool needs slack access
  chat renders consent banner ("Connect account")
        |  click
        v
chat widget maps the consent's claims to the provider's tier ids
  slack:search -> read     slack:post -> write     slack:files:* -> files
  (map: npm/packages/components-core/src/chat/connectionsConsent.ts)
        |
        v
emit connections.hub.open with command_id
  ui_event { tab: provider_connections, provider, tiers, account_id }
  (chat/ui/widget/src/host.ts)
        |
        v
scene routes the contract, summons/focuses the hub window, acks {command_id, ok}
        |
        v
hub widget applies at runtime: switches tab, scrolls the provider card into
view, preselects the tiers, seeds reconnect mode when account_id names an
existing account; posts the applied ack
  (connection-hub@1-0/ui/widgets/connections/src/api/surfaceCommand.ts + App.tsx)

unacked (standalone / non-routing host)
  -> window.open(served hub URL: ?tab=provider_connections&provider=…&tiers=…&account_id=…)
```

Shipped in three commits: `af02826b` (kdcube-ai-app — hub receiver + workspace
groundwork), `bd762da` (website — contract declaration, routing ack,
summon/focus), `2fb7ade1` (kdcube-ai-app — chat consent emit + workspace scene
contract).

Source anchors (under `app/ai-app/src/kdcube-ai-app/`):

- `kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/ui/widgets/connections/src/api/surfaceCommand.ts` — receiver + applied ack
- `kdcube_ai_app/apps/chat/sdk/solutions/chat/ui/widget/src/host.ts` — emitter, ack-wait, fallback
- `npm/packages/components-core/src/chat/connectionsConsent.ts` — claim → tier map
- `kdcube_ai_app/apps/chat/sdk/examples/bundles/workspace@2026-03-31-13-36/ui/scene/src/sceneConfig.ts` — package-host contract declaration

## Related Docs

- [Scene Surface Commands](../../sdk/solutions/scene/scene-surface-commands-README.md)
- [Scene](scene-README.md)
- [Generic Scene Contract](../../sdk/solutions/scene/generic-scene-contract-README.md)
- [Scene Surface Registry](../../sdk/solutions/scene/scene-surface-registry-README.md)
- [Cross-Surface Context Drag](../../sdk/solutions/scene/cross-surface-context-drag-README.md)
