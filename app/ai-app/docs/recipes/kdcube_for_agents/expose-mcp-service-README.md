---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/expose-mcp-service-README.md
title: "Expose An MCP Service From A KDCube App"
summary: "Builder recipe for exposing ordinary FastMCP tools from any KDCube app, choosing public, app-owned, or Connection Hub managed authorization, and adding accounting or named-service semantics only when the product needs them."
status: active
tags: ["recipes", "kdcube-for-agents", "mcp", "as-provider", "fastmcp", "governance", "economics"]
updated_at: 2026-07-16
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/consume-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/tracked-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/guard-paid-surface-and-enforce-economics-README.md
---
# Expose An MCP Service From A KDCube App

Use this recipe when an external agent, another KDCube app, or one of your own
agents should call capabilities implemented by a KDCube app through MCP.

In builder-facing language, a KDCube **app** is the deployable unit still named a
**bundle** in current routes, descriptors, and SDK identifiers. This recipe says
"app" except where `bundle_id`, `bundles.yaml`, `@bundle_entrypoint`, or
`/bundles/` is the literal platform contract.

The shortest valid path is deliberately small:

```text
ordinary async domain method
        |
        v
FastMCP tool
        |
        v
one @mcp(...) method on the app entrypoint
        |
        v
/api/integrations/bundles/<tenant>/<project>/<app-id>/.../mcp/<alias>
```

You do **not** need a named-service provider to expose MCP. Named services are
an optional higher-level contract for reusable object realms. A product-specific
tool such as `search_reports`, `run_forecast`, or `create_invoice` can be an
ordinary FastMCP tool.

## 1. Build A Stateless FastMCP App

Keep the MCP surface in a focused module. The tools should call ordinary async
domain services rather than duplicating product logic inside the transport:

```python
# surfaces/mcp/reports.py
from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable

from pydantic import Field


SearchReports = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
GetReport = Callable[[str], Awaitable[dict[str, Any]]]


def build_reports_mcp_app(
    *,
    search_reports: SearchReports,
    get_report: GetReport,
):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    mcp = FastMCP(
        "Acme reports",
        stateless_http=True,
        instructions=(
            "Use search_reports when the report id is unknown. "
            "Use get_report only with an id returned by search_reports."
        ),
    )

    @mcp.tool(
        name="search_reports",
        title="Search reports",
        description="Search reports visible to the current authorized principal.",
        annotations=ToolAnnotations(
            title="Search reports",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=False,
    )
    async def _search_reports(
        query: Annotated[
            str,
            Field(description="Natural-language report search query."),
        ],
        limit: Annotated[
            int,
            Field(ge=1, le=25, description="Maximum results, from 1 through 25."),
        ] = 10,
    ) -> dict[str, Any]:
        rows = await search_reports(str(query or "").strip(), int(limit))
        return {"items": rows, "count": len(rows)}

    @mcp.tool(
        name="get_report",
        title="Read report",
        description="Read one report by its stable report id.",
        annotations=ToolAnnotations(
            title="Read report",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=False,
    )
    async def _get_report(
        report_id: Annotated[
            str,
            Field(description="Stable report id returned by search_reports."),
        ],
    ) -> dict[str, Any]:
        return await get_report(str(report_id or "").strip())

    return mcp
```

`stateless_http=True` is required for the current proc-served app MCP path.
KDCube dispatches each MCP request to an app instance; the next request may run
in another worker or after a restart. Durable state belongs in product storage,
not in a FastMCP session object held in one Python process.

The functions that perform I/O are async. App code runs in a concurrent proc
runtime; blocking network, database, filesystem, or subprocess work on the event
loop delays unrelated requests. Use async clients, or explicitly move an
unavoidable blocking library to a bounded worker thread.

## 2. Declare The MCP Surface On The App

Expose the FastMCP app from the entrypoint:

