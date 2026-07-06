---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
title: "Slack Integration"
summary: "Recipe for configuring a Slack OAuth connector app in Connection Hub, letting KDCube users connect their own Slack accounts, and wiring Slack search/post tools through delegated-to-KDCube connected accounts."
status: active
tags: ["recipes", "connections", "connection-hub", "slack", "oauth", "connected-accounts", "delegated-to-kdcube"]
updated_at: 2026-07-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/integrations/slack.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/integrations/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/slack/tools.py
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/connected_accounts.py
---
# Slack Integration

Use this recipe when KDCube should let a signed-in user connect their own Slack
account or workspace, then let KDCube tools search Slack or post to Slack on
that user's behalf.

This is the **delegated to KDCube** direction:

```text
Slack user/workspace
  -> user consents in Slack
  -> Connection Hub stores the connected account credential
  -> KDCube tool resolves that credential for the current platform user
  -> tool calls Slack API with the user's delegated Slack token
```

This is different from delegated automation access. Delegated automation access
creates a KDCube bearer token for an automation entering KDCube. Slack
integration stores a provider credential that lets KDCube enter Slack for the
current user.

## Flow

```text
Operator creates Slack app
  User Token Scopes:
    search:read, chat:write
    channels:read/groups:read/im:read/mpim:read
    channels:history/groups:history/im:history/mpim:history
    files:read/files:write
    search:read.* for Slack assistant search
  Redirect URL: KDCube delegated-to-KDCube OAuth callback
        |
        v
Operator configures Connection Hub
  provider: slack
  connector app: demo
  claims: slack:search, slack:post, slack:channels, slack:history,
          slack:files:read, slack:files:write, slack:assistant:search
        |
        v
Application tool declares required connected-account claims
  search_slack -> slack:search
  post_slack_message -> slack:post
  list_slack_channels -> slack:channels
  read_slack_channel_history -> slack:history
  download_slack_file -> slack:files:read
  upload_slack_file -> slack:files:write
  slack_assistant_search_info/slack_assistant_search -> slack:assistant:search
        |
        v
User opens Connection Hub -> Delegated to KDCube / Connected accounts
  clicks Slack connect
  approves Slack OAuth consent
        |
        v
Connection Hub callback stores:
  account metadata in user properties
  credential in user secrets
        |
        v
Agent/tool execution
  SDK resolver checks current user, provider, connector app, and claim
  Slack tool calls Slack API with the resolved user token
```

## Slack App Configuration

Create one Slack app to act as the OAuth connector app. Each KDCube user can
then connect their own Slack account or workspace through that app.

1. Open <https://api.slack.com/apps>.
2. Choose **Create New App** -> **From scratch**.
3. Pick a development workspace for the app.
4. Open **OAuth & Permissions**.
5. Under **User Token Scopes**, add:

```text
search:read
chat:write
channels:read
groups:read
im:read
mpim:read
channels:history
groups:history
im:history
mpim:history
files:read
files:write
search:read.public
search:read.private
search:read.im
search:read.mpim
search:read.files
search:read.users
```

Use **User Token Scopes**, not bot scopes. KDCube stores
`authed_user.access_token`, because these tools act as the connected Slack user.

The current KDCube `search_slack` tool calls Slack's classic
`search.messages` method, which requires the generic `search:read` user-token
scope. The newer `slack_assistant_search` tool calls
`assistant.search.context`, so it maps to Slack's split `search:read.*` scopes
instead.

6. In **OAuth & Permissions** -> **Redirect URLs**, add the exact KDCube
   callback URL:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

For a local demo-project runtime, use the current public host for that runtime:

```text
https://<LOCAL_PUBLIC_HOST>/api/integrations/bundles/demo-tenant/demo-project/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

For the custom-authority test project, add this too:

```text
https://<LOCAL_PUBLIC_HOST>/api/integrations/bundles/demo-tenant/custom-authority/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

For the deployed demo runtime:

```text
https://demo.kdcube.tech/api/integrations/bundles/demo/demo/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

For the deployed dev/staging runtime:

```text
https://dev.kdcube.tech/api/integrations/bundles/demo/demo-march/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

Slack compares redirect URIs exactly. The public host, tenant, project, bundle
id, route, and callback path must all match the URL KDCube sends in the OAuth
request. If ngrok changes, add the new callback URL to the Slack app.

7. Open **Basic Information** and copy **Client ID** and **Client Secret**.
8. If users outside the development workspace must connect, open **Manage
   Distribution** and activate public distribution. Workspace app approval may
   still be required in the user's Slack workspace.

## Workspace Admin Approval Flow

Creating the Slack app is only the connector-app setup. For KDCube to see a
particular Slack workspace, that workspace must authorize this Slack app through
Slack OAuth.

