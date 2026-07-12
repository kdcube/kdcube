---
id: website@2026-07-12/docs/journal/2026-07-13-application-hosted-local-website
title: "Application-Hosted Websites"
summary: "Introduced the dedicated website app and runtime-resolved multi-site registry."
status: complete
date: 2026-07-13
---

# Application-Hosted Local Website

The first implementation incorrectly placed website ownership in
`kdcube-services@1-0`. The final boundary uses a dedicated
`website@2026-07-12` app.

The app owns its shell and composition config. Enabled app sites register a
unique alias, optional hosts, and optional default status in `bundles.yaml`.
OpenResty forwards stable routes while proc resolves the active runtime registry,
so multiple apps can expose sites without generated proxy configuration.
Platform auth is still read from the control-plane backend, so Cognito and
app-hosted platform authorities use the same website code.
