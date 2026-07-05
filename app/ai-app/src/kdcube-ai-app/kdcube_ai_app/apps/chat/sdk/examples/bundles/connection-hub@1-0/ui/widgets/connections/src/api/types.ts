// Response shapes the Connections widget reads (only the fields it uses).

export interface EmailAccount {
  account_id: string;
  provider: string; // "icloud" | ...  (Gmail is a connections provider, not here)
  email: string;
  display_name: string;
  status?: string;
  has_token?: boolean;
}

export interface EmailStatusResult {
  ok: boolean;
  accounts: EmailAccount[];
}

export interface ConnectionAccount {
  account_id: string;
  provider: string;
  label?: string;
  display_name?: string;
  email?: string;
  workspace?: string;
  has_token?: boolean;
  status?: string;
}

export interface ConnectionApp {
  app_id: string;
  provider: string;
  label?: string;
  enabled?: boolean;
  scopes?: string[]; // the per-app scope ceiling; a connect may request a subset
}

export interface CatalogEntry {
  provider: string;
  label?: string;
  enabled?: boolean;
  configured?: boolean;
  connected?: boolean;
  apps?: ConnectionApp[];
  accounts?: ConnectionAccount[];
}

export interface CatalogResult {
  ok?: boolean;
  providers?: CatalogEntry[];
  entries?: CatalogEntry[];
}

export interface StartOAuthResult {
  ok?: boolean;
  authorize_url?: string;
  error?: { message?: string } | null;
}

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

export interface DelegatedAccessResourceOption {
  resource: string;
  label?: string;
  identity_scope?: string;
  grants?: string[];
  admin_only?: boolean;
  operations?: DelegatedAccessOperationOption[];
}

export interface DelegatedAccessRecord {
  access_id: string;
  label?: string;
  client_id?: string;
  delegate_subject?: string;
  operations?: string[];
  resource_grants?: Record<string, string[]>;
  identity_scope?: string;
  created_at?: number;
  expires_at?: number;
  last_four?: string;
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
