# KDCube

**Open-source multi-tenant agent runtime. In production.**

Frameworks solved building AI agents. Running them as a product is still mostly DIY – tenant isolation, spend controls, sandboxed execution, billing, deployment, environments.

Every team ends up rebuilding the same production infrastructure.

KDCube packages that infrastructure into one runtime. Bring LangGraph, CrewAI, Claude Agent SDK, or your own framework for the agent itself. KDCube handles the production infrastructure your framework doesn't.

## Quick start

```bash
git clone https://github.com/kdcube/kdcube
cd kdcube
# setup steps to follow

# or just:
pip install kdcube-cli
```

Prerequisites: Python 3.9+, Git, Docker.

[→ Full quickstart guide](https://github.com/kdcube/kdcube/wiki/quickstart)

## What's inside

**Multi-tenant isolation.** Architectural boundaries between tenants. Customer A's data physically cannot reach Customer B's runtime.

**Deploy from Git.** Hot reload. YAML config. Runs on a single server. Kubernetes optional.

**Per-user spend controls.** Cap what any user costs you in real time, across every runtime boundary – app code, agent harness, generated code in sandboxes.

**Dual-sandbox security.** Agent-generated code runs with no network, no credentials, no env vars. Privileged operations run in a separate sandbox. Two physical walls, not one.

**Channel-based agent protocol.** Named channels replace JSON tool-calling. No escaping, no mangled code. Production agents run reliably on Haiku.

**8 tools for any system.** Generic ontology-aware tools discover connected systems at runtime. No per-integration tool definitions. No retraining.

## Who this is for

You built agents in LangGraph, CrewAI, Claude Agent SDK, or raw API calls. They work. Now you need to run them for users who aren't you.

If you're building a single-user assistant, you probably don't need KDCube yet.

## How it compares

| | DIY stack | + LangSmith | KDCube |
|---|---|---|---|
| Agent framework | Your framework | Your framework | Any framework |
| Observability | ✗ | ✓ $39/seat/mo | ✓ |
| Deployment | ✗ | Cloud only | ✓ Git, hot reload |
| Tenant isolation | ✗ | ✗ | ✓ Architectural |
| Cost controls | ✗ | ✗ | ✓ Per-user |
| Billing | ✗ | ✗ | ✓ Cross-runtime |
| Security | ✗ | ✗ | ✓ Dual sandbox |

## How it was built

One architect. AI agents as developers. Thousands of corrections. Continuous supervision. Built with agents so you can build with agents.

## Try to break it

Clone it and try to break these three: get one tenant's data into another's runtime, get sandboxed code to reach a credential, find a failure the harness misses on Haiku. Open an issue if you do.

## Documentation

[→ Full documentation](https://github.com/kdcube/kdcube/wiki)

## License

MIT
