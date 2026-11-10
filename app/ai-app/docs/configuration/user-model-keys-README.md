---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/user-model-keys-README.md
title: "User Model Keys"
summary: "Per-app opt-in that lets a signed-in user's own model provider key serve their turns: the bundles.yaml switch, the key an app stores, credential precedence, caller-funded economics, and the client-config rule that keeps credentials server-side."
tags: ["configuration", "secrets", "economics", "model-credentials", "descriptor"]
keywords: ["bring your own key", "user model key", "model_credentials.user_keys", "user_model_credential_key", "set_user_secret", "credential precedence", "caller-funded turn", "client config credentials", "resolve_config_request_secrets"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
---
# User Model Keys

An app can let each signed-in user run their turns on their **own** model
provider key. The user stores the key once, in their own secret namespace; from
then on the platform resolves it server-side for every turn that user starts in
that app, and the provider bills the user directly. The platform still applies
the user's quota, still records the turn's cost, and debits no budget.

This serves apps whose users already hold provider accounts — power users,
teams with their own provider contracts, or a free tier that should not draw
on the operator's key — without the app ever handling a key in a request.

## Enable it for an app

The switch is platform-reserved app config in `bundles.yaml`. It is off unless
the entry sets `enabled: true`:

```yaml
bundles:
  items:
    - id: "my.app@1-0"
      config:
        model_credentials:
          user_keys:
            enabled: true
            providers: [anthropic]   # optional; omitted = every model provider
```

| Key | Default | Meaning |
|---|---|---|
| `model_credentials.user_keys.enabled` | `false` | A signed-in user's own key may serve their turns in this app. Only the YAML boolean `true` enables it. |
| `model_credentials.user_keys.providers` | every provider | The providers whose user key is consulted. Each listed provider costs one secret lookup per turn. Provider ids: `openai`, `anthropic`, `google`, `huggingface`, `openrouter`, `custom`. |

An unreadable app config reads as disabled.

## Store a user's key

The app writes the key with the SDK user-secret helper, under the name the
platform reads:

```python
from kdcube_ai_app.apps.chat.sdk.config import delete_user_secret, set_user_secret
from kdcube_ai_app.infra.service_hub.model_credentials import user_model_credential_key

await set_user_secret(user_model_credential_key("anthropic"), api_key)   # services.anthropic.api_key
await delete_user_secret(user_model_credential_key("anthropic"))
```

The key lives at `users.<user>.bundles.<app>.secrets.services.<provider>.api_key`
— the same `services.<provider>.api_key` tail an app secret uses, without the
`platform.` prefix of the platform id. Use `user_model_credential_key(...)`
rather than spelling the name: it is the one place the name is derived, and the
resolver reads exactly that name. The SDK write and delete helpers broadcast the
change, so every process picks up a new or revoked key on its next turn.

## Precedence

For each model credential, `resolve_config_request_secrets` takes the first of:

1. a value already set on the server-built `ConfigRequest`;
2. the caller's own key — only when the app enables `user_keys`, the provider is
   allowed, and the caller is signed in;
3. the app key (`bundles.<app>.secrets.services.<provider>.api_key`);
4. the platform key (`platform.services.<provider>.api_key`).

Anonymous callers have no user key: a fingerprint identifies a browser, not an
account. Each user-key lookup logs one line with the user, the app, the
provider, and whether a key was present — never the value.

The resolver records the source of every field on the built `Config`
(`config.credential_sources`, values `request` / `user` / `bundle` /
`platform`).

## Economics: caller-funded turns

A turn is **caller-funded** when every provider its config can route to — every
role in `role_models` plus the default model — runs on the caller's own key.
`BaseEntrypointWithEconomics.run()` reads that from the config its model clients
were built from:

- the turn is admitted on **quota alone** — wallet, subscription, and project
  budget balances do not gate it, so a user with no credits still runs on their
  own key;
- the rate-limit quota is reserved and committed as usual;
- no money hold is taken and nothing is debited — not the project budget, not a
  subscription budget, not the caller's wallet;
- the turn's cost is still computed and recorded by accounting.

The funding source of such a turn is `caller` (see
[Economics Model](../economics/economic-README.md)). A turn that can reach any
provider on an app or platform key is billed exactly as before, even when some of
its calls use the caller's key. Admin budget bypass keeps precedence.

Costs outside model calls — embeddings and paid tools — are not split by
payer; they are included in the recorded cost and, on a caller-funded turn, are
not debited.

## Singleton apps

A singleton app instance serves every caller with the config it was built from,
so it is always built with app or platform keys; user keys apply to per-turn
app instances only. The loader logs a warning when it builds a singleton for a
turn that resolved a user key.

## Client config never carries a credential

Model credentials are resolved server-side only. Client-supplied config — the
chat message `config`, and `config_request` on the REST operation door — has
every model credential field (`*_api_key` on `ConfigRequest`) removed before the
turn's `ConfigRequest` is built. A dropped field is logged by name with the door
and session; the value is never logged.
