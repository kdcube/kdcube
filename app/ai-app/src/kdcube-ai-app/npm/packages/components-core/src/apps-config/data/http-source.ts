/**
 * Installed-tenant data source: talks to the platform admin REST + the per-app
 * `agent_capabilities` op via a host-provided transport. One of possibly several
 * `AppsConfigDataSource` implementations (a descriptor-set source lands later).
 */
import type { AppScope, AppSummary, AppConfigView, AgentCapabilities } from '../model/index.ts';
import type { AppsConfigDataSource, AppsConfigTransport } from './datasource.ts';
import type { RawBundlesResponse, RawBundleProps, RawAgentCapabilitiesResponse } from './dto.ts';
import { appsListUrl, appPropsUrl, agentCapabilitiesUrl } from './endpoints.ts';
import { deepMerge } from './props.ts';
import {
  appSummaryFromEntry,
  providerSurfacesFromEntry,
  consumerOverviewFromProps,
  agentCapabilitiesFromRaw,
} from './normalize.ts';

async function getJson(transport: AppsConfigTransport, url: string): Promise<unknown> {
  const res = await fetch(url, {
    method: 'GET',
    credentials: 'include',
    headers: transport.authHeaders({ Accept: 'application/json' }),
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
  return res.json();
}

async function postJson(transport: AppsConfigTransport, url: string, body: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: transport.authHeaders({ Accept: 'application/json', 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${url} → ${res.status}`);
  return res.json();
}

export function createHttpDataSource(transport: AppsConfigTransport): AppsConfigDataSource {
  const fetchBundles = (scope: AppScope) =>
    getJson(transport, appsListUrl(transport.baseUrl(), scope)) as Promise<RawBundlesResponse>;

  return {
    async listApps(scope): Promise<AppSummary[]> {
      const resp = await fetchBundles(scope);
      const entries = resp.available_bundles || {};
      return Object.keys(entries).map((id) =>
        appSummaryFromEntry(id, entries[id], resp.default_bundle_id),
      );
    },

    async loadAppConfig(scope, bundleId): Promise<AppConfigView> {
      const [resp, propsResp] = await Promise.all([
        fetchBundles(scope),
        getJson(transport, appPropsUrl(transport.baseUrl(), scope, bundleId)) as Promise<RawBundleProps>,
      ]);
      const entry = (resp.available_bundles || {})[bundleId] || {};
      const merged = deepMerge<Record<string, unknown>>(propsResp.defaults || {}, propsResp.props || {});
      return {
        scope,
        app: appSummaryFromEntry(bundleId, entry, resp.default_bundle_id),
        provider: providerSurfacesFromEntry(entry),
        consumer: consumerOverviewFromProps(merged),
        config: merged,
      };
    },

    async loadAgentCapabilities(scope, bundleId, agentId): Promise<AgentCapabilities> {
      const resp = (await postJson(
        transport,
        agentCapabilitiesUrl(transport.baseUrl(), scope, bundleId),
        { data: { agent: agentId } },
      )) as RawAgentCapabilitiesResponse;
      return agentCapabilitiesFromRaw(agentId, resp.capabilities || {});
    },
  };
}
