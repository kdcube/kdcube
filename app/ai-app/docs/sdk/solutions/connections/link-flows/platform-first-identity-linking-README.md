---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/platform-first-identity-linking-README.md
title: "Platform-First Identity Linking"
summary: "Connection Hub link flow where the user starts with an authenticated KDCube platform session and then proves an external provider identity."
status: active
tags: ["sdk", "connections", "connection-hub", "identity-linking", "platform-session"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/channel-first-identity-linking-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-links/identity-links-README.md
---
# Platform-First Identity Linking

Platform-first identity linking starts from a normal KDCube browser/platform
session.

```text
KDCube user is already signed in
  -> user asks to connect an external identity
  -> Connection Hub creates a short-lived challenge for platform_user_id
  -> provider proof surface completes the challenge
  -> Connection Hub writes provider:<subject> -> platform_user_id
```

## Roundtrip

```text
1. KDCube browser widget
     authenticated platform session
          |
          v
2. Connection Hub
     identity_link_challenge_create(provider=telegram)
     stores:
       challenge_id
       platform_user_id=<current user>
       status=pending
       expires_at
          |
          v
3. Provider proof surface
     Telegram Mini App / OAuth provider / signed verifier
     sends:
       challenge_id
       provider proof
          |
          v
4. Connection Hub authenticator module
     validates provider proof
     extracts provider_subject
          |
          v
5. Connection Hub identity-link store
     writes:
       provider:<provider_subject> -> platform_user_id
```

## Data Sources

| Data | Source |
| --- | --- |
| Platform user id | KDCube browser session |
| Challenge id | Connection Hub challenge store |
| Provider proof | Provider proof surface |
| Verifier secret | Bundle secrets / secrets service |
| Link row | Connection Hub identity-link store |

## Difference From Channel-First

```text
Platform-first:
  platform proof is known before provider proof

Channel-first:
  provider proof is known before platform proof
```

Both flows produce the same identity link. They differ only in which side is
proven first and how the second proof is collected.
