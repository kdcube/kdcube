// Response shapes the Connections widget reads (only the fields it uses).

export interface ConnectionEdgeEndpoint {
  authority_id?: string;
  provider?: string;
  subject?: string;
  user_id?: string;
  label?: string;
}

export interface ConnectionEdge {
  edge_id?: string;
  relationship?: string;
  from?: ConnectionEdgeEndpoint;
  to?: ConnectionEdgeEndpoint;
  grants?: string[];
  status?: string;
  verified_at?: number;
  updated_at?: number;
  metadata?: Record<string, unknown>;
}

export interface ConnectionEdgesResult {
  ok?: boolean;
  platform_user_id?: string;
  edges?: ConnectionEdge[];
  error?: string;
}

export interface ConnectionEdgeMutationResult {
  ok?: boolean;
  edge?: ConnectionEdge;
  error?: string;
  message?: string;
}

export interface ConnectionEdgeChallenge {
  challenge_id: string;
  provider: string;
  target_user_id?: string;
  target_authority_id?: string;
  status: 'pending' | 'completed' | 'expired' | string;
  created_at?: number;
  expires_at?: number;
  completed_at?: number;
  provider_subject?: string;
  label?: string;
  grants?: string[];
}

export interface DelegationGrantOption {
  grant: string;
  kind?: string;
  label?: string;
  description?: string;
  default?: boolean;
}

export interface DelegatedAccessGrantOption {
  authority_id?: string;
  identity_ref?: string;
  grant: string;
  label?: string;
  description?: string;
  source?: string;
  matched_permissions?: string[];
  matched_roles?: string[];
}

export interface DelegatedAccessOperationOption {
  name: string;
  label?: string;
  description?: string;
  grants?: string[];
}

export interface DelegatedAccessNamedServiceOperationOption {
  label?: string;
  description?: string;
  authority_id?: string;
  grants?: string[];
}

export interface DelegatedAccessNamedServiceToolOption extends DelegatedAccessNamedServiceOperationOption {
  operation?: string;
  operations?: Record<string, DelegatedAccessNamedServiceOperationOption>;
}

export interface DelegatedAccessConnectedAccountRequirement {
  provider_id?: string;
  connector_app_id?: string;
  provider_label?: string;
  claims?: string[];
  claim_labels?: Record<string, string>;
  claims_by_operation?: Record<string, string[]>;
  [key: string]: unknown;
}

export interface DelegatedAccessNamedServiceNamespaceOption {
  namespace: string;
  label?: string;
  description?: string;
  authority_id?: string;
  tools?: Record<string, DelegatedAccessNamedServiceToolOption>;
  connected_accounts?: DelegatedAccessConnectedAccountRequirement[];
}

export interface DelegatedAccessResourceOption {
  resource: string;
  label?: string;
  identity_scope?: string;
  grants?: string[];
  admin_only?: boolean;
  operations?: DelegatedAccessOperationOption[];
  named_services?: DelegatedAccessNamedServiceNamespaceOption[];
}

export type DelegatedAccessNamedServiceOperations = Record<string, Record<string, string[]>>;

export interface DelegatedAccessRecord {
  access_id: string;
  label?: string;
  client_id?: string;
  delegate_subject?: string;
  operations?: string[];
  resource_grants?: Record<string, string[]>;
  named_service_operations?: DelegatedAccessNamedServiceOperations;
  /** Per-account claim binding: {provider_id: {account_id: [claims]}}. For a
   *  provider, which connected account(s) this client may use AND, per account,
   *  the claims it may use there. account "*" = any account; claim "*" = any. */
  account_scope?: Record<string, Record<string, string[]>>;
  identity_scope?: string;
  created_at?: number;
  expires_at?: number;
  last_four?: string;
  source?: 'manual' | 'oauth' | string;
}

export interface DelegatedAccessListResult {
  ok?: boolean;
  platform_user_id?: string;
  grant_options?: DelegatedAccessGrantOption[];
  resources?: DelegatedAccessResourceOption[];
  items?: DelegatedAccessRecord[];
  error?: string;
  message?: string;
}

