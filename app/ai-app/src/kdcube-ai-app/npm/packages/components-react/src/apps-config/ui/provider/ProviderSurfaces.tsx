/** The as_provider section: surfaces the app exposes, grouped by kind. */
import type { ProviderSurface, ProviderSurfaceKind } from '@kdcube/components-core/apps-config';
import { Section } from '../primitives/Section.tsx';
import { SurfaceCard } from './SurfaceCard.tsx';

const KIND_ORDER: ProviderSurfaceKind[] = ['bundle', 'api', 'mcp', 'widget'];
const KIND_LABEL: Record<ProviderSurfaceKind, string> = {
  bundle: 'Chat surface',
  api: 'APIs',
  mcp: 'MCP doors',
  widget: 'Widgets',
};

export function ProviderSurfaces({ surfaces }: { surfaces: ProviderSurface[] }) {
  return (
    <Section
      title="Provider surfaces"
      count={surfaces.length}
      hint="What this app exposes to the platform (as provider): chat, APIs, MCP doors, widgets."
    >
      {surfaces.length === 0 ? (
        <p className="ac-note ac-note--muted">This app exposes no inbound surfaces.</p>
      ) : (
        KIND_ORDER.map((kind) => {
          const group = surfaces.filter((s) => s.kind === kind);
          if (group.length === 0) return null;
          return (
            <div key={kind} className="ac-surfacegroup">
              <h4 className="ac-surfacegroup__title">{KIND_LABEL[kind]}</h4>
              <div className="ac-surfacegroup__items">
                {group.map((s, i) => (
                  <SurfaceCard key={`${s.kind}:${s.alias}:${i}`} surface={s} />
                ))}
              </div>
            </div>
          );
        })
      )}
    </Section>
  );
}
