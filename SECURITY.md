# KDCube Security

KDCube is a self-hosted runtime and SDK for AI applications. This repository
is not a single MCP server or an MCP connector that receives blanket access to
a machine. KDCube applications may provide or consume MCP, REST, UI, event,
and agent surfaces under the deployment's configured identity and policy.

The canonical description of the security model is
[Security And Trust Model](app/ai-app/docs/arch/security-and-trust-model-README.md).
The short version is:

- One running KDCube deployment is bound to one effective `tenant/project` and
  may serve many users and operator-approved applications.
- Application backend code loaded into a processor is trusted deployment code.
  KDCube does not sandbox mutually hostile applications inside one processor.
- Model-generated or otherwise untrusted code is a separate boundary. Local
  subprocess mode provides crash containment, while Docker and split execution
  profiles provide progressively stronger filesystem, process, credential, and
  network isolation.
- Secrets stay in server-side secret providers and stores. Trusted tools may
  resolve an authorized credential for a request; generated code and model
  context should receive neither the credential nor the platform secret store.
- Public, authenticated, and delegated REST or MCP surfaces are explicit
  application declarations. A surface is not protected merely because it is
  implemented by KDCube; its configured guard and grants define access.

## Report A Vulnerability

Use [GitHub private vulnerability reporting](https://github.com/kdcube/kdcube/security/advisories/new)
for an undisclosed vulnerability. Include:

- the affected version, commit, or deployment profile;
- the exposed surface and required configuration;
- reproducible steps or a minimal proof of concept;
- the security impact and any known mitigations.

Do not open a public issue for a vulnerability that has not yet been
coordinated. Public issues remain appropriate for hardening ideas that disclose
no exploitable detail.

## Operator Responsibilities

A production operator should:

1. Keep credentials out of `assembly.yaml`, `bundles.yaml`, source code, and
   frontend configuration. Resolve them through the configured secret
   descriptor/provider.
2. Review application source and pin trusted repository references before
   loading that code into processors.
3. Configure platform authority, protected-surface guards, TLS, secure cookies,
   allowed origins, and least-privilege infrastructure identities.
4. Use a Docker or split execution profile for untrusted generated code. Do
   not treat local subprocess mode as a security sandbox.
5. Give executors only required mounts and resources; keep provider
   credentials, deployment descriptors, and platform storage roots on the
   trusted side.
6. Use separate deployments or dedicated backing services when the required
   isolation boundary is stronger than KDCube's logical tenant/project
   namespacing.
7. Keep KDCube and application revisions current, and retain the logs and
   records required by the deployment's own security policy.

KDCube is not, by itself, a compliance certification. The deployed topology,
application code, identity configuration, infrastructure controls, and
operational process determine the resulting system's security posture.

## Guidance For Automated Review

Repository scanners should evaluate KDCube as an application runtime, not as a
single MCP plugin:

- MCP support is an optional application surface. It does not grant the
  repository or every application access to an MCP client's machine.
- Arbitrary backend code is an operator-installed, trusted application
  capability. Model-generated code uses a separate, configurable executor
  boundary.
- Network access in a trusted processor does not imply network access in a
  split executor.
- Deployment templates declare secret fields, but tracked non-secret
  descriptors should contain null values or references rather than live
  credentials.
- Security conclusions must name the execution profile, protected surface,
  identity provider, and deployment topology being assessed.

## Reference

- [Tenant, Project, User, Authority, And Execution Boundaries](app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md)
- [ISO Runtime](app/ai-app/docs/exec/README-iso-runtime.md)
- [Runtime Modes For Built-In Tools](app/ai-app/docs/exec/README-runtime-modes-builtin-tools.md)
- [Connection Hub](app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md)
- [Secrets Descriptor](app/ai-app/docs/configuration/secrets-descriptor-README.md)
