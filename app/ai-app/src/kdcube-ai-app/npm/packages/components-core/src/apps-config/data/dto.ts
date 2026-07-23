/**
 * Raw backend payload shapes (permissive). These mirror what the platform admin
 * REST and the per-app `agent_capabilities` op return today; ./normalize maps
 * them into the clean domain model. Kept loose on purpose — normalize is the one
 * place that absorbs backend shape drift, so the UI never sees a raw payload.
 */

export interface RawBundlesResponse {
  available_bundles?: Record<string, RawBundleEntry>;
  default_bundle_id?: string;
  tenant?: string;
  project?: string;
}

export interface RawBundleEntry {
  id?: string;
  name?: string;
  version?: string;
  path?: string;
  module?: string;
  repo?: string;
  ref?: string;
  subdir?: string;
  git_commit?: string;
  apis?: RawSurface[];
  mcp_endpoints?: RawSurface[];
  widgets?: RawSurface[];
  scheduled_jobs?: RawSurface[];
  allowed_roles?: string[];
  default_chat?: boolean;
}

export interface RawSurface {
  alias?: string;
  route?: string;
  http_method?: string;
  transport?: string;
  user_types?: string[];
  roles?: string[];
  allowed_roles?: string[];
  auth?: string;
  authority_id?: string;
  grants?: string[];
  [k: string]: unknown;
}

/** getBundleProps: { props, defaults, … } — the caller deep-merges defaults←props. */
export interface RawBundleProps {
  props?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  bundle_id?: string;
  tenant?: string;
  project?: string;
}

/** `agent_capabilities` op response. Nested shapes stay loose pending live payloads. */
export interface RawAgentCapabilitiesResponse {
  capabilities?: RawAgentCapabilities;
  selection?: unknown;
  cache_policy?: unknown;
}

export interface RawAgentCapabilities {
  agent?: string;
  tools?: unknown[];
  mcp?: unknown[];
  named_services?: unknown[];
  skills?: unknown[];
  supported_models?: unknown[];
  default_model?: string;
  subagents?: unknown[];
}
