/** Per-agent capabilities (loaded lazily per agent). */
import type { ConsentState, NamedServiceRealm } from './named-service.ts';

export interface ToolRef {
  name: string;
  description?: string;
  /** tool_traits.<name>.strategy — e.g. exploration / exploitation. */
  strategy?: string[];
}

export interface ToolGroup {
  alias: string;
  kind: string; // python | mcp | named_service
  tools: ToolRef[];
}

export interface McpConnection {
  alias: string;
  serverId?: string;
  delegated?: boolean;
  scopes?: string[];
  consent?: ConsentState;
}

export interface SkillRef {
  id: string;
  label?: string;
}

export interface ModelChoice {
  model: string;
  provider?: string;
  label?: string;
}

export interface SubagentRef {
  id: string;
  label?: string;
}

export interface AgentCapabilities {
  agentId: string;
  tools: ToolGroup[];
  mcp: McpConnection[];
  namedServices: NamedServiceRealm[];
  skills: SkillRef[];
  models: ModelChoice[];
  defaultModel?: string;
  subagents: SubagentRef[];
}

// ── static per-agent config (read straight from the app's props) ────────────

/** A named-service namespace an agent consumes, from its roster config. */
export interface NamedServiceConfig {
  namespace: string;
  /** the door alias the roster is bound to (e.g. `named_services`). */
  alias?: string;
  operations: string[];
}

/**
 * One agent as configured in the app: its tools, MCP connections, the
 * named-service namespaces it consumes, and its model choices — all from
 * `surfaces.as_consumer.agents.<id>` in the app's props. This is the static
 * config view (no runtime consent/connection state; that enrichment is later).
 */
export interface AgentConfig {
  id: string;
  isDefault?: boolean;
  tools: ToolGroup[];
  mcp: McpConnection[];
  namedServices: NamedServiceConfig[];
  models: ModelChoice[];
  defaultModel?: string;
  maxTokens?: number;
  additionalInstructions?: string;
  capabilityProvider?: string;
  /** the full agent props subtree — the completeness fallback (raw tree). */
  raw: Record<string, unknown>;
}
