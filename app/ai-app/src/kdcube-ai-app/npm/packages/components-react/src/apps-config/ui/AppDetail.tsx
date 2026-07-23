/** Right pane: the selected app — identity header + as_provider + as_consumer. */
import { selectAppConfig, selectSelectedAppId } from '@kdcube/components-core/apps-config';
import { useAppsConfigSelector } from '../binding.tsx';
import { StatusNote } from './primitives/StatusNote.tsx';
import { Badge } from './primitives/Badge.tsx';
import { ProviderSurfaces } from './provider/ProviderSurfaces.tsx';
import { ConsumerOverview } from './consumer/ConsumerOverview.tsx';
import { Section } from './primitives/Section.tsx';
import { ConfigTree } from './primitives/ConfigTree.tsx';

export function AppDetail() {
  const selectedId = useAppsConfigSelector(selectSelectedAppId);
  const slot = useAppsConfigSelector(selectAppConfig);

  if (!selectedId) {
    return (
      <div className="ac-detail ac-detail--empty">
        <p className="ac-note ac-note--muted">Select an app to inspect its surfaces and agents.</p>
      </div>
    );
  }

  const view = slot.data;
  const ready = slot.status === 'ready' && !!view;

  return (
    <div className="ac-detail">
      <StatusNote status={slot.status} error={slot.error} loadingLabel="Loading app…" />
      {ready && (
        <>
          <header className="ac-detail__head">
            <div className="ac-detail__title">
              <h2>{view.app.name}</h2>
              {view.app.isDefault && <Badge tone="accent">default</Badge>}
              {view.app.origin && view.app.origin !== 'unknown' && (
                <Badge tone="muted">{view.app.origin}</Badge>
              )}
            </div>
            <div className="ac-detail__ids">
              <code>{view.app.bundleId}</code>
              {view.app.version && <span className="ac-detail__ver">{view.app.version}</span>}
            </div>
          </header>

          <ProviderSurfaces surfaces={view.provider} />
          <ConsumerOverview consumer={view.consumer} />
          <Section
            title="Configuration"
            hint="The full app config — everything else declared on this app (execution, telemetry, models, react/instructions, connections, integrations…)."
          >
            <ConfigTree value={view.config} omitKeys={['surfaces']} />
          </Section>
        </>
      )}
    </div>
  );
}
