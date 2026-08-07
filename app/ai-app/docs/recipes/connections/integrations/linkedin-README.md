---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/linkedin-README.md
title: "LinkedIn Integration"
summary: "Recipe for configuring a LinkedIn OAuth connector app in Connection Hub, letting KDCube users connect their own LinkedIn accounts, and wiring LinkedIn publishing through delegated-to-KDCube connected accounts, productivity MCP tools, and the linkedin named-service namespace."
status: active
tags: ["recipes", "connections", "connection-hub", "linkedin", "oauth", "connected-accounts", "delegated-to-kdcube", "named-services", "mcp", "capability-catalog", "account-selection"]
updated_at: 2026-08-07
keywords: ["LinkedIn OAuth", "publish LinkedIn post", "LinkedIn named service", "LinkedIn productivity MCP", "account_required", "agent_account_binding_required", "outcome_unknown"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/named-services-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/linkedin/tools.py
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/linkedin/named_service.py
---
# LinkedIn Integration

Use this recipe when KDCube should let a signed-in user connect their own
LinkedIn account, then publish posts and comments on that user's behalf.

This is the **delegated to KDCube** direction:

```text
LinkedIn member
  -> user consents in LinkedIn
  -> Connection Hub stores the connected account credential
  -> KDCube tool resolves that credential for the current platform user
  -> tool calls the LinkedIn REST API with the user's delegated token
```

## Two LinkedIn Layers Exist

The SDK carries two independent LinkedIn integrations. Pick deliberately.

| Layer | Package | OAuth owner | Use for |
| --- | --- | --- | --- |
| Connection Hub (this recipe) | `integrations/linkedin/{rest_api,tools,named_service}.py` | Connection Hub connector app | New work: agent tools, productivity MCP, `linkedin` namespace |
| Bundle-owned (legacy) | `integrations/linkedin/{accounts,settings}.py` | The bundle itself | Existing `task-and-memo-app@1-0` wiring |

They do not share storage, OAuth clients, or callback routes. The layers can run
side by side; a user who connects through both has two independent connections.

## What LinkedIn Allows

LinkedIn's standard OAuth products are write-oriented. Plan the product around
this before designing screens:

| Capability | Available | Notes |
| --- | --- | --- |
| Publish a text post | yes | `w_member_social` |
| Publish a post with images | yes | 1 image inline, 2–20 as a multi-image post |
| Comment on a post | yes, on `/v2` | `w_member_social`; see below |
| Read the connected member's own profile | yes | `openid`, `profile`, `email` |
| Read post content, feeds, reactions | **no** | needs `r_member_social`, restricted to LinkedIn-approved apps |
| Search people, posts or companies | **no** | no such scope on standard products |
| Publish documents (PDF) | **no** | Documents API needs Marketing partner access; render pages to images instead |

The `linkedin` namespace therefore declares `search: false` and its
`object.get` performs no provider read. That is a LinkedIn constraint, not a
KDCube limitation.

## Flow

```text
Operator creates LinkedIn Developer App
  Products: Sign In with LinkedIn using OpenID Connect, Share on LinkedIn
  Redirect URL: Connection Hub delegated-to-KDCube OAuth callback
        |
        v
Operator configures Connection Hub
  provider: linkedin
  connector app: demo
  claims: linkedin:profile, linkedin:post
        |
        v
User opens Connection Hub -> Delegated to KDCube / Connected accounts
  clicks LinkedIn connect, approves the LinkedIn consent screen
        |
        v
Connection Hub callback stores account metadata and credential
        |
        v
Agent/tool execution
  SDK resolver checks user, provider, connector app, and claim
  LinkedIn tool calls /rest/posts with the resolved member token
```

## LinkedIn App Configuration

1. Open <https://developer.linkedin.com/> and create an app associated with a
   LinkedIn Company Page.
2. On **Products**, request **Sign In with LinkedIn using OpenID Connect**
   (grants `openid`, `profile`, `email`).
3. On **Products**, request **Share on LinkedIn** (grants `w_member_social`).
   New apps sometimes wait for review here.
4. On **Auth** → **Authorized redirect URLs**, add the exact Connection Hub
   callback:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

LinkedIn compares redirect URIs byte for byte. If a bundle still runs the
legacy layer, register **both** URLs — the legacy one keeps its own bundle
route (`.../<BUNDLE_ID>/public/linkedin_oauth_callback`). One LinkedIn app can
serve both; the client id and secret are then shared.

If the runtime is behind ngrok, both URLs change with the tunnel host.

5. Copy **Client ID** and **Client Secret** from **Auth**.

## Connection Hub Configuration

`bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: connection-hub@1-0
      config:
        delegated_to_kdcube:
          enabled: true
          oauth:
            public_base_url: "https://<PUBLIC_HOST>"
          providers:
            linkedin:
              label: LinkedIn
              adapter: linkedin.oauth_member
              enabled: true
              connector_apps:
                demo:
                  label: KDCube LinkedIn
                  enabled: true
                  client_id: "<LINKEDIN_OAUTH_CLIENT_ID>"
                  client_secret_ref: connections.delegated_to_kdcube.providers.linkedin.connector_apps.demo.client_secret
                  allowed_claims:
                    - linkedin:profile
                    - linkedin:post
              claims:
                linkedin:profile:
                  label: Read your LinkedIn profile
                  description: Read the approving member's own LinkedIn name and email.
                  provider_scopes:
                    - openid
                    - profile
                    - email
                linkedin:post:
                  label: Post and comment on LinkedIn
                  description: Publish posts and comments on LinkedIn as the approving member.
                  provider_scopes:
                    - w_member_social
```

`bundles.secrets.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: connection-hub@1-0
      secrets:
        connections:
          delegated_to_kdcube:
            oauth_state_secret: "<RANDOM_HEX_32_BYTES>"
            providers:
              linkedin:
                connector_apps:
                  demo:
                    client_secret: "<LINKEDIN_OAUTH_CLIENT_SECRET>"
```

### Why there is no separate comment claim

LinkedIn gates posting **and** commenting on the same `w_member_social` scope.
A separate `linkedin:comment` claim would put two consent checkboxes in front of
one provider scope, so `linkedin:post` covers both.

### Comments use the unversioned endpoint

Posts go to `/rest/posts`; comments go to **`/v2/socialActions`**, without a
`LinkedIn-Version` header. The versioned `/rest/socialActions` refuses a
`w_member_social` token:

```json
{"status": 403,
 "message": "Not enough permissions to access: partnerApiSocialActions.CREATE.<version>"}
```

`partnerApi…` is LinkedIn's marker for a resource behind Community Management
partner access. The documentation is contradictory here — the Posts and
MultiImage pages describe `w_member_social` as covering comments, while the
Comments API page lists `w_member_social_feed` — so treat the 403 above, not the
docs, as the authority. If a deployment obtains partner access, move comments to
`/rest/socialActions` and add the version header.

### Identity scopes are always requested

The adapter adds `openid` and `profile` to every authorization request whatever
the user ticked. Authorship is `urn:li:person:{sub}`, and `sub` reaches KDCube
only at connect time through the id_token or userinfo. A connection granted
`w_member_social` alone would have no author to publish as.

## API Version

Every `/rest` call carries a dated `LinkedIn-Version` header, and LinkedIn
sunsets old versions. The version is a descriptor property on the app that hosts
the tools:

```yaml
config:
  integrations:
    linkedin:
      api_version: "202601"
```

Raise it when LinkedIn retires the configured version. Never hardcode it in
bundle source.

## Tool Configuration

Tools declare the connected-account claims they need. Example main-agent tool
block:

```yaml
- name: linkedin
  kind: python
  module: kdcube_ai_app.apps.chat.sdk.integrations.linkedin.tools
  alias: linkedin
  allowed:
    - list_linkedin_accounts
    - get_linkedin_profile
    - post_linkedin_update
    - post_linkedin_image_update
    - comment_on_linkedin_post
  tool_traits:
    list_linkedin_accounts:
      strategy: [exploration]
    get_linkedin_profile:
      strategy: [exploration]
    post_linkedin_update:
      strategy: [exploitation]
    post_linkedin_image_update:
      strategy: [exploitation]
    comment_on_linkedin_post:
      strategy: [exploitation]
  tool_claims:
    get_linkedin_profile:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: linkedin
              claims: [linkedin:profile]
    post_linkedin_update:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: linkedin
              claims: [linkedin:post]
    post_linkedin_image_update:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: linkedin
              claims: [linkedin:post]
    comment_on_linkedin_post:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: linkedin
              claims: [linkedin:post]
```

`list_linkedin_accounts` declares no claim: it reads KDCube's own connection
records, the same boundary Slack and mail use for account refs.

`post_linkedin_image_update` takes a `conv:fi:` reference or a workspace-relative
path, so it only works inside a turn that has an artifact workspace.

## Productivity MCP Surface

`kdcube-services@1-0` exposes plain MCP tools over the same connected accounts:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/kdcube-services@1-0/public/mcp/productivity
```

| Tool | Grant | Connected-account claim |
| --- | --- | --- |
| `productivity_linkedin_accounts` | `linkedin:profile` | none — see below |
| `productivity_linkedin_profile` | `linkedin:profile` | `linkedin:profile` |
| `productivity_linkedin_post` | `linkedin:post` | `linkedin:post` |
| `productivity_linkedin_post_image` | `linkedin:post` | `linkedin:post` |
| `productivity_linkedin_comment` | `linkedin:post` | `linkedin:post` |

`productivity_linkedin_accounts` reads KDCube's own connection records and
calls no LinkedIn API, so it declares no connected-account claim — the same
position `object.list` takes on the named-service door. Declaring one would
resolve an account before the caller has one to name, and the tool takes no
`account_id` to name it with.

**Known limitation — image publishing depends on the named-service door.**
`productivity_linkedin_post_image` accepts only `staged_refs`, and slots are
minted by `object.action request_upload` on the `linkedin` namespace. A client
granted only the productivity resource can publish text and comments but
cannot publish images. Wiring file hosting into this surface is a tracked
follow-up; until then, route image posts through the named-service door.

Declare the connector app so credential resolution knows which OAuth client
serves the provider:

```yaml
surfaces:
  as_provider:
    mcp:
      productivity:
        connector_apps:
          linkedin: demo
```

## Named-Service MCP Namespace

The same integration is exposed as namespace `linkedin` on the generic
named-services surface:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/kdcube-services@1-0/public/mcp/named_services
```

| Operation | Grants | Behavior |
| --- | --- | --- |
| `provider.about` | `named_services:use` | Namespace intro and schema. |
| `provider.capabilities` | `named_services:use` | Declares `search: false` and what is not supported. |
| `object.schema` | `named_services:use` | Progressive capability catalog: root, `schema_path` branch, `query` search, kind, or exact operation. |
| `object.list` | `named_services:use` | Connected LinkedIn accounts with their author URNs. |
| `object.get` | `named_services:use` | Account record, or a post ref's permalink. |
| `object.action.publish_post` | `named_services:use`, `linkedin:post` | Publish a post with no media. |
| `object.action.publish_image_post` | `named_services:use`, `linkedin:post` | Publish a post carrying images. |
| `object.action.add_comment` | `named_services:use`, `linkedin:post` | Comment on a post ref. |
| `object.action.request_upload` | `named_services:use`, `linkedin:post` | Reserve a signed upload slot for one image. |
| `object.action.discard_upload` | `named_services:use`, `linkedin:post` | Release one staged image. |

Refs:

```text
linkedin:<account_id>
linkedin:<account_id>:post:<post_urn>      e.g. linkedin:acc_1:post:urn:li:share:7123456789
```

The post URN keeps its own colons; the ref parser splits on the first three
segments only.

**The post URN type is not fixed.** LinkedIn returns `urn:li:share:<id>` for a
post with no media or a single image, and `urn:li:ugcPost:<id>` for a
multi-image post (`content.multiImage`). Both are carried verbatim in the ref
and both round-trip through `object.get` and `add_comment` — verified live.
Never assume the `share:` form: a consumer that matches on it will break on
galleries.

Refs round-trip: both publish actions return a post ref, and `add_comment`
accepts exactly that ref. `object.get` on it returns the permalink and states
that post content is unreadable.

`object.get` checks the ref's account against the connection records on both
ref kinds, so a post ref naming an account this user never connected answers
`linkedin_account_not_found`. The post URN itself cannot be checked — LinkedIn
exposes no read for it — so the returned object carries `urn_verified: false`.
Treat the permalink as derived from the ref, not as proof the post exists.

Text and image publishing are separate actions so a delegated grant can allow
one without the other; LinkedIn itself covers both with `w_member_social`.
`publish_post` rejects file-shaped payload keys instead of dropping them.
Both actions require post text: a LinkedIn post always carries commentary.

Images arrive on `publish_image_post` as `payload.files` in one of three
forms. Inside a chat turn, `{file_path}` names a KDCube workspace artifact and
the service reads the bytes. On turn-less transports, `{staged_ref}` follows a
signed upload and `{filename, content_base64, mime}` is a small inline
fallback. One file becomes `content.media`; several become
`content.multiImage`. `alt_texts` is positional, so resolved files keep the
payload order across the three forms.

### Capability Catalog

`object.schema` is progressive rather than one large payload:

```text
linkedin
├── accounts     /accounts/list, /accounts/inspect
├── publishing   /publishing/text, /publishing/images, /publishing/staging
└── engagement   /engagement/posts, /engagement/comment
```

A namespace-only call returns the root; `schema_path` browses one branch;
`query` searches the capability declarations and returns `catalog_path`,
`object_kind`, and `schema_operation`; `schema_operation` expands one exact
contract. Capability search covers this declared catalog only — there is no
index of LinkedIn content, and `object.search` stays unsupported.

Pass one selector at a time. The view is inferred from what you send, so
`schema_operation` together with `query` is refused rather than guessed.

### Choosing the account

Every `linkedin` ref embeds its account id, and how the account is chosen
depends on which tool carries the call:

| Tool | `object_ref` | Account comes from |
| --- | --- | --- |
| `named_services_action` | required | the ref |
| ReAct `object_action` | required | the ref |
| `named_services_call` | optional | the ref, else `payload.account_id` |

So on the generic call tool the account can be left unnamed. What happens then
depends on the caller's own per-account binding, not on how many accounts the
user connected:

- exactly one bound account — it resolves, and nothing is asked;
- two or more — `account_required` with labeled candidates; resend the same
  call with `account_id` set to one of them. An account is never picked
  silently;
- an account the caller is **not** bound to, named explicitly —
  `agent_account_binding_required`, even when that account itself holds the
  claim.

The plain productivity MCP tools pass the same `account_id` into requirement
preflight and into the LinkedIn operation. The selector therefore resolves one
account before any LinkedIn request; preflight cannot approve one account and
let the mutation use another.

An account-binding denial carries a Connection Hub URL focused on this
caller's grant card, the productivity or named-services MCP `resource`, and the
requested LinkedIn account/claim. A hosted UI may render that URL and an
external client may relay it. Nothing opens, grants, or retries automatically.

## User Experience

1. The user signs into KDCube.
2. The user opens Connection Hub → Connections → Delegated to KDCube /
   Connected accounts.
3. LinkedIn appears as an available provider once the descriptor config loads.
4. The user selects claims and starts the connection.
5. KDCube opens LinkedIn OAuth in a new tab; the user approves.
6. LinkedIn redirects back to Connection Hub, which stores the account and
   credential.
7. Agent tools can now publish for that platform user.

## Test The Flow

After descriptor changes, refresh the runtime, connect LinkedIn, then try:

```text
List my connected LinkedIn accounts.
```

```text
Post "hello from KDCube" to LinkedIn.
```

```text
Publish the chart from this turn to LinkedIn with the caption "Q3 revenue".
```

```text
Comment "thanks everyone" on LinkedIn post urn:li:share:7123456789.
```

Expected behavior:

- if LinkedIn is not connected, tools return a managed connected-account error
  the chat UI surfaces as a connect action, and LinkedIn is never called;
- with `linkedin:post`, publishing and commenting work;
- with `linkedin:profile`, profile and account reads work;
- a token that predates `Share on LinkedIn` approval fails with 403 and is
  reported as a scope problem that reconnecting fixes;
- with two accounts bound and none named, the agent lists the accounts and
  asks which to use rather than choosing one;
- resending that operation with one candidate `account_id` makes the preflight
  and the LinkedIn call resolve the same account;
- naming an account the caller is not bound to fails with
  `agent_account_binding_required` before LinkedIn is called;
- a successful create response without a post/comment identifier is reported
  as `linkedin_response_incomplete` with `outcome_unknown: true`; inspect the
  LinkedIn profile or target post before deciding whether to retry. KDCube does
  not replay the mutation automatically, but this signal is not a provider-side
  idempotency guarantee if a client ignores it.

The chart case is the one worth running deliberately: inside a chat turn the
image is a workspace artifact, so it publishes through `payload.files` as
`{file_path}` with no staging step and no bytes in the tool call.

## Troubleshooting

### `redirect_uri` does not match

Register the exact Connection Hub callback URL shown in the LinkedIn error,
including host, tenant, project, bundle id and path.

### 403 `NOT_ENOUGH_PERMISSIONS` or `ACCESS_DENIED` on publish

Check that:

- **Share on LinkedIn** is approved on the Products tab;
- the connected account holds `linkedin:post`;
- the stored token was issued *after* product approval — reconnect otherwise.

### Publishing fails with an empty author

The connected account has no `external_subject`. Reconnect so the adapter can
record the OIDC `sub`; a connection made without `openid` cannot publish.

### 426 or an unknown-version error

`integrations.linkedin.api_version` names a version LinkedIn has sunset. Raise
it to a currently supported `YYYYMM`.

### `agent_account_binding_required` on an account that is clearly connected

The connection is not the problem: the account is connected and holds the
claim, but **this caller** has no per-account binding for it. Per-account
bindings are default-closed, so a caller inherits nothing from the user's
connections. Open the caller's own grant card in Connection Hub → Delegated by
KDCube, tick the claim on that account, and save — the change applies to the
bearer the client already holds, with no re-issue.

`consent.reason` is `agent_account_binding_required`, distinct from
`connect_required`: the connection needs no change.

### The account keeps asking to reconnect

Standard LinkedIn apps receive 60-day access tokens and no refresh token, so
expiry surfaces as `reconnect_required`. Approved apps that receive refresh
tokens are refreshed automatically by the shared adapter with no code change.

### A post or comment reports `linkedin_response_incomplete`

LinkedIn returned a successful status but did not return the identifier KDCube
needs to prove which object was created. A post carries that identifier only in
the `x-restli-id` header, because `/rest/posts` answers with an empty body; a
comment can carry it in the header or as a URN in the body, since comments use
the unversioned `/v2/socialActions` endpoint, and the outcome counts as unknown
only when neither source produced one. The effect may or may not have landed.
The response preserves the real provider status and marks
`outcome_unknown: true`, so KDCube does not automatically replay the mutation.
Inspect the LinkedIn profile for a post or the target post for a comment before
deciding whether to retry. Standard LinkedIn access does not provide KDCube a
content-search API, and an external client can still create a duplicate if it
ignores this warning.

## Storage Boundary

Connection Hub owns the connected-account registry: account metadata as user
properties, credentials as user secrets. LinkedIn tools use the SDK connected
account resolver and never read Connection Hub storage or descriptors directly.
