---
id: repo:kdcube-ai-app/app/ai-app/docs/service/service-and-infrastructure-index-README.md
title: "Service and Infrastructure Documentation Index"
summary: "Entry index for platform service runtime docs: auth, gateway, environment setup, monitoring, scaling, secrets, descriptor wiring, and infrastructure-facing operational guides."
tags: ["service", "index", "infrastructure"]
keywords: ["service docs index", "runtime infrastructure docs", "gateway and auth guides", "monitoring and scaling docs", "environment setup docs", "descriptor and secrets wiring", "service operations"]
updated_at: 2026-09-05
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/redis-key-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/README-monitoring-observability.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/gateway-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/environment/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/host-vault-README.md
---
# Service and Infrastructure Index

Core entry points:
- Auth overview: [auth-README.md](auth/auth-README.md)
- OAuth delegated credential protocol adapter: [oauth-delegated-credential-protocol-adapter-README.md](../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md)
- Service-facing chat interfaces: [servicing-interfaces-README.md](servicing-interfaces-README.md)
- Service streams: [streams/README.md](streams/README.md)
- Synchronization mechanisms and locks: [synch-mechanisms/critical-section-README.md](synch-mechanisms/critical-section-README.md)
- What Redis holds, family by family, and which families are durable: [redis-key-registry-README.md](redis-key-registry-README.md)
- Gateway architecture: [gateway-README.md](gateway-README.md)
- Monitoring & observability: [README-monitoring-observability.md](README-monitoring-observability.md)
- Control plane configuration: [instance-config-README.md](maintenance/instance-config-README.md)
- Descriptor model and mode differences: [descriptors-README.md](cicd/descriptors-README.md)
- Delegated management of a running deployment: [delegated-management-service-README.md](cicd/delegated-management-service-README.md)
- Secret provider selection and end-to-end flows: [secrets-service-README.md](secrets/secrets-service-README.md)
- Local durable host-vault lifecycle and trust boundary: [host-vault-README.md](secrets/host-vault-README.md)
- Runtime env and descriptor mapping: [service-runtime-configuration-mapping-README.md](../configuration/service-runtime-configuration-mapping-README.md)
- All-in-one deployment: [README.md](../../deployment/docker/all_in_one/README.md)
