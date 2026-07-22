/**
 * apps-config domain model — app identity + the two-sided surface view the UI
 * renders. Host- and backend-agnostic: every host and the React layer depend on
 * these types, never on raw backend payloads (see ../data).
 *
 * "app" is the user-facing word; `bundleId` stays the technical key (bundle_id).
 */
import type { ProviderSurface } from './surface.ts';
import type { AgentConfig } from './agent.ts';

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface AppScope {
  tenant: string;
  project: string;
}

export type AppOrigin = 'built-in' | 'local' | 'git' | 'unknown';

/** One row in the app list. */
export interface AppSummary {
  bundleId: string;
  name: string;
  version?: string;
  isDefault?: boolean;
  origin?: AppOrigin;
  path?: string;
  gitCommit?: string;
}

/** The `as_consumer` view: the agents configured in the app + shared MCP services. */
export interface ConsumerOverview {
  defaultAgent?: string;
  agents: AgentConfig[];
  /** `surfaces.as_consumer.mcp.services` server ids (shared across agents). */
  mcpServices: string[];
}

/** Header view for a selected app: identity + both surface sides + full config. */
export interface AppConfigView {
  scope: AppScope;
  app: AppSummary;
  provider: ProviderSurface[];
  consumer: ConsumerOverview;
  /** the full merged app config (defaults←props) — for the config tree. */
  config: Record<string, unknown>;
}