```text
KDCube operator creates one Slack connector app
  |
  | app has Client ID, Client Secret, redirect URLs, user scopes
  v
Slack app is distributed or listed
  |
  v
User clicks Connect Slack in KDCube
  |
  v
Slack opens the workspace authorization screen
  |
  +-- workspace permits user installs
  |     -> user approves
  |
  +-- workspace requires admin approval
        -> user requests approval / sends install link to admin
        -> admin reviews requested scopes and approves/install app
```

What to share with a Slack admin:

- the Slack app name shown in Slack, for example `KDCube`;
- the Slack app installation/authorization URL from **Manage Distribution** or
  the **Add to Slack** link;
- the requested user-token scopes from this recipe, including `search:read`,
  `chat:write`, conversation read/history scopes, `files:read`, `files:write`,
  and the split `search:read.*` scopes for Slack assistant search;
- the KDCube callback host/domain that receives OAuth redirects;
- a short explanation that KDCube stores per-user/per-workspace delegated
  credentials and uses them only for approved KDCube claims such as
  `slack:search`, `slack:post`, `slack:history`, `slack:files:read`,
  `slack:files:write`, and `slack:assistant:search`.

Do not share the Slack client secret with workspace users or admins. The client
secret stays in KDCube secrets.

If the Slack app is not distributed, other workspaces may reject authorization
with Slack's `invalid_team_for_non_distributed_app` error. For pilot customers,
use Slack's unlisted public distribution. For broad commercial use, publish
through the Slack Marketplace.

One approved workspace connection becomes one KDCube connected account:

```text
Slack user + Slack workspace/team -> KDCube connected account
```

The same KDCube user can connect multiple Slack workspaces, but each workspace
must authorize the Slack app separately. Slack may default the OAuth page to the
workspace currently active in the browser. If the user needs another workspace,
they should switch/sign in to that workspace before starting OAuth, or use
Slack's workspace chooser when Slack shows it.

## Slack OAuth Page Options

These are the recommended choices for the current KDCube Slack connected-account
adapter.

| Slack page section | What to do | Why |
| --- | --- | --- |
| Advanced token security via token rotation | Leave **Opt In** off for now. | The current adapter stores the returned user access token. Token rotation requires refresh-token handling and credential rotation support. |
| Proof Key for Code Exchange (PKCE) | Leave **Opt In** off for now. | KDCube currently performs a server-side OAuth code exchange using the Slack client secret. Enable PKCE only after the adapter supports `code_verifier`. |
| OAuth Tokens / Install to workspace | Not required for the normal KDCube user-connect flow. You may install to your own workspace only for Slack-side testing. | KDCube starts OAuth from Connection Hub when each user connects their account. |
| Redirect URLs | Add every exact KDCube callback URL for each host/tenant/project that will run this Slack app. | Slack rejects OAuth when the request `redirect_uri` is not registered. |
| Bot Token Scopes | Not needed by the current KDCube Slack tools. | The adapter stores and uses Slack user tokens, not bot tokens. |
| User Token Scopes | Add the scopes listed above. | These are the Slack scopes mapped by KDCube claims such as `slack:search`, `slack:post`, `slack:history`, `slack:files:read`, `slack:files:write`, and `slack:assistant:search`. |

The old callback path `connection_oauth_callback` is not the current
delegated-to-KDCube connected-account callback. The current callback is:

```text
delegated_to_kdcube_oauth_callback
```

Keep old redirect URLs only if another legacy flow still needs them. For the
Slack connected-account flow in this recipe, add the delegated-to-KDCube URLs
listed above.

Useful Slack references:

- <https://docs.slack.dev/authentication/installing-with-oauth/>
- <https://docs.slack.dev/reference/methods/chat.postMessage/>
- <https://docs.slack.dev/reference/methods/conversations.list/>
- <https://docs.slack.dev/reference/methods/conversations.history/>
- <https://docs.slack.dev/reference/methods/files.info/>
- <https://docs.slack.dev/reference/methods/files.getUploadURLExternal/>
- <https://docs.slack.dev/reference/methods/files.completeUploadExternal/>
- <https://docs.slack.dev/reference/methods/assistant.search.info/>
- <https://docs.slack.dev/reference/methods/assistant.search.context/>
- <https://docs.slack.dev/reference/scopes/search.read/>
- <https://docs.slack.dev/reference/scopes/chat.write/>

## Connection Hub Configuration

Configure the Slack provider and connector app under the Connection Hub bundle.
The `public_base_url` must be the public URL that Slack can call back.

`bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: connection-hub@1-0
      config:
        connections:
          delegated_to_kdcube:
            enabled: true
            oauth:
              public_base_url: "https://<PUBLIC_HOST>"
            providers:
              slack:
                label: Slack
                adapter: slack.oauth_user_token
                enabled: true
                connector_apps:
                  demo:
                    label: KDCube Slack
                    enabled: true
                    client_id: "<SLACK_CLIENT_ID>"
                    client_secret_ref: connections.delegated_to_kdcube.providers.slack.connector_apps.demo.client_secret
                    allowed_claims:
                      - slack:search
                      - slack:post
                      - slack:channels
                      - slack:history
                      - slack:files:read
                      - slack:files:write
                      - slack:assistant:search
                claims:
                  slack:search:
                    label: Search Slack
                    description: Search Slack content visible to the approving user.
                    provider_scopes:
                      - search:read
                  slack:post:
                    label: Post to Slack
                    description: Post messages into Slack destinations allowed by the approving user.
                    provider_scopes:
                      - chat:write
                  slack:channels:
                    label: List Slack channels
                    description: List Slack conversations visible to the approving user.
                    provider_scopes:
                      - channels:read
                      - groups:read
                      - im:read
                      - mpim:read
                  slack:history:
                    label: Read Slack history
                    description: Read Slack conversation history visible to the approving user.
                    provider_scopes:
                      - channels:history
                      - groups:history
                      - im:history
                      - mpim:history
                  slack:files:read:
                    label: Read Slack files
                    description: Read and download Slack files visible to the approving user.
                    provider_scopes:
                      - files:read
                  slack:files:write:
                    label: Upload Slack files
                    description: Upload files to Slack destinations allowed by the approving user.
                    provider_scopes:
                      - files:write
                  slack:assistant:search:
                    label: Use Slack assistant search
                    description: Use Slack AI semantic search where available.
                    provider_scopes:
                      - search:read.public
                      - search:read.private
                      - search:read.im
                      - search:read.mpim
                      - search:read.files
                      - search:read.users
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
              slack:
                connector_apps:
                  demo:
                    client_secret: "<SLACK_CLIENT_SECRET>"
```

The Slack app's registered User Token Scopes are the provider-side universe.
KDCube claims are the Connection Hub consent units. The selected claims must
map to a subset of the Slack scopes registered on the Slack app.

`oauth_state_secret` signs the OAuth state for delegated-to-KDCube provider
connect flows. Generate it once per environment and keep it in secrets:

```bash
openssl rand -hex 32
```

## Tool Configuration

Tools declare the connected-account claims they need. The tool does not read
Slack secrets directly and does not know where the credential is stored.

Example main-agent tool block:

```yaml
- name: slack
  kind: python
  module: kdcube_ai_app.apps.chat.sdk.integrations.slack.tools
  alias: slack
  allowed:
    - search_slack
    - list_slack_channels
    - read_slack_channel_history
    - download_slack_file
    - upload_slack_file
    - slack_assistant_search_info
    - slack_assistant_search
    - post_slack_message
  tool_traits:
    search_slack:
      strategy:
        - exploration
    list_slack_channels:
      strategy:
        - exploration
    read_slack_channel_history:
      strategy:
        - exploration
    download_slack_file:
      strategy:
        - exploration
    upload_slack_file:
      strategy:
        - exploitation
    slack_assistant_search_info:
      strategy:
        - exploration
    slack_assistant_search:
      strategy:
        - exploration
    post_slack_message:
      strategy:
        - exploitation
  tool_claims:
    search_slack:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:search
    list_slack_channels:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:channels
    read_slack_channel_history:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:history
    download_slack_file:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:files:read
    upload_slack_file:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:files:write
    slack_assistant_search_info:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:assistant:search
    slack_assistant_search:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:assistant:search
    post_slack_message:
      connections:
        delegated_to_kdcube:
          connected_accounts:
            - provider_id: slack
              connector_app_id: demo
              claims:
                - slack:post
```

`provider_id` must match the provider under
`connections.delegated_to_kdcube.providers`. `connector_app_id` must match the
entry under `connector_apps`. In the snippets above, that is `slack` and
`demo`.

## User Experience

1. The user signs into KDCube.
2. The user opens Connection Hub -> Connections -> Delegated to KDCube /
   Connected accounts.
3. Slack appears as an available provider if the descriptor config is loaded.
4. The user starts Slack connection and selects the claims to approve, such as
   Search Slack or Post to Slack.
5. KDCube opens Slack OAuth in a new tab.
6. The user approves the Slack consent screen.
7. Slack redirects back to Connection Hub.
8. Connection Hub stores the connected account metadata and credential for that
   KDCube platform user.
9. The connected Slack account appears in the widget.
10. Agent tools can now resolve the account when the same platform user asks to
    search or post through Slack.

