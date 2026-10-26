---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/proxy/proxy-ecs-ops-README.md
title: "Proxy ECS Ops"
summary: "Operational contract for the OpenResty entry proxy on ECS: public edge provenance, trusted application ingress, forwarded metadata, config activation, and rollout verification."
tags: ["proxy", "openresty", "ops", "ecs", "aws", "cloudfront", "alb", "security"]
keywords: ["OpenResty", "ECS", "CloudFront", "ALB", "origin verification", "trusted application ingress", "private MCP", "real IP", "forwarded proto", "nginx config activation", "checksum"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proxy/proxy-ops-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proxy/proxy-local-ops-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-app-surface-interoperability-README.md
---

# Proxy ECS Ops

KDCube's ECS entry proxy is an OpenResty service behind an Application Load
Balancer. A deployment may expose that ALB directly or place CloudFront in
front of it. Those topologies use different authoritative headers; the proxy
configuration is rendered for the selected topology.

The proxy listens on port 80 inside the ECS network and forwards to services
resolved through Cloud Map.

## Supported Entry Topologies

### Direct ALB

```text
viewer
  -> ALB HTTPS listener
       -> OpenResty port 80
            -> KDCube services

viewer HTTP
  -> ALB HTTP listener
       -> HTTPS redirect
```

The ALB is the sole public proxy in this mode:

- the HTTPS listener forwards to OpenResty;
- the HTTP listener redirects instead of forwarding application traffic;
- OpenResty obtains the viewer scheme from the ALB-authored
  `X-Forwarded-Proto` value;
- OpenResty obtains the viewer address from the ALB-authored
  `X-Forwarded-For` chain.

For public requests, the proxy task security group accepts port 80 from the
ALB security group. Trusting either forwarded header requires that other
public network identities cannot reach the proxy task directly. A separate
source-group rule admits trusted KDCube application tasks for deliberate
same-KDCube REST and MCP calls, as described below.

### CloudFront and ALB

```text
viewer HTTPS
  -> CloudFront
       adds X-KDCube-Origin-Verify
       adds X-KDCube-Viewer-Proto
       adds CloudFront-Viewer-Address
       -> public ALB HTTPS origin listener
            verifies X-KDCube-Origin-Verify
            -> OpenResty port 80
                 -> KDCube services
```

The CloudFront path establishes the following boundary:

1. CloudFront connects to the ALB over HTTPS, and the ALB security group
   admits port 443 from the AWS-managed CloudFront origin-facing prefix list.
2. The ALB forwarding rule also requires a deployment-generated
   `X-KDCube-Origin-Verify` value.
3. Requests that do not match the forwarding rule receive a fixed `403`.
4. CloudFront writes the dedicated viewer metadata consumed by OpenResty.

The network restriction and origin-verification value serve different
purposes. The prefix list limits the source network. The header condition
binds the request to this distribution and deployment.

The generated origin-verification value is deployment state. It must not be
placed in a tracked descriptor or log. A deployment principal that can read
or modify CloudFront and ALB configuration can also control this boundary;
IAM and CI controls protect that principal.

### Trusted Application Surface Calls

Trusted KDCube app code may consume another app's real REST or MCP surface
through the private OpenResty address. The network contract is:

```text
application_tasks security group
  -> web_proxy security group : TCP 80

web_proxy ECS task
  -> attaches only web_proxy security group
```

This source-group rule supplies reachability to one proxy listener. The target
REST or MCP surface still authenticates the request and enforces its current
endpoint, resource, operation, and domain policy. The proxy keeps its dedicated
network identity and does not inherit application-task access to Redis, RDS,
or other application services.

Generated code executes behind the supervisor/executor boundary and does not
receive the trusted application-task network identity.

## Forwarded Scheme Contract

OpenResty always sends one normalized `X-Forwarded-Proto` value downstream.
The source depends on the selected topology:

| Topology | Authoritative input |
| --- | --- |
| Direct ALB | ALB-authored `X-Forwarded-Proto` |
| CloudFront | CloudFront-authored `X-KDCube-Viewer-Proto` after origin verification |

The proxy takes the rightmost value when an accepted input contains a
comma-joined chain, accepts only `http` or `https`, and otherwise falls back
to the scheme of its immediate request.

This normalization validates syntax. For public requests, provenance comes
from the selected ingress topology: the ALB authors `X-Forwarded-Proto` in
direct mode; CloudFront authors `X-KDCube-Viewer-Proto`, and the ALB verifies
the CloudFront origin, in CDN mode. Trusted application calls share the
listener and follow the explicit edge-header boundary described below.

For local non-TLS deployments, `assembly.yaml` owns the equivalent choice:

```yaml
proxy:
  forwarded_proto:
    source: "request"                    # default direct-local behavior
    # source: "trusted_x_forwarded_proto" # trusted TLS terminator in front
```

The CLI renders all non-TLS local proxy variants from this setting. The
`trusted_x_forwarded_proto` value preserves an external HTTPS scheme through a
tunnel or load balancer; the operator must make that terminator the effective
ingress. See the assembly descriptor reference for the complete contract.

## Viewer Address Contract

OpenResty rate-limit keys use the recovered viewer address.

| Topology | `real_ip_header` |
| --- | --- |
| Direct ALB | `X-Forwarded-For` |
| CloudFront | `CloudFront-Viewer-Address` |

`set_real_ip_from` is rendered from the deployment network CIDR and tells
OpenResty which reachable network peers may supply the selected real-IP
header. The proxy-task security group admits the ALB and the explicit trusted
application-task source group; those network rules and OpenResty's header
trust serve different purposes. CloudFront's viewer address may include a
source port; current OpenResty/Nginx real-IP support accepts the RFC 3986
address-and-port form.

### Shared-Listener Edge-Header Boundary

An internal app call normally carries no edge-authored viewer headers, so
OpenResty uses its immediate scheme and address. Because the ALB path and the
trusted application-task path share one listener, the current topology relies
on trusted application code not authoring the edge-owned headers selected for
that deployment:

- direct ALB: `X-Forwarded-Proto` and `X-Forwarded-For`;
- CloudFront and ALB: `X-KDCube-Viewer-Proto` and
  `CloudFront-Viewer-Address`.

The network rule grants application tasks reachability; it does not make
application-authored edge metadata authoritative. A separate internal
listener, or equivalent sanitization before applying edge headers, would
remove this shared-listener assumption. Generated code does not receive the
trusted application-task network identity and cannot reach this path directly.

Access logs retain both identities needed for diagnosis:

- `viewer`: the recovered viewer address;
- `peer`: the immediate trusted peer before real-IP replacement.

They also retain the forwarded chain and CloudFront viewer-address header.

## Configuration Activation

The proxy image contains the activation and startup utility:

```text
deployment/docker/all_in_one_kdcube/nginx/kdcube-nginx-config
```

The cloud deployment keeps the selected template in shared proxy-config
storage. Operators do not edit that derived file directly.

Deployment-owned Cognito and delegated templates carry the same bounded
[request-header capacity](proxy-ops-README.md#request-header-capacity). Adopt a
capacity change in the deployment's source template, then activate and verify
it through the flow below. The image supplies the utility; the shared template
supplies the effective header limit.

### Desired configuration

Terraform performs the deterministic part of the contract:

```text
selected source template
  + frame-embedding settings
  + ingress-topology markers
  -> exact desired template bytes
  -> SHA-256
```

The desired template bytes and SHA are placed in the one-shot activation task
definition. The SHA is also placed independently in the long-lived web-proxy
task definition as `NGINX_CONFIG_SHA256`.

### Activation task

After each infrastructure apply, the workflow runs the one-shot task:

```text
kdcube-nginx-config activate
```

The task:

1. takes an exclusive activation lock in shared proxy-config storage;
2. decodes the desired template into a temporary file in the shared
   filesystem;
3. computes its SHA-256 and compares it with the deployment-provided SHA;
4. renders `APP_DOMAIN`, `ALB_CIDR`, and `ROUTE_PREFIX` into a temporary
   runtime candidate;
5. runs `openresty -t` against that candidate;
6. moves the validated template and SHA marker into their active paths.

The moves occur only after validation. A process interruption between the two
moves can temporarily leave a mismatched pair; proxy startup detects that
state and fails closed. Running the activation task again repairs it.

The deployment workflows also serialize runs per target environment. The
shared lock prevents concurrent activation tasks from interleaving their file
moves; workflow serialization prevents an older deployment run from activating
after a newer run for the same environment.

Exit codes used by the deployment workflow are:

| Code | Meaning |
| --- | --- |
| `0` | A validated template was activated. |
| `10` | The active template and marker already match the desired SHA. |
| other | Activation failed; the workflow stops. |

The workflow input named `run_nginx_config_init` is a force-recovery switch.
Normal changes are detected from content and do not depend on that input.

### Proxy startup

In cloud mode, startup requires three equal values:

```text
SHA-256(active template)
  == active SHA marker
  == NGINX_CONFIG_SHA256 from this web-proxy task definition
```

It then renders the runtime candidate, runs `openresty -t`, installs the
rendered file inside the container, and records both the template SHA and the
rendered runtime SHA under `/run/kdcube/`.

This three-way comparison detects:

- stale shared config from an earlier deployment;
- a partial activation;
- corrupt template or marker bytes;
- a proxy task starting against another task revision's template.

It does not prove that an authorized or compromised deployment writer chose
the correct source template. A principal able to rewrite the desired template,
activation task, and proxy task definition can make all three values agree.
Source review, CI integrity, and IAM protect that threat boundary.

### Post-roll verification

After ECS services converge, the workflow enters the running proxy task and
checks:

```text
Terraform desired SHA
  == shared marker
  == SHA-256(shared active template)
  == startup-recorded template SHA

startup-recorded runtime SHA
  == SHA-256(container nginx.conf)
```

A successful infrastructure workflow therefore establishes which reviewed
template and rendered runtime file the running task loaded.

## Rollout Sequence

The expected sequence is:

```text
terraform apply
  -> registers desired activation and proxy task definitions
  -> activation task validates and activates desired config
  -> web-proxy rollout starts or is forced when config changed
  -> ECS convergence check
  -> running-container checksum check
```

Terraform may register a new proxy task definition before activation finishes.
Such a task carries the new expected SHA and rejects the old shared template.
The previous healthy task remains in service while ECS retries; activation
then allows the new revision to start. ECS deployment health settings must
retain the old task until the replacement is healthy.

## Service Discovery

Cloud deployments resolve upstream services through Cloud Map names such as:

```nginx
upstream web_ui      { server web-ui.kdcube.local:80; }
upstream chat_api    { server chat-ingress.kdcube.local:8010; }
upstream chat_proc   { server chat-proc.kdcube.local:8020; }
upstream proxy_login { server proxylogin.kdcube.local:80; }
```

The AWS VPC resolver is configured for variable-based proxy targets and Lua
subrequests:

```nginx
resolver 169.254.169.253 valid=10s;
resolver_timeout 5s;
```

## Network Checklist

- The proxy task accepts port 80 from the ALB security group and the explicit
  trusted application-task security group.
- The proxy task carries only its dedicated security group; shared proxy-config
  storage admits NFS from that group explicitly.
- Trusted same-KDCube REST/MCP calls still pass target authentication and
  authorization after reaching OpenResty.
- Trusted application calls do not author the edge-owned scheme or viewer
  address headers selected for the deployment topology.
- Direct-ALB mode redirects public HTTP to HTTPS.
- CloudFront mode restricts ALB ingress to the managed origin-facing prefix
  list and requires the origin-verification header on forwarding rules.
- Unmatched CloudFront-origin requests receive `403`.
- The selected topology renders the expected scheme and viewer-address
  sources into OpenResty.
- Rate limits use the recovered viewer address.
- The ALB idle timeout covers the configured streaming and WebSocket timeout.
- Cloud Map and the VPC resolver can resolve every declared upstream.

## Configuration Checklist

- The web-proxy and activation tasks use the same released proxy image.
- The web-proxy task definition carries `NGINX_CONFIG_SHA256`.
- The activation task carries the desired template bytes and the same SHA.
- The activation task runs after every non-plan infrastructure apply.
- `openresty -t` succeeds for each selected auth and ingress topology.
- ECS rollout convergence succeeds.
- The running-container checksum verification succeeds.

## Verification Commands

The repository provides focused contract tests:

```bash
python3 -m unittest app/ai-app/deployment/docker/tests/test_proxy_config_contract.py
./app/ai-app/deployment/docker/tests/test_proxy_config_runtime.sh <proxy-image>
```

The proxy Dockerfile supports both platform build contexts used by KDCube.
Release workflows build with `app/ai-app` as the context and use the
Dockerfile's `KDCUBE_AI_APP_SOURCE_PATH=.` default. A local CLI runtime stages
the repository and builds the proxy from that repository root, so both local
Compose files pass `KDCUBE_AI_APP_SOURCE_PATH=app/ai-app`. The contract test
resolves the startup utility from both contexts before an image is published
or locally refreshed.

The deployment repository should additionally run its Terraform validation,
workflow parsing, and every rendered cloud-template topology against the same
proxy image before rollout.

## Source Files

- Proxy image:
  `app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_ProxyOpenResty`
- Activation/startup utility:
  `app/ai-app/deployment/docker/all_in_one_kdcube/nginx/kdcube-nginx-config`
- Local proxy template:
  `app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf`
- Managed-ECS reference template:
  `app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ecs.conf`

## External References

- [AWS: restrict access to Application Load Balancer origins](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/restrict-access-to-load-balancer.html)
- [AWS: add CloudFront request headers](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/adding-cloudfront-headers.html)
- [AWS: require HTTPS to a custom origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-https-cloudfront-to-custom-origin.html)
- [Nginx real-IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html)
