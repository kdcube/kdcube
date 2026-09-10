---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/setups/README.md
title: "Setup Recipes"
summary: "Recipes for local and test setups that reproduce a deployment shape: a website beside a local KDCube runtime on two hostnames of one site, the cloud shape in miniature."
status: active
tags: ["recipes", "setups", "local", "mini-cloud", "website"]
keywords: ["setup recipes", "mini cloud", "local emulator", "same-site cross-origin", "website with KDCube"]
updated_at: 2026-09-10
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/setups/test-website-with-kdcube-locally-as-mini-cloud-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/identity-provider-urls-README.md
---

# Setup Recipes

| Recipe | Use it when |
| --- | --- |
| [Test A Website That Uses KDCube Locally, Simulating The Cloud](test-website-with-kdcube-locally-as-mini-cloud-README.md) | You want the cloud's same-site cross-origin shape on one machine, a website on one hostname and a local runtime on another under one parent domain, to prove sign-in in every login mode, cookies, embedded widgets and sign-out before touching an environment. |
| [Serving Local KDCube With Ngrok](../../service/cicd/ngrok-README.md) | You want a local runtime reachable on one public HTTPS origin, alone or with a website served at its root. |
