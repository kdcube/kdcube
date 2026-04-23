---
id: ks:docs/README.md
title: "Platform Documentation Index"
summary: "Top-level map of the KDCube documentation tree: architecture, configuration, service runtime, SDK authoring, execution, economics, and deployment or operations guides."
tags: ["docs", "index", "sdk", "service", "ops", "architecture"]
keywords: ["documentation index", "platform architecture", "configuration guides", "service runtime docs", "bundle sdk docs", "execution docs", "economics docs", "deployment and operations docs"]
see_also:
  - ks:docs/what-you-can-do-with-kdcube-README.md
  - ks:docs/arch/architecture-short.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/service/service-and-infrastructure-index-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/ops/ops-overview-README.md
---
# Docs Index

Curated index of platform, service, and SDK documentation.
![pixel-cubes.svg](../../../assets/pixel-cubes.svg)

## Start Here

* What You Can Do With KDCube: [what-you-can-do-with-kdcube-README.md](what-you-can-do-with-kdcube-README.md)
* Quick Start (Local Docker Compose): [quick-start-README.md](quick-start-README.md)

## Architecture

* System Architecture (Short): [architecture-short.md](arch/architecture-short.md)
* System Architecture (Long): [architecture-long.md](arch/architecture-long.md)

## Service & Gateway

* Auth Overview: [auth-README.md](service/auth/auth-README.md)
* Gateway & Admission Control (Current): [gateway-README.md](service/gateway-README.md)
* Service Runtime Configuration Mapping: [service-runtime-configuration-mapping-README.md](configuration/service-runtime-configuration-mapping-README.md)
* Monitoring & Observability: [README-monitoring-observability.md](service/README-monitoring-observability.md)
* Service and Infrastructure Index: [service-and-infrastructure-index-README.md](service/service-and-infrastructure-index-README.md)

## Communication & Relay

* Communication Integrations (External + Internal): [README-comm.md](service/comm/README-comm.md)
* Communication Subsystem Architecture: [comm-system.md](service/comm/comm-system.md)
* Redis-based Chat Relay & SSE Fan-Out: [CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md](service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md)
* Attachments System: [attachments-system.md](hosting/attachments-system.md)

## Execution & Isolation

* Isolated Code Execution Architecture (Docker + External Modes): [runtime-README.md](exec/runtime-README.md)
* Isolated Runtime (ISO) - Design and Operations: [README-iso-runtime.md](exec/README-iso-runtime.md)
* Run Python in ISO Runtime (Docker) — Minimal Developer Guide: [run-py-README.md](exec/run-py-README.md)
* Runtime modes for built-in tools: [README-runtime-modes-builtin-tools.md](exec/README-runtime-modes-builtin-tools.md)
* Isolated Code Execution - Operations Guide: [operations.md](exec/operations.md)
* Executor log streams: [logging-README.md](exec/logging-README.md)
* Distributed Execution (Fargate/External): [distributed-exec-README.md](exec/distributed-exec-README.md)

## SDK Bundles

* Bundle docs index: [bundle-index-README.md](sdk/bundle/bundle-index-README.md)
* Bundle developer guide: [bundle-developer-guide-README.md](sdk/bundle/bundle-developer-guide-README.md)
* Bundle ops guide: [bundle-delivery-and-update-README.md](sdk/bundle/bundle-delivery-and-update-README.md)
* Bundle interfaces: [bundle-interfaces-README.md](sdk/bundle/bundle-interfaces-README.md)
* Bundle client UI contract: [bundle-client-ui-README.md](sdk/bundle/bundle-client-ui-README.md)
* Bundle client communication: [bundle-client-communication-README.md](sdk/bundle/bundle-client-communication-README.md)
* Bundle SSE events: [bundle-sse-events-README.md](sdk/bundle/bundle-sse-events-README.md)
* Bundle storages + cache: [bundle-storage-and-cache-README.md](sdk/bundle/bundle-storage-and-cache-README.md)

## SDK Agents (ReAct v2)

