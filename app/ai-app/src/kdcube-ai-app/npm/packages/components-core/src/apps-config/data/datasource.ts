/**
 * The pluggable data source behind apps-config.
 *
 * Everything the UI needs is three reads over a scope + ids. The installed-tenant
 * source (./http-source) talks to the platform admin REST + the per-app
 * `agent_capabilities` op; a later "visualize a provided descriptor set" mode is
 * simply another implementation of this interface — no UI change.
 */
import type {
  AppScope,
  AppSummary,
  AppConfigView,
  AgentCapabilities,
} from '../model/index.ts';

export interface AppsConfigDataSource {
  listApps(scope: AppScope): Promise<AppSummary[]>;
  loadAppConfig(scope: AppScope, bundleId: string): Promise<AppConfigView>;
  loadAgentCapabilities(
    scope: AppScope,
    bundleId: string,
    agentId: string,
  ): Promise<AgentCapabilities>;
}

/**
 * Host-provided transport: base URL + auth headers. Kept minimal so any host
 * (the admin widget, the control-plane client) supplies it from its own
 * settings singleton without the core knowing about iframes or tokens.
 */
export interface AppsConfigTransport {
  baseUrl(): string;
  authHeaders(extra?: Record<string, string>): Record<string, string>;
}