If the user has several Slack workspaces or accounts, they can connect several
accounts through the same connector app. Each connected account should carry
its own workspace/team id, external subject, claims, and credential reference.
Tools accept an optional `account_id` when the user needs to choose a specific
connected Slack account.

## Test The Flow

After descriptor changes, refresh the runtime and open the Connection Hub
widget. Connect Slack first, then test from an agent that has the Slack tools.

Prompts that should reach the tools:

```text
Search Slack for "KDCube" and show the top 5 matches.
```

```text
List the Slack channels I can access.
```

```text
Read the last 20 messages from Slack channel C1234567890 and summarize files mentioned there.
```

```text
Download Slack file F1234567890 into this conversation workspace.
```

```text
Upload the report artifact to Slack channel C1234567890 with the comment "Draft report".
```

```text
Use Slack assistant search to find recent context about customer onboarding, including files.
```

```text
Post "hello from KDCube" to Slack channel <channel-id>.
```

Expected behavior:

- if Slack is not connected, the tool returns a managed connected-account error
  that the chat UI can surface as a consent/connect action;
- if Slack is connected with `slack:search`, `search_slack` can search;
- if Slack is connected with `slack:post`, `post_slack_message` can post;
- if Slack is connected with `slack:channels`, `list_slack_channels` can list
  visible conversations;
- if Slack is connected with `slack:history`, `read_slack_channel_history` can
  read visible conversation history;
- if Slack is connected with `slack:files:read`, `download_slack_file` can
  materialize visible Slack files as KDCube artifacts;
- if Slack is connected with `slack:files:write`, `upload_slack_file` can upload
  KDCube artifacts to Slack;
- if Slack is connected with `slack:assistant:search`,
  `slack_assistant_search_info` and `slack_assistant_search` can use Slack's AI
  semantic search where Slack enables it for the workspace;
- if the account lacks the needed claim, the user must reconnect or upgrade the
  connected account with that claim.

## Troubleshooting

### Slack Says `redirect_uri did not match any configured URIs`

Add the exact callback URL shown in the Slack error to **OAuth & Permissions**
-> **Redirect URLs** in the Slack app.

For example, this error:

```text
redirect_uri did not match any configured URIs. Passed URI:
https://<LOCAL_PUBLIC_HOST>/api/integrations/bundles/demo-tenant/demo-project/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

means this exact URL must be registered in Slack. Do not register only the host
or only the bundle prefix.

### Slack Search Fails

Check that:

- `search:read` is registered as a Slack **User Token Scope**;
- the KDCube claim `slack:search` maps to those provider scopes;
- the connected account was approved with `slack:search`;
- the user token can see the Slack content being searched.

### Slack Post Fails

Check that:

- `chat:write` is registered as a Slack **User Token Scope**;
- the KDCube claim `slack:post` maps to `chat:write`;
- the connected account was approved with `slack:post`;
- the channel id is valid and the user can post there.

### Slack Channel Listing Or History Fails

Check that:

- `channels:read`, `groups:read`, `im:read`, and `mpim:read` are registered
  when using `list_slack_channels`;
- `channels:history`, `groups:history`, `im:history`, and `mpim:history` are
  registered when using `read_slack_channel_history`;
- the connected account was approved with `slack:channels` or `slack:history`;
- the connected Slack user can see the channel/conversation being read.

### Slack File Download Or Upload Fails

Check that:

- `files:read` is registered and approved for `download_slack_file`;
- `files:write` is registered and approved for `upload_slack_file`;
- downloaded files are visible to the connected Slack user;
- uploaded files are KDCube artifact paths, not arbitrary local filesystem paths;
- Slack workspace policy permits file upload.

### Slack Assistant Search Fails

Check that:

- the Slack workspace supports assistant search;
- `slack_assistant_search_info` returns `is_ai_search_enabled: true`;
- the Slack app has the split `search:read.*` scopes required by
  `assistant.search.context`;
- the connected account was approved with `slack:assistant:search`.

### Provider Appears But OAuth Does Not Start

Check that:

- `connections.delegated_to_kdcube.enabled` is true;
- the Slack provider is enabled;
- the connector app is enabled;
- `client_id` is set in `bundles.yaml`;
- the matching `client_secret` is set in `bundles.secrets.yaml`;
- the runtime was refreshed after descriptor changes.

### Tool Still Says Slack Is Not Connected

Check that the tool claim block references the same provider and connector app:

```yaml
provider_id: slack
connector_app_id: demo
```

Then check that the connected account was created for the same KDCube platform
user who is running the agent.

## Storage Boundary

Connection Hub owns the connected-account registry. Account metadata is stored
as user properties and credentials are stored as user secrets using the platform
property/secret lifecycle. Application tools should use the SDK connected
account resolver and should not read Connection Hub storage or descriptors
directly.
