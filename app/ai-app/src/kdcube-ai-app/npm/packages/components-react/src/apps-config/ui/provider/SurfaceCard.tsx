/** One as_provider surface (bundle / api / mcp / widget). */
import type { ProviderSurface } from '@kdcube/components-core/apps-config';
import { Badge } from '../primitives/Badge.tsx';

const KIND_TONE = {
  bundle: 'accent',
  api: 'neutral',
  mcp: 'success',
  widget: 'muted',
} as const;

export function SurfaceCard({ surface }: { surface: ProviderSurface }) {
  const vis = surface.visibility;
  return (
    <div className="ac-surface">
      <div className="ac-surface__head">
        <Badge tone={KIND_TONE[surface.kind]}>{surface.kind}</Badge>
        <span className="ac-surface__label">{surface.label}</span>
        {surface.detail && <code className="ac-surface__detail">{surface.detail}</code>}
      </div>
      <div className="ac-surface__meta">
        {surface.authMode && <Badge tone="warn" title="auth mode">{surface.authMode}</Badge>}
        {surface.authorityId && <span className="ac-kv">authority: {surface.authorityId}</span>}
        {surface.grants && surface.grants.length > 0 && (
          <span className="ac-kv">grants: {surface.grants.join(', ')}</span>
        )}
        {vis?.roles && vis.roles.length > 0 && (
          <span className="ac-kv">roles: {vis.roles.join(', ')}</span>
        )}
        {vis?.userTypes && vis.userTypes.length > 0 && (
          <span className="ac-kv">user types: {vis.userTypes.join(', ')}</span>
        )}
      </div>
    </div>
  );
}
