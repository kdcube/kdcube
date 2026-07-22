/**
 * A named-service namespace as one agent sees it: which operations are wired
 * into the agent's roster (`connected`) vs served by the provider but not yet
 * wired (`available` — the "connect more" signal), plus per-account consent.
 */

export interface ConsentState {
  kind?: string; // e.g. delegated_agent_grant
  covered?: string[];
  unmet?: string[];
  /** opaque grant block the UI POSTs back to perform the one-click grant. */
  grant?: unknown;
}

export interface ConnectedAccount {
  accountId: string;
  label?: string;
  providerId?: string;
  claims?: string[];
}

export interface NamedServiceOp {
  op: string; // e.g. object.search
  tool?: string; // resolved tool name, e.g. search_objects
  /** true = in the roster; false = served by the provider but not wired ("connect more"). */
  enabled: boolean;
  description?: string;
}

export interface NamedServiceRealm {
  namespace: string;
  alias: string;
  connected: NamedServiceOp[];
  available: NamedServiceOp[];
  connectedAccounts?: ConnectedAccount[];
  consent?: ConsentState;
}
