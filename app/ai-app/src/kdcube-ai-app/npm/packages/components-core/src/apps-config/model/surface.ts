/** `as_provider` — the inbound surfaces an app exposes to the platform. */

export type ProviderSurfaceKind = 'bundle' | 'api' | 'mcp' | 'widget';

export interface SurfaceVisibility {
  roles?: string[];
  userTypes?: string[];
  allowedRoles?: string[];
}

export interface ProviderSurface {
  kind: ProviderSurfaceKind;
  /** server/widget/route alias; the literal 'bundle' for the bundle surface. */
  alias: string;
  label: string;
  /** free-form detail: method, transport, default_chat, … */
  detail?: string;
  authMode?: string; // bundle | managed | token | none
  authorityId?: string;
  grants?: string[];
  visibility?: SurfaceVisibility;
}