export interface DelegatedAccessCreateResult {
  ok?: boolean;
  access?: DelegatedAccessRecord;
  access_token?: string;
  authorization_header?: string;
  error?: string;
  message?: string;
}

export interface DelegatedAccessRevokeResult {
  ok?: boolean;
  removed?: boolean;
  session_removed?: boolean;
  error?: string;
  message?: string;
}

export interface ConnectionEdgeChallengeResult {
  ok?: boolean;
  challenge?: ConnectionEdgeChallenge;
  platform_user_id?: string;
  target_user_id?: string;
  claimable_by_current_user?: boolean;
  platform_claim_url?: string;
  edge?: ConnectionEdge;
  delegation_options?: DelegationGrantOption[];
  error?: string;
  message?: string;
}

export interface SupportedAuthenticatorProvider {
  provider: string;
  label?: string;
  implemented?: boolean;
  secret_label?: string;
  subject_namespace?: string;
  proofs?: string[];
}

export interface AuthenticatorRow {
  authenticator_id: string;
  provider: string;
  authority_id?: string;
  label?: string;
  enabled?: boolean;
  role_providing?: boolean;
  implemented?: boolean;
  where?: string;
  source?: 'config' | 'postgres' | string;
  subject_namespace?: string;
  secret_ref?: string;
  secret_configured?: boolean;
  selector?: Record<string, unknown>;
  verifier?: Record<string, unknown>;
  properties?: Record<string, unknown>;
}

export interface AuthenticatorsListResult {
  ok?: boolean;
  items?: AuthenticatorRow[];
  count?: number;
  providers?: string[];
  supported_providers?: SupportedAuthenticatorProvider[];
  error?: string;
  message?: string;
}

export interface AuthenticatorMutationResult {
  ok?: boolean;
  authenticator?: AuthenticatorRow;
  removed?: boolean;
  error?: string;
  message?: string;
}

export interface DelegatedToKdcubeClaim {
  claim_id: string;
  label?: string;
  description?: string;
  provider_scopes?: string[];
}

export interface DelegatedToKdcubeConnectorApp {
  connector_app_id: string;
  provider_id: string;
  label?: string;
  enabled?: boolean;
  client_id?: string;
  redirect_uri?: string;
  allowed_claims?: string[];
}

export interface DelegatedToKdcubeProvider {
  provider_id: string;
  label?: string;
  adapter?: string;
  enabled?: boolean;
  claims?: Record<string, DelegatedToKdcubeClaim>;
  connector_apps?: Record<string, DelegatedToKdcubeConnectorApp>;
}

export interface DelegatedToKdcubeAccount {
  account_id: string;
  provider_id: string;
  connector_app_id?: string;
  external_subject?: string;
  display_name?: string;
  email?: string;
  workspace?: string;
  claims?: string[];
  status?: string;
  has_credential?: boolean;
  credential_status?: string;
  credential_kind?: string;
  credential_refreshable?: boolean;
  credential_expires_at?: number;
  reconnect_required?: boolean;
  credential_message?: string;
  credential_status_at?: string;
  last_error?: string;
  last_error_at?: string;
}

export interface DelegatedToKdcubeCatalogResult {
  ok?: boolean;
  enabled?: boolean;
  providers?: Record<string, DelegatedToKdcubeProvider>;
  accounts?: DelegatedToKdcubeAccount[];
  error?: string;
  message?: string;
}

export interface DelegatedToKdcubeMutationResult {
  ok?: boolean;
  account?: DelegatedToKdcubeAccount;
  removed?: boolean;
  account_id?: string;
  error?: string;
  message?: string;
}

export interface DelegatedToKdcubeOAuthStartResult {
  ok?: boolean;
  provider_id?: string;
  connector_app_id?: string;
  authorize_url?: string;
  state_id?: string;
  redirect_uri?: string;
  claims?: string[];
  provider_scopes?: string[];
  error?: string;
  message?: string;
}

