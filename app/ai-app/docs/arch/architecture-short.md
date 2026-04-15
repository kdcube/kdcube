---
id: ks:docs/arch/architecture-short.md
title: "Architecture Short"
summary: "Concise system architecture overview with key components and integrations."
tags: ["arch", "architecture", "overview"]
keywords: ["high-level architecture", "ingress", "proc", "gateway", "SSE", "redis"]
see_also:
  - ks:docs/arch/architecture-long.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/hosting/attachments-system.md
---
# KDCube AI App — System Architecture (Short)

This is a **concise overview** of the current system and integrations.
For a deeper dive, see `architecture-long.md` in this folder.

---

## 1) System at a glance

```mermaid
graph TD
  %% Entry / Auth
  UI[Web UI / Client] -->|HTTPS + masked cookie| NGINX[Web Proxy / Nginx]
  AUTH["ProxyLogin (Delegated Auth + 2FA)"] -->|token exchange| NGINX
  NGINX -->|real auth/id cookies| GATE[Chat API + Gateway]
  KB[Knowledge Base Service] --> GATE
  CP[Control Plane / Project Mgmt] --> GATE

  %% Transport + Gateway
  NGINX -->|SSE / Socket.IO| GATE
  NGINX -->|REST| GATE
  GATE -->|session mgmt| SESS[Session Manager]
  GATE -->|rate limit/backpressure| GW[Gateway + Throttling]

  %% Queue + Processing
  GATE -->|enqueue| Q[Redis Queues]
  Q --> PROC[Chat Processor Workers]

  %% Orchestration
  PROC --> BUNDLES[Dynamic Bundles / Workflows]
  BUNDLES -->|events| RELAY[ChatRelay + Redis Pub/Sub]
  RELAY -->|fan-out| GATE

  %% Context management
  BUNDLES --> CTX[Context Management]
  CTX -->|storage| PG[(Postgres RDS)]
  CTX -->|artifacts| S3[(S3)]
  KB -->|storage| PG
  KB -->|artifacts| S3
  CP -->|policies + quotas| PG

  %% Runtime + providers
  BUNDLES --> RT["Runtime (LLM + Tools)"]
  RT --> DOCKER[Ephemeral Docker Exec]
  RT --> TOOLS[External Tools / APIs]

  subgraph EXTPROV["External Providers (LLMs/Search/Embeddings)"]
    OAI[OpenAI]
    ANTH[Anthropic]
    GEM[Gemini]
    BRAVE[Brave Search]
    DDG[DuckDuckGo]
  end

  RT --> OAI
  RT --> ANTH
  RT --> GEM
  RT --> BRAVE
  RT --> DDG

  %% Cache/Queues/PubSub
  BUNDLES -->|cache/queues/pubsub| REDIS["(Redis / ElastiCache)"]

  classDef aws fill:#e8f4ff,stroke:#7aa7d6,color:#0b2b4f;
  classDef ext fill:#f2f7ee,stroke:#8fbf7a,color:#1f3b1c;
  classDef infra fill:#f7f2ff,stroke:#b69ad6,color:#2b1b4f;

  class PG,REDIS,S3 aws;
  class OAI,ANTH,GEM,BRAVE,DDG,TOOLS ext;
  class AUTH infra;
```

---

## 2) ECS / Fargate deployment

In production the services above run as independent **ECS Fargate tasks** inside a private VPC. Docker Compose host‑name aliases are replaced by **AWS Cloud Map DNS** (`*.kdcube.local`). TLS is terminated at the ALB; the proxy listens on HTTP :80 only inside the VPC.

