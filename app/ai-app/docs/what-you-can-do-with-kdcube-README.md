---
id: ks:docs/what-you-can-do-with-kdcube-README.md
title: "What You Can Do With KDCube"
summary: "User-facing introduction to KDCube: what kind of products and runtime shapes it supports, how environments and bundles are organized, and how engineers and coding agents can work with the platform."
tags: ["docs", "product", "overview", "sdk", "platform"]
keywords: ["what is kdcube", "what can kdcube do", "ai product platform", "bundle runtime", "environment isolation", "build with agents", "integrate existing app", "local to cloud workflow", "kdcube overview"]
see_also:
  - ks:docs/quick-start-README.md
  - ks:docs/README.md
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/service/cicd/cli-README.md
---
# What You Can Do With KDCube

KDCube is a platform and SDK for building, integrating, and operating
end-to-end AI products.

It is designed for work that is larger than a single prompt, a single chat
screen, or one agent loop.

## KDCube In One Paragraph

KDCube gives you an environment that can host many bundles. A bundle is one
application unit that can combine backend logic, APIs, widgets, a full UI,
agents, tools, storage, configuration, secrets, and scheduled work. That lets
you build real AI systems with a stable runtime model instead of stitching
together separate prototypes.

## What You Can Build

You can use KDCube to build:

- AI assistants and copilots with real workflows and custom UI
- internal operational tools with authenticated APIs and admin widgets
- public AI-backed APIs and webhooks
- scheduled or background AI pipelines
- iframe-based applications with their own frontend
- wrappers around an existing backend, frontend, or integration so it runs as a
  KDCube bundle
- multi-surface products where chat, API, UI, and cron logic belong to the same
  application

## How KDCube Organizes Things

Two concepts matter first:

- `tenant/project` = one isolated KDCube environment
- bundle = one end-to-end application unit inside that environment

Interpretation:

- do not think of `tenant/project` as one bundle
- think of it as one full environment that can host many bundles

That environment boundary encloses:

- its own platform snapshot/version
- its own descriptors and deployment configuration
- its own bundle props and bundle secrets
- its own user-scoped bundle state
- its own Postgres and Redis runtime data

This is useful both for lifecycle stages and for parallel isolated deployments.

## What Makes KDCube Different

KDCube is not only:

- a prompt wrapper
- a chatbot template
- a one-agent playground
- a frontend shell around one model call

It gives you a product runtime:

- multiple application surfaces:
  chat, API, widgets, full UI, MCP, cron
- multiple execution styles:
  React v2, Claude Code, custom Python agents, isolated exec, `@venv(...)`
- explicit configuration and secrets ownership
- provenance through timelines, citations, and artifacts
- operational controls for gateway, budgets, backpressure, and deployment

## You Can Build By Hand Or With Agents

KDCube now documents itself in a way that works for both engineers and coding
agents.

That means an agent can help with real bundle work such as:

- creating a new bundle from scratch
- wrapping an existing service or UI into a bundle
- mapping settings into the correct KDCube configuration scope
- wiring the bundle into a local environment
- reloading and testing the bundle
- navigating the docs and examples without guessing

The key point is that the agent is not expected to improvise the platform. The
docs and reference bundles give it a concrete contract to follow.

## Common Ways To Start

### I want to try the platform locally

Start with:

- [Quick Start (Local Docker Compose)](quick-start-README.md)
- [CLI docs](service/cicd/cli-README.md)

### I want to build or wrap a bundle

Start with the Tier 1 bundle pack:

1. [How To Navigate KDCube Bundle Docs](sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
2. [How To Test A Bundle](sdk/bundle/build/how-to-test-bundle-README.md)
3. [How To Write A Bundle](sdk/bundle/build/how-to-write-bundle-README.md)
4. [Bundle Runtime Settings, Configuration, and Secrets](configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [How To Configure And Run A Bundle](sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

### I want to understand configuration ownership

Start with:

- [Bundle Runtime Settings, Configuration, and Secrets](configuration/bundle-runtime-configuration-and-secrets-README.md)
- [Docs Index](README.md)

### I want the full docs map

Use:

- [Docs Index](README.md)

## Short Practical Framing

If you need a concise way to think about KDCube, use this:

- one environment can host many AI applications
- each application is modeled as a bundle
- each bundle can expose several surfaces at once
- the platform handles runtime, state separation, provenance, and deployment
- both engineers and coding agents can work effectively because the docs and
  examples are structured for that workflow