// ── provider connections (connections_* ops; registry-driven OAuth) ─────────

// The connections_* ops report failures either as a plain string or as a
// structured {code, message} object — accept both.
export type ConnectionsError = string | { code?: string; message?: string; details?: Record<string, unknown> } | null;

// User-facing claim bundle a provider offers at connect time; picking tiers
// makes the OAuth ask exactly the union of the picked tiers' scopes.
export interface ConnectionsClaimTier {
  id: string;
  label?: string;
  description?: string;
  scopes?: string[];
}

export interface ConnectionsClientApp {
  app_id: string;
  provider: string;
  label?: string;
  enabled?: boolean;
  scopes?: string[]; // the per-app scope ceiling
}

export interface ConnectionsAccount {
  account_id: string;
  provider: string;
  app_id?: string;
  external_user_id?: string;
  display_name?: string;
  email?: string;
  workspace?: string;
  status?: string;
  scope?: string[]; // scopes the provider granted on the last consent
  has_token?: boolean;
  // Present when the provider declares claim_tiers: which tiers the granted
  // scopes fully cover (true = the account already holds that tier).
  tier_coverage?: Record<string, boolean>;
  connected_at?: string;
  updated_at?: string;
}

export interface ConnectionsProviderRow {
  provider: string;
  label?: string;
  enabled?: boolean;
  configured?: boolean;
  connected?: boolean;
  apps?: ConnectionsClientApp[];
  accounts?: ConnectionsAccount[];
  claim_tiers?: ConnectionsClaimTier[]; // display order; [] when the provider offers a single all-in consent
}

export interface ConnectionsCatalogResult {
  ok?: boolean;
  user_id?: string;
  providers?: ConnectionsProviderRow[];
  error?: ConnectionsError;
  message?: string;
}

export interface ConnectionsStartOAuthResult {
  ok?: boolean;
  provider?: string;
  app_id?: string;
  authorize_url?: string;
  state_id?: string;
  redirect_uri?: string;
  error?: ConnectionsError;
  message?: string;
}

export interface ConnectionsDisconnectResult {
  ok?: boolean;
  provider?: string;
  deleted?: boolean;
  accounts?: ConnectionsAccount[];
  error?: ConnectionsError;
  message?: string;
}

// ── delegated access map (admin, read-only) ────────────────────────────────

export interface AccessMapGrant {
  grant: string;
  label?: string;
  description?: string;
  admin_only?: boolean;
  delegable_roles?: string[];
  delegable_permissions?: string[];
}

export interface AccessMapToolRow {
  name: string;
  label?: string;
  description?: string;
  grants: string[];
}

export interface AccessMapNamespaceEntry {
  tool: string;
  operation: string;
  label?: string;
  description?: string;
  grants: string[];
}

export interface AccessMapNamespace {
  namespace: string;
  label?: string;
  description?: string;
  authority_id?: string;
  entries: AccessMapNamespaceEntry[];
  grants: string[];
}

export interface AccessMapResource {
  resource: string;
  label?: string;
  description?: string;
  admin_only?: boolean;
  grants: string[];
  tools: AccessMapToolRow[];
  namespaces: AccessMapNamespace[];
  grant_union: string[];
}

export interface AccessMapProvider {
  provider_id: string;
  label?: string;
  enabled?: boolean;
  connector_apps: { id: string; label?: string; enabled?: boolean; allowed_claims: string[] }[];
  claims: { claim: string; label?: string; description?: string }[];
}

export interface DelegatedAccessMapResult {
  ok?: boolean;
  error?: string;
  message?: string;
  enabled?: boolean;
  grants?: AccessMapGrant[];
  resources?: AccessMapResource[];
  providers?: AccessMapProvider[];
  unknown_grants?: string[];
}

export interface DcrAllowlistResult {
  ok?: boolean;
  error?: string;
  message?: string;
  allowed_redirect_uris?: string[];
  effective_redirect_uris?: string[];
  defaults?: string[];
}