```python
# entrypoint.py
from kdcube_ai_app.infra.plugin.bundle_loader import mcp

from .surfaces.mcp.reports import build_reports_mcp_app


class ReportingApp(...):
    @mcp(
        alias="reports",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.reports.auth",
    )
    async def reports_mcp(self, request=None, **kwargs):
        del kwargs
        return build_reports_mcp_app(
            search_reports=lambda query, limit: self.reports.search(
                query=query,
                limit=limit,
                request=request,
            ),
            get_report=lambda report_id: self.reports.get(
                report_id=report_id,
                request=request,
            ),
        )
```

The externally reachable URL is:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/reports
```

For `route="operations"`, the route is:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/reports
```

`auth_config` is a pointer into this app's effective descriptor. It does not
hard-code credentials in Python and it does not itself select an auth mode.

## 3. Choose The Authorization Owner

The MCP protocol and authorization policy are separate. Choose one owner for the
boundary and state it in the descriptor.

| Boundary | Descriptor | Who authenticates and authorizes |
| --- | --- | --- |
| Public MCP | omit `auth`, or leave it without a managed/app-owned mode | no credential guard; use only for intentionally public tools |
| App-owned MCP | `auth.mode: bundle` | app code validates its credential and applies product authorization |
| Managed MCP | `auth.mode: managed` | KDCube/Connection Hub validates delegated credentials, resource grants, and selected tools before app code runs |

The route name does not replace auth policy. A `public/mcp/...` URL can still be
protected by `mode: managed`; "public" means the HTTP route is reachable for MCP
discovery and OAuth challenge, not that every tool call is authorized.

### Public surface

```yaml
surfaces:
  as_provider:
    mcp:
      public_catalog:
        auth: {}
```

Use this only for data and operations that are genuinely public. The app must
still bound inputs, result sizes, rates, and expensive work.

### App-owned authentication

```yaml
surfaces:
  as_provider:
    mcp:
      reports:
        auth:
          mode: bundle
```

With `mode: bundle`, the proc bridge does not invent an app credential policy.
The app must validate the request and reject it before returning privileged
tools or data. Keep that policy in one app auth component; do not scatter token
parsing across individual MCP tools.

### Connection Hub managed authentication

```yaml
# Reporting app item in bundles.yaml
surfaces:
  as_provider:
    mcp:
      reports:
        auth:
          mode: managed
          authority_id: delegated_client
          selected_tool_grants: true
```

`mode: managed` makes the proc MCP bridge enforce the delegated-client boundary
before it invokes `reports_mcp`:

```text
MCP request
  -> bearer exists and validates
  -> credential issuer matches authority_id
  -> credential resource matches this concrete MCP URL
  -> requested tool exists in the resource policy
  -> required grants are present
  -> requested tool was selected in this consent
  -> project actor/grantor authority into request context
  -> invoke app FastMCP tool
```

`@mcp` deliberately does not accept `roles=` or `user_types=`. MCP endpoint
policy lives in `auth`/`auth_config`; product-specific record checks may run
inside the domain service after that boundary passes.

## 4. Publish Managed Tool And Grant Policy

The app descriptor selects managed auth. Connection Hub owns the delegable
capabilities and concrete resource/tool catalog used for consent and runtime
enforcement:

```yaml
connections:
  delegated_credentials:
    oauth:
      enabled: true
      capabilities:
        - grant: reports:read
          label: Read reports
          description: Search and read reports through MCP.
          delegable_roles:
            - kdcube:role:registered
            - kdcube:role:paid

      resources:
        - resource: >-
            */api/integrations/bundles/*/*/reporting@1-0/public/mcp/reports*
          tools:
            search_reports:
              label: Search reports
              description: Search reports visible to the approving user.
              grants: [reports:read]
            get_report:
              label: Read report
              description: Read one report visible to the approving user.
              grants: [reports:read]
```

These two owners answer different questions:

```text
reporting app descriptor
  "this MCP endpoint uses delegated_client managed auth"

Connection Hub resource catalog
  "these tools exist; these grants unlock them; these roles may delegate them"
```

