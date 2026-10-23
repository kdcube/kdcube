---
id: repo:kdcube-ai-app/app/ai-app/docs/service/secrets/manage-a-secret-with-connection-hub-cli-README.md
title: "KDCube Admin: Manage A Secret With The Connection Hub CLI"
summary: "Step-by-step path for a KDCube super-admin to set, inspect, read and delete one deployment or application secret from a fresh machine with the Connection Hub CLI, including the consent page and the common refusals."
tags: ["service", "secrets", "cli", "connection-hub", "operations", "how-to"]
keywords: ["connection-hub secrets host", "set a secret", "secret consent", "resource_not_consented", "oauth_issuer_mismatch", "card grant", "once", "every time", "super-admin"]
updated_at: 2026-10-23
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
---
# KDCube Admin: Manage A Secret With The Connection Hub CLI

This page is the whole path for one person: a KDCube super-admin who wants to
set, inspect, read or delete one platform or application secret on a KDCube
deployment, from any machine, with no bearer token to copy around. A user's own
secret works the same way with `--scope user --user-id <user id>` (plus
`--bundle-id` for a user secret of one application). The
Connection Hub CLI holds the session; Connection Hub asks you, in the browser,
before the first use of each secret.

The protocol, the resource names and the plain `kdcube secrets` commands are
in [Manage KDCube Secrets](secret-management-cli-README.md).

## Who Can Do This

Only a **KDCube super-admin** (a platform administrator of the deployment).
Secret management is an admin-only resource in Connection Hub: the consent
page and the Card editor offer it only to a platform administrator, and a
Card never carries more than the person who approved it holds.

## Before You Start

- **The deployment's public address.** Use the HTTPS address the deployment's
  OAuth issuer is configured for, not `localhost` or a local workdir (see
  `oauth_issuer_mismatch` below).
- **The deployment registers the secret resources.** In the deployment's
  Connection Hub descriptor, the `kdcube-management` service under
  `connections.delegated_credentials.admission.services` lists
  `urn:kdcube:management:secret:*:*:*:*:*`. The Connection Hub descriptor
  template has it by default.
- **Versions.** The consent flow below needs a KDCube platform at release
  2026.10.23.1100 or later and a Connection Hub that includes the W929 fixes
  (consent for one secret). The troubleshooting section names what an older
  deployment shows.
- **`uv`** on the machine you work from.

## 1. Install The CLI

From PyPI, once `connection-hub-cli` 2026.10.23.1100 or later is published
(it pulls `kdcube-cli` 2026.10.23.1100 or later, which understands the secret
consent):

```bash
uv tool install connection-hub-cli
connection-hub --version
```

Until then, or to match a deployment exactly, run the CLI from source at the
App Ecosystem and KDCube revisions your deployment runs. Both packages come
from source: the secret consent is handled in `kdcube-cli`, and the
`kdcube-cli` on PyPI before 2026.10.23.1100 does not have it.

```bash
CH() { uvx \
  --from "git+https://github.com/elenaviter/app-ecosystem@<app-ecosystem commit>#subdirectory=products/connection-hub/packages/connection-hub-cli" \
  --with "git+https://github.com/kdcube/kdcube@<kdcube commit>#subdirectory=app/ai-app/src/kdcube-ai-app/kdcube_cli" \
  connection-hub "$@"; }
CH --version
```

Every `connection-hub` command below works the same as `CH`.

## 2. Select The Deployment

```bash
connection-hub setup --endpoint https://<public address>
```

## 3. Authorize

```bash
connection-hub host authorize
```

Your browser opens the deployment's login. Sign in as the super-admin and
approve. The CLI now holds a Card for the deployment resource only, stored in
your platform's credential store. Secrets are added to that Card one at a
time, the first time you use each one.

## 4. Set A Secret

Pass the value on standard input, so it never appears on the command line or
in your shell history. An application secret names its scope and application:

```bash
openssl rand -base64 48 | tr -d '\n' | connection-hub secrets host set <key> \
  --scope bundle --bundle-id <application id> --value-stdin
```

A platform secret uses `--scope platform` and a key that starts with
`platform.` (for example `platform.services.brave.api_key`). Without
`--value-stdin` the CLI asks for the value with a hidden prompt.

