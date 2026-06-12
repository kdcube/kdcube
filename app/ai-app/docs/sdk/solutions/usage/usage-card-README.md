---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/usage/usage-card-README.md
title: "Usage Card Widget"
summary: "Compact budget/quota card that reads the economics /me/budget-breakdown route and refreshes on accounting.usage service events. Reusable bundle widget mounted by alias, with a hidden pay-flow surface."
status: draft
tags: ["sdk", "solutions", "usage", "economics", "widget", "bundle", "react", "data-bus"]
updated_at: 2026-06-09
keywords:
  [
    "usage card",
    "economics widget",
    "budget breakdown",
    "quota card",
    "accounting.usage",
    "sdk://infra/economics/ui/widget/usage-card",
    "me/budget-breakdown",
    "bundle widget alias",
    "versatile scene panel",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Usage Card Widget

The usage card is a small, embeddable widget that shows the signed-in user
their current budget posture: short rolling windows, the active reset window,
and any lifetime credit balance. It is a reusable SDK widget — bundles mount
it by alias, the same way the chat widget or the memory widget is mounted.

The widget reads one platform route. It does not implement billing flows. The
pay-flow surface (checkout, subscription, billing portal) is deliberately
omitted because this card is a read-only status panel; bundles that need a
pay surface render it elsewhere.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.infra.economics.ui.widget.usage-card
  package.json            React 18 + Vite 6 widget app
  index.html              Vite entry, mounts /src/main.tsx
  tsconfig.json           Strict TS, ES2020, react-jsx
  vite.config.ts          Honors OUTDIR / VITE_BUILD_DEST_ABSOLUTE_PATH
  src/main.tsx            ReactDOM root mount
  src/App.tsx             Card layout: three usage windows + lifetime credits
  src/styles.css          KDCube tokens; state bars teal -> gold -> red
  src/api/settings.ts     Placeholder substitution + parent CONFIG bridge
  src/api/client.ts       GET /api/economics/me/budget-breakdown
  src/api/types.ts        QuotaBreakdown, BudgetEffectivePolicy, ...
```

The widget is plain React. It has no Socket.IO dependency of its own — refresh
nudges arrive as `postMessage` from the host scene (see below).

## What The Card Shows

Three stacked windows, plus a credits line when the account has any:

| Window | Source field | Purpose |
| --- | --- | --- |
| Last 60 minutes | `breakdown.rolling_60m` | Burst spend; fastest moving |
| Last 24 hours | `breakdown.rolling_24h` | Day-scale spend |
| Active reset window | `breakdown.reset_window` | Plan window (e.g. 30 days) |
| Lifetime credits | `breakdown.lifetime_credits` | Optional balance row |

Each window renders one or more rows with:

- a state pill bar (teal when healthy, gold at >=80%, red at 100%)
- spent / cap text (currency or token counts depending on policy)
- a one-line policy hint when the cap is set by an effective policy

The card itself stays compact (12px base, three sections) so it can live in a
narrow floating panel inside a scene or a sidebar.

### Density modes

The card renders in one of two densities:

| Mode | When | Shows |
| --- | --- | --- |
| Super-compact | Default when summoned with `?view=compact` (or `?compact=1`) | One-line `Plan: <name> · <email>` header, then `Last hour` / `Today` / `This month` (in that order). Each block shows `$ spent / quota` as the headline (colored gold ≥80%, red at the cap) with `tokens spent / quota` alongside, plus when the window resets. No request counts; quota reads `∞` on an unlimited plan. |
| Full | Default standalone, or after the host sends `kdcube-set-view {view:"expanded"}` | The three stacked windows above with per-row pill bars and policy hints. |

A scene host flips between the two with `kdcube-set-view`; the same message the
memory and chat widgets honor. The dollar color logic is shared across both
modes (teal healthy, gold ≥80%, red at 100%).

The super-compact view reports its rendered height to the host via a
`kdcube-usage-resize {height, compact}` message, so a summoned panel fits to
content (mirrors the memory and tasks widgets).

## Architecture

```text
                 +--------------------------------------+
                 |   Platform: /api/economics/me        |
                 |   - GET /budget-breakdown            |
                 +-------------------+------------------+
                                     |
                       (1) HTTPS, credentials: include
                                     |
                                     v
+-------------------------+   +--------------------+
|   Host scene (bundle)   |   |   usage-card       |
|   - iframe mount        |<->|   widget (iframe)  |
|   - profile auth gate   |   |   - App.tsx        |
|   - Socket.IO chat_service|  |   - client.ts      |
+------+------+-----------+   +--------+-----------+
       |      ^                        ^
       |      |  (3) CONFIG_REQUEST    | (4) postMessage
       |      |       / CONFIG_RESPONSE|     kdcube-usage-card-refresh
       |      |  (auth, baseUrl, ids)  |     (debounced nudge)
       |      |                        |
       |      +------------------------+
       |
       | (2) service_event envelope
       |     route: chat_service
       |     env.type === 'accounting.usage'
       v
+------------------+
|  Socket.IO       |
|  (chat data bus) |
+------------------+
```

Numbered legs:

1. **Data fetch.** The widget's `client.ts` calls
   `GET /api/economics/me/budget-breakdown` with `credentials: 'include'`
   and the auth headers from the runtime config it received from the host.
   The response shape is described by `BudgetBreakdownResponse` in
   `src/api/types.ts`.
2. **Usage signal.** When the platform's accounting subsystem records spend,
   `Communicator.service_event(type="accounting.usage", ...)` publishes an
   envelope on the `chat_service` socket route (see `chat/emitters.py`).
3. **Config handshake.** On mount, the widget posts `CONFIG_REQUEST` to its
   parent. The host scene responds with `CONFIG_RESPONSE` carrying the chat
   base URL, tenant, project, bundle id, and authorization material so the
   widget can build its `/api/economics/me/...` URL and headers. This is the
   standard widget contract documented in
   [Bundle Widget Integration](../../bundle/bundle-widget-integration-README.md).
4. **Refresh nudge.** The host scene listens to `chat_service` events,
   filters `env.type === 'accounting.usage'`, debounces the stream, and posts
   a `kdcube-usage-card-refresh` message to the widget iframe. The widget
   debounces again and re-fetches the breakdown.

The card therefore has three refresh paths and only one fetch:

- initial mount (after the CONFIG handshake completes)
- a `kdcube-usage-card-refresh` post from the host
- the explicit refresh button inside the card

## Bundle Integration

The card follows the standard "shared SDK widget mounted by alias" pattern.
Three changes in the bundle entrypoint, no widget code copied:

### 1. Declare the surface

```python
from kdcube_ai_app.apps.chat.sdk.contracts.bundle_decorators import (
    api, ui_widget,
)

@api(alias="usage_card_widget", route="operations",
     **_api_visibility("usage_card_widget"))
@ui_widget(icon={"tailwind": "heroicons-outline:chart-bar",
                 "lucide": "Gauge"},
           alias="usage_card",
           **_widget_visibility("usage_card"))
def usage_card_widget(self, **kwargs):
    del kwargs
    return ["<div>Usage card is served from "
            "sdk://infra/economics/ui/widget/usage-card after build.</div>"]
```

The function body is a placeholder. KDCube serves the real UI from the built
widget directory; the operation only exists so the surface has a declared
alias and an `@api` mount point.

### 2. Point the alias at the SDK widget source

In `configuration_defaults`, under `ui.widgets`, add:

```python
"usage_card": {
    "enabled": True,
    "src_folder": "sdk://infra/economics/ui/widget/usage-card",
    "build_command": (
        "npm install --no-package-lock && "
        "OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
    ),
},
```

The `sdk://` prefix resolves to the SDK package directory shown in the
*Package Surface* tree above. The platform builds the widget into the
runtime's build destination and serves it under the bundle's widget URL.
For the full discovery/build/serve lifecycle, read
[UI Components Lifecycle](../../bundle/ui-components-lifecycle-README.md).

### 3. Set widget visibility

Still in `configuration_defaults`, under `visibility.widget`, add:

```python
"usage_card": {"user_types": []},
```

An empty list means "all signed-in user types may see the card." Restrict
visibility by listing user types explicitly. The card is meaningless for
anonymous sessions because `/me/budget-breakdown` requires a session — gate
the iframe behind a profile fetch on the host side (see *Scene Wiring*).

## Scene Wiring

In the versatile reference bundle the host is `ui/scene`, and the wiring
lives in `ui/scene/src/main.tsx`. The same shape applies to any scene that
embeds the card.

The scene does four things for this widget:

| Responsibility | Where |
| --- | --- |
| Gate the toggle button behind a non-anonymous profile | `fetchProfileUserType` + `userType` state |
| Embed the widget iframe (drag, z-index, floating panel) | `usageFrame`, `usagePanelSize`, `startUsageDrag` |
| Relay `CONFIG_REQUEST` -> `CONFIG_RESPONSE` to the iframe | shared parent-listener block (also serves the chat and memory iframes) |
| Subscribe to `chat_service`, filter `accounting.usage`, debounce, post `kdcube-usage-card-refresh` | `scheduleUsageRefresh` + the Socket.IO `chat_service` subscription |

The widget URL the scene mounts is the bundle's widget URL for the
`usage_card` alias, the same shape as any other `@ui_widget` alias.

## Hidden Pay-flow Surface

`src/api/client.ts` only exposes the GET this card reads. The economics
ingress also serves the pay-flow POSTs (top-up checkout, subscription, the
billing portal), but those are intentionally not surfaced in this widget.
Bundles that need a pay panel should render it next to the card or as a
separate widget, not by extending this one. Keeping the card read-only also
keeps its review surface tight: every additional verb is one more visibility
and rate-limit decision.

## Customization Knobs

The widget today is configurable via runtime config (auth, base URL, tenant,
project, bundle) and not via per-instance props. If you need a variant — for
example, a 7-day window instead of 30-day, or hiding the lifetime row —
the cheapest path is:

- a second `@ui_widget` alias that points at a forked SDK widget source
- or a thin wrapper widget that hosts this iframe and hides sections via
  postMessage

Do not edit `App.tsx` in place if the change is bundle-specific; keep the
shared SDK widget shared. The card's debounce window (host-side ~800 ms,
widget-side ~600 ms) and panel size (360 x 520 in the reference scene) are
scene-level concerns and live in the host code, not the widget.

## Quick Checklist

| Step | Where |
| --- | --- |
| Add `@ui_widget(alias="usage_card", ...)` and an `@api` mount in the bundle entrypoint | `entrypoint.py` |
| Add `ui.widgets.usage_card` with `src_folder: sdk://infra/economics/ui/widget/usage-card` | `configuration_defaults` |
| Add `visibility.widget.usage_card.user_types` | `configuration_defaults` |
| In the host scene: render an iframe at the widget URL, behind a profile gate | scene `main.tsx` |
| In the host scene: subscribe to `chat_service`, filter `accounting.usage`, post `kdcube-usage-card-refresh` to the iframe | scene `main.tsx` |
| Verify `GET /api/economics/me/budget-breakdown` returns 200 for the signed-in session | platform |

Once those are in place the card renders, refreshes on accounting events, and
needs no further bundle code.