```mermaid
graph LR
  CLIENT[Client] -->|"HTTPS :443"| ALB["ALB\n+ ACM cert"]

  subgraph VPC["VPC — private subnets"]
    ALB -->|"HTTP :80\nX-Forwarded-For"| PROXY["web-proxy\nOpenResty"]

    subgraph ECS["ECS Cluster · Fargate"]
      PROXY --> WEBUI["web-ui\n:80"]
      PROXY -->|"unmask_token()"| PROXYLOGIN["proxylogin\n:80"]
      PROXY --> INGRESS["chat-ingress\n:8010"]
      PROXY --> PROC["chat-proc\n:8020"]
      PROXY -.->|optional| KB["kb\n:8000"]
    end

    CLOUDMAP[/"Cloud Map\nkdcube.local"/] -.->|"A records TTL 10s"| ECS

    INGRESS & PROC & KB --> RDS[(RDS\nPostgreSQL)]
    INGRESS & PROC --> REDIS[(ElastiCache\nRedis)]
    PROC & KB --> EFS[(EFS\nbundle storage)]
    PROXYLOGIN --> SM[Secrets Manager]
    ECS -->|"awslogs"| CW[CloudWatch Logs]
  end

  ECR[ECR] -.->|"image pull"| ECS
```

**What changes vs Docker Compose:**

| | Docker Compose | ECS / Fargate |
|---|---|---|
| Service discovery | Docker DNS (`web-ui`, `chat-ingress` …) | Cloud Map (`*.kdcube.local`) |
| TLS | Proxy-level (Let's Encrypt) | ALB + ACM |
| Real client IP | `$remote_addr` direct | Recovered from `X-Forwarded-For` (`real_ip_header` active) |
| Secrets | `.env` / bind-mount | Secrets Manager → task env vars |
| AWS credentials | `~/.aws` bind-mount | Task IAM role |
| Shared storage | Host bind-mount | EFS access point |

See `architecture-long.md §2` for the full breakdown, security group topology, IAM roles, and CI/CD pipeline.

---

## 3) Supported client transports

- **SSE**: primary streaming transport (current UI default)
- **Socket.IO**: fully supported alternative
- **REST**: non‑streaming endpoints (profile/admin/monitoring/etc.)

---

## 3) Auth & token transport

- **Delegated auth** via ProxyLogin; hosted UI for 2FA is always available.
- **Infra auth (cookie‑only mode)**: client stores only a masked (non‑real) token cookie.
  Nginx exchanges it via ProxyLogin to real tokens, sets auth/id cookies, and forwards to API.
- Server accepts tokens from **headers, cookies, SSE query params, Socket.IO auth payload** (for compatibility).

---

## 4) Multi‑tenancy + storage

- **Postgres**: per‑tenant + per‑project schema (prod/dev separated) + **control_plane** schema.
- **S3** (prod): bucket per tenant/project or shared bucket with prefix segmentation; KB artifacts live here too.
- **Redis**: cache + messaging (Pub/Sub) + rate‑limit counters.
- **Neo4j**: optional, currently off.

Processor note:
- proc concurrency is bounded per worker
- each active proc task is guarded by an activity-based idle watchdog plus a hard wall-time cap
- this allows same-turn `followup` / `steer` to keep a turn warm while still terminating silent or runaway tasks

---

## 5) Limits & economics

- **Gateway rate limiting** + backpressure + circuit breakers.
- **Economics rate limiting**: tier policies, per‑user quotas, concurrency locks.
- **Input limits**: message/attachment size limits enforced at transport layer.

---

## 6) Streaming flow (SSE or Socket.IO)

```mermaid
sequenceDiagram
  participant UI as Client UI
  participant API as Chat API
  participant RL as Redis Relay
  participant Q as Redis Queue
  participant W as Worker / Bundle

  UI->>API: open stream (SSE / Socket.IO connect)
  UI->>API: send message (SSE / Socket.IO)
  API->>Q: enqueue task (per user_type queue)
  W->>Q: dequeue + lock
  W->>RL: publish chat_* events to session channel
  RL-->>API: fan-out to connected stream
  API-->>UI: chat_start/step/delta/complete
```

---

## 7) Key docs

- Comm integrations: [README-comm.md](../service/comm/README-comm.md)
- Comm architecture: [comm-system.md](../service/comm/comm-system.md)
- Gateway: [gateway-README.md](../service/gateway-README.md)
- Economics: [economics-usage.md](../economics/economics-usage.md)
- Control plane: [instance-config-README.md](../service/maintenance/instance-config-README.md)
- Monitoring: [README-monitoring-observability.md](../service/README-monitoring-observability.md)
- ECS deployment: [proxy-ecs-ops-README.md](../arch/proxy/proxy-ecs-ops-README.md)
