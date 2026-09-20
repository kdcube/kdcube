---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-cards/delegated-cards-README.md
title: "Delegated Access Cards: Storage, Rendering, And Enforcement"
summary: "Points from KDCube's Connection Hub integration to Connection Hub's canonical delegated-card lifecycle."
status: active
tags: ["sdk", "connections", "connection-hub", "delegated-access", "cards"]
keywords: ["Delegated by KDCube", "resource grants", "catalog drift", "Connection Hub", "agent capability control", "resident agent card"]
updated_at: 2026-09-20
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
---

# Delegated Access Cards

Connection Hub owns the canonical card, catalog, persistence, rendering, mutation,
revocation, drift, and per-call enforcement lifecycle. Read
[Delegated Access Cards](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md).

KDCube's Connection Hub app is the first frontend over this contract. KDCube
configuration and integration documents continue to own the host descriptor,
transport, consent-event, and application-registration details.

Hosted agents use one stable credentialless Control Card for descriptor-owned
authority and one resident Agent Card for the user's positive selection. The
runtime and capability picker both consume their live effective projection;
PostgreSQL user settings hold behavior preferences, not delegated authority.
See [Agent Capability Control And Selection](../../user-settings/capabilities-README.md)
for the KDCube producer, picker, and runtime contract.
