---
id: ks:docs/service/cicd/gateway-config-README.md
title: "Gateway Config Descriptor"
summary: "gateway.yaml schema and CLI handling for GATEWAY_CONFIG_JSON."
tags: ["service", "cicd", "gateway", "descriptor", "schema", "cli"]
keywords: ["gateway.yaml", "GATEWAY_CONFIG_JSON", "rate limits", "backpressure"]
see_also:
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/cli-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Gateway Config Descriptor (gateway.yaml)

The gateway descriptor is an optional YAML file that replaces
`GATEWAY_CONFIG_JSON` in `.env.ingress`, `.env.proc`, and `.env.metrics`.

**Template:** [`app/ai-app/deployment/gateway.yaml`](../../../deployment/gateway.yaml)

## CLI usage

During `kdcube-setup`, the wizard can prompt for a gateway config path:

```
Gateway config path (gateway.yaml) (leave blank to skip)
```

If provided, the CLI:
- loads the YAML (or JSON) file,
- writes it into `GATEWAY_CONFIG_JSON` for ingress/proc/metrics,
- then patches `tenant` and `project` from the wizard prompts.

You can also set `KDCUBE_GATEWAY_DESCRIPTOR_PATH` to skip the prompt.

## Schema

The schema mirrors `GATEWAY_CONFIG_JSON` under a `gateway` root:

```yaml
gateway:
  tenant: "TENANT_ID"
  project: "PROJECT_ID"
  profile: "development"
  guarded_rest_patterns:
    ingress: []
    proc: []
  rate_limits:
    ingress: {}
    proc: {}
  backpressure: {}
  service_capacity: {}
  pools: {}
  limits: {}
  redis: {}
```

Use the full template file for the default values and structure.
