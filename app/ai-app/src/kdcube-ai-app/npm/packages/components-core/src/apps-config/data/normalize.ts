/**
 * Pure DTO → domain-model mappers. The only place backend shape knowledge lives
 * on the read path; everything downstream (state, UI) sees clean model types.
 */
import type {
  AppSummary,
  AppOrigin,
  ProviderSurface,
  SurfaceVisibility,
  ConsumerOverview,
  AgentConfig,
  NamedServiceConfig,
  ModelChoice,
  AgentCapabilities,
  ConsentState,
  ToolGroup,
  McpConnection,
  NamedServiceRealm,
  NamedServiceOp,
} from '../model/index.ts';
import type { RawBundleEntry, RawSurface, RawAgentCapabilities } from './dto.ts';
import { getPath } from './props.ts';

// ── tiny defensive coercers (payloads are loose) ────────────────────────────
type AnyRec = Record<string, unknown>;
const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const rec = (v: unknown): AnyRec =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as AnyRec) : {};
const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined);
const strArr = (v: unknown): string[] | undefined =>
  Array.isArray(v) ? (v.filter((x) => typeof x === 'string') as string[]) : undefined;

// ── app identity ────────────────────────────────────────────────────────────
function originFromEntry(e: RawBundleEntry): AppOrigin {
  if (e.repo) return 'git';
  if (e.path) return 'local';
  return 'built-in';
}

export function appSummaryFromEntry(
  bundleId: string,
  e: RawBundleEntry,
  defaultId?: string,
): AppSummary {
  return {
    bundleId,
    name: str(e.name) || bundleId,
    version: str(e.version),
    isDefault: bundleId === defaultId,
    origin: originFromEntry(e),
    path: str(e.path),
    gitCommit: str(e.git_commit),
  };
}

// ── as_provider surfaces ────────────────────────────────────────────────────
function visibilityFromSurface(s: RawSurface): SurfaceVisibility | undefined {
  // Coerce to string[] | undefined — a backend that returns a scalar here would
  // otherwise reach `.join()` in the UI and crash the render.
  const v: SurfaceVisibility = {
    roles: strArr(s.roles),
    userTypes: strArr(s.user_types),
    allowedRoles: strArr(s.allowed_roles),
  };
  if (!v.roles && !v.userTypes && !v.allowedRoles) return undefined;
  return v;
}

export function providerSurfacesFromEntry(e: RawBundleEntry): ProviderSurface[] {
  const out: ProviderSurface[] = [];
  if (e.default_chat) {
    out.push({ kind: 'bundle', alias: 'bundle', label: 'Chat surface', detail: 'default_chat' });
  }
  for (const a of e.apis || []) {
    const label = str(a.alias) || str(a.route) || 'api';
    out.push({
      kind: 'api',
      alias: label,
      label,
      detail: [str(a.http_method), str(a.route)].filter(Boolean).join(' ') || undefined,
      authMode: str(a.auth),
      authorityId: str(a.authority_id),
      grants: strArr(a.grants),
      visibility: visibilityFromSurface(a),
    });
  }
  for (const m of e.mcp_endpoints || []) {
    const label = str(m.alias) || 'mcp';
    out.push({
      kind: 'mcp',
      alias: label,
      label,
      detail: [str(m.transport), str(m.route)].filter(Boolean).join(' ') || undefined,
      authMode: str(m.auth),
      authorityId: str(m.authority_id),
      grants: strArr(m.grants),
      visibility: visibilityFromSurface(m),
    });
  }
  for (const w of e.widgets || []) {
    const label = str(w.alias) || 'widget';
    out.push({
      kind: 'widget',
      alias: label,
      label,
      visibility: visibilityFromSurface(w),
    });
  }
  return out;
}

// ── as_consumer overview ────────────────────────────────────────────────────

/** One agent's static config (tools / mcp / named services / models) from props. */
export function agentConfigFromProps(id: string, agent: AnyRec, isDefault: boolean): AgentConfig {
  const tools: ToolGroup[] = [];
  const mcp: McpConnection[] = [];
  const namedServices: NamedServiceConfig[] = [];

  for (const tv of asArr(agent.tools)) {
    const t = rec(tv);
    const kind = str(t.kind) || 'python';
    if (kind === 'mcp') {
      mcp.push({
        alias: str(t.alias) || str(t.name) || str(t.server_id) || 'mcp',
        serverId: str(t.server_id),
        delegated: t.delegated === true,
        scopes: strArr(t.scopes),
      });
    } else if (kind === 'named_service') {
      const namespaces = rec(t.namespaces);
      for (const nsName of Object.keys(namespaces)) {
        namedServices.push({
          namespace: nsName,
          alias: str(t.alias),
          operations: strArr(rec(namespaces[nsName]).allowed) || [],
        });
      }
    } else {
      const traits = rec(t.tool_traits);
      tools.push({
        alias: str(t.alias) || str(t.name) || 'tools',
        kind,
        tools: (strArr(t.allowed) || []).map((n) => ({
          name: n,
          strategy: strArr(rec(traits[n]).strategy),
        })),
      });
    }
  }

  const modelsCfg = rec(getPath(agent, 'capabilities.models'));
  const models: ModelChoice[] = asArr(modelsCfg.supported)
    .map((mv) => {
      const m = rec(mv);
      return { model: str(m.model) || '', provider: str(m.provider), label: str(m.label) };
    })
    .filter((m) => m.model);

  const maxTokensRaw = getPath(agent, 'model.max_tokens');

  return {
    id,
    isDefault,
    tools,
    mcp,
    namedServices,
    models,
    defaultModel: str(modelsCfg.default),
    maxTokens: typeof maxTokensRaw === 'number' ? maxTokensRaw : undefined,
    additionalInstructions: str(agent.additional_instructions),
    capabilityProvider: str(agent.capability_provider),
    raw: agent,
  };
}