* ReAct v2 Structure: [structure-README.md](sdk/agents/react/structure-README.md)
* End-to-end flow (react v2): [flow-README.md](sdk/agents/react/flow-README.md)
* ReAct v2 State Machine: [react-state-machine-README.md](sdk/agents/react/react-state-machine-README.md)
* Runtime Configuration: [runtime-configuration-README.md](sdk/agents/react/runtime-configuration-README.md)
* ReAct v2 — Context + Turn Data: [react-context-README.md](sdk/agents/react/react-context-README.md)
* Context Layout (Blocks): [context-layout.md](sdk/agents/react/context-layout.md)
* Context Progression & Compaction: [context-progression.md](sdk/agents/react/context-progression.md)
* Context Compaction (v2): [compaction-README.md](sdk/agents/react/compaction-README.md)
* Context Caching (Dual Checkpoints, Round-Based): [context-caching-README.md](sdk/agents/react/context-caching-README.md)
* Context Browser (v2): [context-browser-README.md](sdk/agents/react/context-browser-README.md)
* Session View (Cache TTL): [session-view-README.md](sdk/agents/react/session-view-README.md)
* Plan tracking (react v2): [plan-README.md](sdk/agents/react/plan-README.md)
* ReAct Announce Block (ANNOUNCE banner): [react-announce-README.md](sdk/agents/react/react-announce-README.md)
* ReAct v2 Budget Model: [react-budget-README.md](sdk/agents/react/react-budget-README.md)
* React Round (Tool Call) Model: [react-round-README.md](sdk/agents/react/react-round-README.md)
* React Tools (react.*): [react-tools-README.md](sdk/agents/react/react-tools-README.md)
* React Event Blocks: [event-blocks-README.md](sdk/agents/react/event-blocks-README.md)
* Tool Call Blocks (react v2): [tool-call-blocks-README.md](sdk/agents/react/tool-call-blocks-README.md)
* Timeline (react v2): [timeline-README.md](sdk/agents/react/timeline-README.md)
* Turn Log Structure (Current): [turn-log-README.md](sdk/agents/react/turn-log-README.md)
* Turn Data (Conversation Fetch): [turn-data-README.md](sdk/agents/react/turn-data-README.md)
* Sources Pool: [source-pool-README.md](sdk/agents/react/source-pool-README.md)
* Conversation Artifacts (v2): [conversation-artifacts-README.md](sdk/agents/react/conversation-artifacts-README.md)
* Artifact Discovery (Logical/Physical Paths): [artifact-discovery-README.md](sdk/agents/react/artifact-discovery-README.md)
* Artifact Storage Rules: [artifact-storage-README.md](sdk/agents/react/artifact-storage-README.md)
* Hooks (v2): [hooks-README.md](sdk/agents/react/hooks-README.md)
* External execution notes (Fargate / distributed): [external-exec-README.md](sdk/agents/react/external-exec-README.md)

## SDK Tools & Skills

* Tool Subsystem: [tool-subsystem-README.md](sdk/tools/tool-subsystem-README.md)
* MCP Integration (Runtime): [mcp-README.md](sdk/tools/mcp-README.md)
* Skills Subsystem: [skills-README.md](sdk/skills/skills-README.md)
* Skills Infrastructure: [skills-infra-README.md](sdk/skills/skills-infra-README.md)

## SDK Streaming & Storage

* Streaming Exec Widget (Live Code + Execution Status): [streaming-widget-README.md](sdk/streaming/streaming-widget-README.md)
* Channeled Streamer (Versatile Streamer): [channeled-streamer-README.md](sdk/streaming/channeled-streamer-README.md)
* SDK Storage Layout: [sdk-store-README.md](sdk/storage/sdk-store-README.md)
* Git Store (shared git subprocess transport): [git-store-README.md](sdk/storage/git-store-README.md)
* KV Cache (Service Hub): [cache-README.md](sdk/storage/cache-README.md)

## Economics & OPEX

* Economics Model (Control Plane): [economic-README.md](economics/economic-README.md)
* Economics subsystem: [economics-usage.md](economics/economics-usage.md)
* Economics Operations (Schema + Jobs + Config): [operational-README.md](economics/operational-README.md)
* Stripe Integration Guide: [stripe-README.md](economics/stripe-README.md)
* OPEX Aggregations: [README-AGGREGATIONS.md](aggregations/README-AGGREGATIONS.md)

## Hosting & Storage

* Artifacts Limits: [artifacts-limits-README.md](hosting/artifacts-limits-README.md)
* Managed Infra. S3: [s3-README.md](ops/s3-README.md)

## Safety & Governance

* Feedback System – Complete Architecture & Design (v2.2): [feedback-system.md](feedback-system.md)
* Citations & Sources System: [citations-system.md](citations-system.md)
* Prompt Exfiltration in Direct vs Internal Agents: [README-prompt-exfiltration-internal-and-direct-agents.md](sdk/agents/README-prompt-exfiltration-internal-and-direct-agents.md)

## Deployment

* All-in-One Docker Compose: [README.md](../deployment/docker/all_in_one/README.md)
