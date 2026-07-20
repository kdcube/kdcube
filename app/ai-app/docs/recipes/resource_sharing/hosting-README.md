---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/resource_sharing/hosting-README.md
title: "Move Files In And Out Over MCP (Hosting Room)"
summary: "Exact calls for an external agent to attach files to mail, upload files to Slack, and pull provider attachments/files out — signed upload slots and download URLs, bytes always over plain HTTP, never inside tool calls."
status: active
tags: ["recipes", "resource-sharing", "hosting", "upload", "download", "mcp", "mail", "slack", "staged-ref", "signed-url"]
updated_at: 2026-07-07
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/hosting/hosting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/named-services-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
---
# Move Files In And Out Over MCP (Hosting Room)

Use this recipe when an external agent connected to the generic
`named_services` MCP surface needs real file bytes to cross the boundary:
attach a file to an outgoing email, upload a file to Slack, or fetch a mail
attachment / Slack file. Bytes travel over plain HTTP against signed,
short-lived URLs; tool calls carry only JSON.

Architecture, module map, and bundle wiring live in the
[hosting solution doc](../../sdk/solutions/hosting/hosting-README.md) — this
page is the call sequence.

## Send a file INTO KDCube (attach to mail, upload to Slack)

Step 1 — reserve a slot (per file, single-use):

```text
named_services_action namespace=mail|slack
  object_ref=<any connected account ref>
  action=request_upload
  payload_json={"filename": "report.pdf", "mime": "application/pdf"}
->
  {upload_url, staged_ref, expires_at, max_bytes}
```

Step 2 — send the bytes (raw body, no form encoding):

```bash
curl -X POST --data-binary @report.pdf -H "Content-Type: application/pdf" "<upload_url>"
# -> {"ok": true, "staged_ref": "staged:<id>:report.pdf", "size_bytes": ...}
```

Step 3 — reference the staged ref in the bounded action:

```text
mail:   action=send    payload_json={"to": "...", "subject": "...",
                                     "attachments": [{"staged_ref": "staged:<id>:report.pdf"}]}
mail:   action=forward payload_json={..., "attachments": [{"staged_ref": ...}]}
slack:  action=upload_file payload_json={"channel": "C…", "staged_ref": "staged:<id>:report.pdf",
                                         "filename": "report.pdf", "initial_comment": "..."}
```

A successful action consumes and deletes the staged file; reuse needs a fresh
slot. To remove a staged file you decided against using:

```text
named_services_action namespace=mail|slack action=discard_upload
  payload_json={"staged_ref": "staged:<id>:report.pdf"}
-> {removed: true}          # idempotent
```

Unused uploads also expire on their own after one hour. Tiny files (≤10MB)
may skip the slot and ride inline as
`{"filename": ..., "content_base64": ...}` — meant for clients that hold
bytes but cannot issue HTTP requests.

Where the bytes wait: a host-local staging directory
(`$STORAGE_PATH/kdcube-integration-staging/<id>/`), 25MB per file — a
hand-off buffer between two HTTP calls, deliberately not durable storage.
Storage and lifecycle details:
[hosting solution doc](../../sdk/solutions/hosting/hosting-README.md).

## Get a file OUT of KDCube (mail attachment, Slack file)

Mail — attachment refs use the stable Gmail part id:

```text
named_services_action object_ref=mail:gmail:<account>:message:<id>
                      action=download_attachments
-> items: [{ref: "mail:…:attachment:<message_id>:<part_id>", filename, size_bytes,
            download: {encoding: "url", url, expires_at}}]
```

or `named_services_get` on one attachment ref directly. Slack:

```text
named_services_get object_ref=slack:<account>:file:<file_id>
-> object.download: {encoding: "url", url, expires_at}
```

Then a plain GET fetches the bytes:

```bash
curl -L -o report.pdf "<download url>"
```

`encoding: "none"` means the hosting bundle mints no links (public origin or
signing secret unavailable); ask in chat instead.

## Requirements checklist

```text
[ ] The deployment's kdcube-services bundle configures
    conversations.file_download_secret in bundles.secrets.yaml.
[ ] The connector consent includes the write claims (mail:send, slack:files:write)
    for inbound; read claims (mail:read, slack:files:read) for outbound.
[ ] The connected provider account approved the matching claims
    (gmail:send, slack:files:write, slack:files:read, ...). Denials come back
    as needs_connected_account_consent with reason, candidates, and the
    Connection Hub URL.
```
