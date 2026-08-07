---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/provider-error-contract-README.md
title: "Provider Error And Observability Contract"
summary: "The required error, retry, logging, and partial-result behavior for KDCube integrations that call external providers."
status: active
tags: ["sdk", "integrations", "provider-errors", "observability", "oauth", "retries", "idempotency"]
updated_at: 2026-07-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/resolve-connected-credential-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/google/google-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/connected_accounts.py
---
# Provider Error And Observability Contract

Use this contract whenever a KDCube integration calls an external API such as
Google, Slack, or a private OAuth service. A provider failure must remain useful
after it crosses a tool, named-service, REST, or MCP boundary. It must also be
safe to show to a user or agent.

The integration owns this translation at the provider boundary. Surfaces relay
the resulting managed envelope; they should not reconstruct provider meaning.

## Keep authorization and provider failures separate

There are three different failure points:

```text
Connection Hub cannot resolve the user's account or claim
  -> return its consent envelope

Provider rejects an expired, revoked, or insufficiently scoped credential
  -> report connected_account_auth_failure
  -> refresh once; require reconnect if it still fails

Provider service, request, policy, quota, or transport fails
  -> return a managed provider error with safe diagnostics
```

A generic HTTP `403` is not automatically an expired credential. Inspect the
provider's reason. A disabled API, missing project entitlement, resource policy
denial, and insufficient OAuth scope need different remedies.

## Client error envelope

Return the provider's safe message and enough structured detail for the caller
to decide what to do next:

```json
{
  "ok": false,
  "error": {
    "code": "google_sheets_provider_configuration_error",
    "message": "Google Sheets API has not been used in this project or is disabled.",
    "where": "google_sheets.create_spreadsheet",
    "managed": true
  },
  "ret": {
    "provider": "google",
    "operation": "create_spreadsheet",
    "stage": "open_created_spreadsheet",
    "provider_status": 403,
    "provider_code": "PERMISSION_DENIED",
    "provider_reason": "SERVICE_DISABLED",
    "retryable": false,
    "outcome_unknown": false,
    "partial_result": {
      "spreadsheet_id": "...",
      "web_url": "..."
    }
  }
}
```

The exact `error.code` may be provider-specific. The fields in `ret` keep the
cross-integration meaning stable:

| Field | Meaning |
| --- | --- |
| `provider` | Stable provider id, such as `google` or `slack`. |
| `operation` | Provider operation that was attempted. |
| `stage` | Failed stage when one logical operation has several side effects. |
| `provider_status` | HTTP status, or `0` only when no response was received. |
| `provider_code` | Provider's safe machine code, when present. |
| `provider_reason` | Provider's specific reason, such as `SERVICE_DISABLED`. |
| `retryable` | Whether retry may succeed without a config or consent change. |
| `outcome_unknown` | A mutating request may have reached the provider, so blind replay could duplicate work. |
| `partial_result` | Stable ids or URLs already created before a later stage failed. |

Never replace an available provider reason with a generic `status: 0`. Never
return a traceback, bearer token, refresh token, client secret, raw request
headers, or an unredacted provider response body to the caller.

## Failure classes

| Failure | Required handling |
| --- | --- |
| Account or claim is missing | Relay the Connection Hub consent envelope. Do not call the provider. |
| Token is expired, revoked, or invalid | Return `connected_account_auth_failure`; refresh once, then direct the user to reconnect. |
| OAuth scope is insufficient | Preserve the provider reason and direct the user to reconnect with the required claim. |
| Provider API is disabled or not provisioned | Return a deployment-configuration error. Name the service when the provider safely supplies it. |
| Provider denies this resource/action | Return access denied. Do not misclassify every `403` as a token-refresh problem. |
| Provider rate-limits the call | Preserve `429` and `Retry-After`; mark retryable. |
| Provider returns `5xx` | Mark unavailable and retryable. For mutations, assess whether the outcome is unknown. |
| Timeout/connection failure | Set `provider_status: 0`, mark retryable, and set `outcome_unknown` for a mutation that may have been sent. |
| Provider accepts a mutation but omits its required created-object identifier | Return an incomplete-response error with the real provider status and `outcome_unknown: true`; inspect/search before retrying. |
| Local validation fails | Return a validation error with `outcome_unknown: false`; no provider call was made. |

## Server logging

Log every provider failure once at the integration boundary with:

- provider, operation, and stage;
- normalized code and provider status/code/reason;
- retryability and `outcome_unknown`;
- stable partial-result identifiers;
- exception type and traceback for transport/runtime exceptions.

Redact credentials and authorization headers before logging. A surface may add
request correlation fields, but it should not log the same provider failure as
a second unrelated error.

## Mutations and retries

Reads can normally be retried when `retryable` is true. Mutations need stronger
rules:

1. Validate before the first provider side effect.
2. Record the stage before each side effect.
3. Preserve any provider id or URL already returned.
4. Set `outcome_unknown` when a timeout or provider failure leaves the outcome
   ambiguous, including a successful response that omits the identifier needed
   to prove which object was created.
5. Do not automatically replay an ambiguous mutation. Inspect by stable id or
   idempotency key first.

An idempotency key is only a guarantee when the provider actually enforces it.
Returning a caller-provided key as correlation data does not make an operation
exactly once.

## New-integration checklist

Before releasing a provider integration:

1. Document every external API or product that must be enabled in the provider
   project/account. OAuth connectivity alone does not prove API activation.
2. Document required OAuth scopes and which KDCube claim maps to each scope.
3. Implement the failure classes above at the shared provider client boundary,
   so MCP, REST, tools, and named services receive the same semantics.
4. Verify no credential or raw sensitive response reaches logs, tool output,
   timeline events, or model context.
5. Test at least: missing consent, expired token, insufficient scope, disabled
   API, resource denial, rate limit, provider `5xx`, transport timeout, and a
   partial or ambiguous mutation.