The first request for a secret your Card does not hold yet answers with a
consent. The CLI opens Connection Hub's consent page in your browser and
waits, on your terminal, for you to press Enter.

## 5. Approve On The Consent Page

The page names the agent, the operation (for example
`kdcube.management.secret.value.write`) and the exact secret, for example
`Application connection-hub@1-0: connections.signing_key`. Do only this:

1. Choose **Once** (this request only) or **Every time** (this operation on
   this secret from now on).
2. Press the button that then reads **Allow `<operation>` once** (or
   **Allow `<operation>` always** after choosing **Every time**).

Nothing else on the page needs to change. Then go back to the terminal and
press Enter. The CLI retries the identical request once and prints a receipt
without the value.

Each operation is approved separately: `set`, `metadata`, `get` and `delete`
on the same secret each ask once, unless you chose **Every time** for that
operation. Use this consent for the CLI's Card: saving a secret on that Card
in Connection Hub's Card editor is currently refused
(`oauth_client_resource_unreachable`, see Troubleshooting).

**Agents and automation** pass `--no-open --no-wait`. The CLI then returns the
consent as `recovery.authorization_url` instead of waiting at a prompt; the
agent gives that address to the person, and after approval reruns the same
command with the same `--invocation-id`.

## 6. Check, Read Or Delete

```bash
connection-hub secrets host metadata <key> --scope bundle --bundle-id <application id>
connection-hub secrets host get <key> --scope bundle --bundle-id <application id> \
  --output ./secret-value
connection-hub secrets host delete <key> --scope bundle --bundle-id <application id>
```

`metadata` reports whether the secret exists and which provider holds it,
never the value. `get` requires `--output` and writes the value to a new
private file (`0600` on POSIX), never to the terminal; an existing file is
replaced only with `--replace`. `delete` reports
whether a value existed.

## Troubleshooting

| You see | Meaning | What to do |
| --- | --- | --- |
| `oauth_issuer_mismatch` at `host authorize` | The selected address is not the one the deployment's OAuth issuer is configured for, typically `localhost` or a local workdir on a deployment with a public issuer. | Run `connection-hub setup --endpoint https://<public address>` and authorize again. |
| `service_resource_not_registered` | The deployment's Connection Hub does not register the secret resources for the management service. | A deployment operator adds `urn:kdcube:management:secret:*:*:*:*:*` to the `kdcube-management` service's resources and reloads Connection Hub. |
| `forbidden` with `delegated credential resource mismatch` | The Card does not hold the secret, and the deployment's Connection Hub predates the secret consent, so no consent was offered. | Update and reload Connection Hub, then rerun. |
| `oauth_client_resource_unreachable` when saving the CLI's Card in the Card editor | The CLI's Card is an OAuth Card entered through the deployment resource; the editor cannot add a secret to it yet. | Use the consent from the CLI instead (steps 4 and 5). |
| `resource_not_consented` | Expected on the first use of a secret: the Card does not hold it yet. The answer carries the consent. | Approve on the consent page (step 5). With `--no-open --no-wait`, open `recovery.authorization_url`, approve, and rerun with the same `--invocation-id`. |
| `management_recovery_invalid` | The CLI refused the consent it was given, usually because the installed `kdcube-cli` is older than 2026.10.23.1100 and does not know the secret consent. | `uv tool upgrade connection-hub-cli` (or reinstall at 2026.10.23.1100 or later) and rerun. |
| `invalid_grant` with `card_resource_not_covered` | The CLI's session could not be refreshed. On a platform before 2026.10.23.1100, every session made by `host authorize` failed at its first refresh this way. It also happens when the CLI's Card was revoked or no longer holds the deployment resource. | Update the platform; meanwhile run `connection-hub host authorize` again. |
| `delegated_access_unknown_resources` when you press Allow | The deployment's Connection Hub predates the fix that keeps the exact secret on the Card (it tried to save the whole secret row). | Update and reload Connection Hub, then rerun the same command. |
| Consent page stays on "Loading this capability" | The deployment's Connection Hub predates the consent page fix for one concrete secret. | Update and reload Connection Hub, then open the consent again (rerun the command). |
| The CLI waits forever after printing the consent | It is running without a person at the terminal (an agent or a pseudo-terminal). | Use `--no-open --no-wait` and pass the consent address to the person. |