OAuth is the client-facing way an MCP client obtains a delegated credential.
It is not the authorization model itself. The stored Connection Hub grant record
is the authority: client actor, approving grantor, concrete resource, grants,
selected tools, identity scope, expiry, and revocation.

## 5. Use The Projected Identity In Product Code

After managed authorization succeeds, the proc bridge projects the accepted
delegation into the normal request context. The external client remains the
actor; the approving KDCube user remains the grantor and platform/economics
subject.

Important projected fields include:

```text
identity_authority.delegate_identity    external client actor
identity_authority.grantor_user_id       approving KDCube user
identity_authority.platform_user_id      product-data subject
identity_authority.economics_user_id     economics subject
identity_authority.grants                grants for this resource
identity_authority.operations            selected/consented tools
identity_authority.identity_scope        approved identity scope
```

Read this context through the SDK rather than accepting a `user_id` supplied by
the tool caller:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
)


def current_authority() -> dict:
    ctx = get_current_request_context()
    user = getattr(ctx, "user", None) if ctx is not None else None
    value = getattr(user, "identity_authority", None)
    return dict(value or {}) if isinstance(value, dict) else {}
```

Product storage still owns record-level authorization. For example,
`get_report` should query reports visible to `platform_user_id`; it should not
trust an arbitrary owner id in the MCP arguments.

## 6. Make Write Tools Safe

MCP clients and networks retry. A write tool must therefore define its retry
contract:

```python
@mcp.tool(
    name="create_report",
    description="Create one report. request_id makes retries idempotent.",
    annotations=ToolAnnotations(
        title="Create report",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def _create_report(request_id: str, title: str) -> dict:
    return await reports.create_once(
        request_id=request_id,
        title=title,
        authority=current_authority(),
    )
```

For destructive tools, set `destructiveHint=True`, require a stable target ref,
and make the product service reject stale revisions where applicable. MCP
annotations inform clients; Connection Hub grants and app domain checks enforce
authority. One does not replace the other.

## 7. Account For Paid MCP Work

MCP connectivity does not automatically create a price or charge a user. Keep
three layers explicit:

```text
tracking      what service usage occurred?
pricing       what did that usage cost?
enforcement   may it run, and who pays?
```

For a paid MCP tool:

1. Instrument the paid service with KDCube accounting, including provider,
   units, and either a supported price-table entry or reported `cost_usd`.
2. Derive the `EconomicsSubject` from the projected `identity_authority`.
3. Run the paid operation inside `EconomicsGuard` so it verifies, reserves,
   executes, and settles under one accountable `scope_id`.

```text
managed MCP credential
  -> delegate actor + approving grantor
  -> projected economics_user_id
  -> EconomicsGuard verify/reserve
  -> tracked service call
  -> actual usage/cost event
  -> settle or release
```

This supports inbound economic control without turning MCP into a billing
protocol. KDCube can enforce costs it knows because the app reported or priced
them. It cannot infer what an arbitrary external service charges.

See [Implement A Self-Tracked Service](../economics/tracked-service-README.md)
and [Guard A Paid Surface](../economics/guard-paid-surface-and-enforce-economics-README.md).

## 8. Return Bounded Results And Files By Reference

MCP tool output enters an agent context. Keep it bounded:

- return structured rows with stable ids/refs;
- paginate searches and cap limits server-side;
- return summaries before full bodies;
- return a signed URL or product object ref for large/binary files;
- do not base64-encode large files into a normal JSON result;
- do not return internal store objects with fields the client cannot use.

If the MCP tool runs within the current KDCube ReAct workspace and deliberately
creates files there, it may use the strict KDCube file declaration described in
[MCP Tool Results And React File Hosting](../../sdk/tools/mcp-README.md#mcp-tool-results-and-react-file-hosting).
A remote MCP client has no ambient ReAct output directory, so product refs or
short-lived download URLs are the general solution.

## 9. Named Services Are Optional

Choose ordinary app-native MCP when your tool names and schemas already describe
the product well:

```text
search_reports(query, limit)
get_report(report_id)
create_forecast(request_id, inputs)
```

Choose a named-service provider when several apps and agents should share a
typed object realm with stable namespace refs and the common discovery/search/
get/upsert/action grammar:

```text
namespace: report
refs: report:item:<id>
operations: object.search, object.get, object.upsert, object.action
```

Named services can be exposed through the generic `kdcube-services@1-0` MCP
bridge, but they are not a prerequisite for MCP. They solve a different problem:
reusable object semantics across tools, UI, scenes, hosting, and agents.

## 10. Connect This Service To A KDCube Agent

Once the endpoint is reachable, register it once under the consuming app's
`surfaces.as_consumer.mcp.services` and attach an allow-listed view under each
agent's `surfaces.as_consumer.agents.<agent_id>.tools`.

```text
provider app: surfaces.as_provider.mcp.reports
        |
        | streamable HTTP
        v
consumer app: surfaces.as_consumer.mcp.services.mcpServers.reports
        |
        +-> agent analyst: search_reports, get_report
        `-> agent operator: search_reports, get_report, create_report
```

Follow [Connect An MCP Service To A KDCube Agent](consume-mcp-service-README.md)
for the exact descriptor and runtime path.

When the exposed surface serves per-user data under Connection Hub managed
authentication, a consuming agent connects with `delegated: true` + `scopes`
instead of a static credential — it then calls as the signed-in user under a
per-agent consent grant. See
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

## 11. Verify The Boundary

1. Load the app and confirm the MCP endpoint appears in its discovered interface.
2. Use an MCP client/inspector to run `initialize`, `tools/list`, and one bounded
   read-only `tools/call` against the concrete URL.
3. For managed auth, call without a bearer and confirm a `401` protected-resource
   challenge points to Connection Hub metadata.
4. Complete consent, then confirm an allowed tool succeeds.
5. Call a tool not selected in consent and confirm the MCP result is an error
   before the tool body executes.
6. Use the same bearer against another app/resource URL and confirm resource
   mismatch is denied.
7. Revoke the Connection Hub grant and confirm the next call fails.
8. For a paid tool, confirm one scope carries admission/reservation, actual
   usage, and settlement for the projected user.
9. Restart or route to another proc worker and confirm stateless MCP calls still
   work.

## Common Failures

| Symptom | Check |
| --- | --- |
| Endpoint is `404` | App is enabled, `@mcp` alias and route match the URL, and provider surface is enabled |
| FastMCP asks for a stale session id | App was not built with `stateless_http=True` |
| Managed tool runs without expected product scope | Domain code ignored projected identity and trusted caller-supplied ids |
| `tool not consented` | Tool was not selected for this Connection Hub grant, or `selected_tool_grants` policy differs from intent |
| `required delegated grant is missing` | Connection Hub resource tool policy requires a grant absent from this credential's resource grants |
| Every tool is visible to every internal agent | Consumer app used `allowed: ["*"]` or attached the server to each agent; narrow per-agent inventories |
| Paid call has no charge | MCP itself is not pricing; instrument the underlying service and run the call under economics enforcement |
| Large result consumes context | Return pagination, object refs, or short-lived file URLs instead of inline binary/base64 |

## Related Documentation

- [Connect An MCP Service To A KDCube Agent](consume-mcp-service-README.md)
- [MCP SDK Integration](../../sdk/tools/mcp-README.md)
- [Protect App MCP With Managed Credentials](../connections/protect-bundle-mcp-with-managed-credentials-README.md)
- [Make A Named Service Agent-Friendly](named-services-mcp-README.md)
- [Implement A Self-Tracked Service](../economics/tracked-service-README.md)
- [Guard A Paid Surface](../economics/guard-paid-surface-and-enforce-economics-README.md)