export function consumerOverviewFromProps(merged: Record<string, unknown>): ConsumerOverview {
  const asConsumer = rec(getPath(merged, 'surfaces.as_consumer'));
  const agentsMap = rec(asConsumer.agents);
  const defaultAgent = str(asConsumer.default_agent);
  const agents: AgentConfig[] = Object.keys(agentsMap).map((id) =>
    agentConfigFromProps(id, rec(agentsMap[id]), id === defaultAgent),
  );
  const servers = rec(getPath(asConsumer, 'mcp.services.mcpServers'));
  return { defaultAgent, agents, mcpServices: Object.keys(servers) };
}

// ── per-agent capabilities ──────────────────────────────────────────────────
function toolGroupsFrom(raw: unknown): ToolGroup[] {
  return asArr(raw).map((gv) => {
    const g = rec(gv);
    return {
      alias: str(g.alias) || str(g.name) || 'tools',
      kind: str(g.kind) || 'python',
      tools: asArr(g.tools ?? g.allowed)
        .map((tv) =>
          typeof tv === 'string'
            ? { name: tv }
            : { name: str(rec(tv).name) || '', description: str(rec(tv).description) },
        )
        .filter((t) => t.name),
    };
  });
}

function mcpFrom(raw: unknown): McpConnection[] {
  return asArr(raw).map((mv) => {
    const m = rec(mv);
    return {
      alias: str(m.alias) || str(m.server_id) || 'mcp',
      serverId: str(m.server_id),
      delegated: m.delegated === true,
      scopes: strArr(m.scopes),
      consent: (m.consent ?? undefined) as ConsentState | undefined,
    };
  });
}

function namedServicesFrom(raw: unknown): NamedServiceRealm[] {
  return asArr(raw).map((nsv) => {
    const ns = rec(nsv);
    const realm = rec(ns.realm);
    const ops = asArr(ns.operations ?? realm.operations);
    const connected: NamedServiceOp[] = [];
    const available: NamedServiceOp[] = [];
    for (const ov of ops) {
      const o = rec(ov);
      const entry: NamedServiceOp = {
        op: str(o.op) || str(o.operation) || str(o.name) || '',
        tool: str(o.tool),
        enabled: o.enabled_for_agent !== false,
        description: str(o.description),
      };
      if (!entry.op) continue;
      (entry.enabled ? connected : available).push(entry);
    }
    return {
      namespace: str(ns.namespace) || str(ns.alias) || '',
      alias: str(ns.alias) || str(ns.namespace) || '',
      connected,
      available,
      connectedAccounts: asArr(ns.connected_accounts ?? realm.connected_accounts).map((av) => {
        const a = rec(av);
        return {
          accountId: str(a.account_id) || str(a.id) || '',
          label: str(a.label) || str(a.name),
          providerId: str(a.provider_id),
          claims: strArr(a.claims),
        };
      }),
      consent: (ns.consent ?? undefined) as ConsentState | undefined,
    };
  });
}

export function agentCapabilitiesFromRaw(
  agentId: string,
  raw: RawAgentCapabilities,
): AgentCapabilities {
  return {
    agentId: raw.agent || agentId,
    tools: toolGroupsFrom(raw.tools),
    mcp: mcpFrom(raw.mcp),
    namedServices: namedServicesFrom(raw.named_services),
    skills: asArr(raw.skills).map((sv) =>
      typeof sv === 'string'
        ? { id: sv }
        : { id: str(rec(sv).id) || str(rec(sv).name) || '', label: str(rec(sv).label) },
    ),
    models: asArr(raw.supported_models).map((mv) =>
      typeof mv === 'string'
        ? { model: mv }
        : { model: str(rec(mv).model) || '', provider: str(rec(mv).provider), label: str(rec(mv).label) },
    ),
    defaultModel: raw.default_model,
    subagents: asArr(raw.subagents).map((sv) =>
      typeof sv === 'string' ? { id: sv } : { id: str(rec(sv).id) || '', label: str(rec(sv).label) },
    ),
  };
}
