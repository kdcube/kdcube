import { useMemo } from 'react';
import { useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { ProviderConnectCard } from './ProviderConnectCard';

// Provider connections tab: one connect card per connections-catalog provider
// (Slack, Gmail, …), sorted by label. Tokens land in the shared connection
// store the `connections` named service resolves from.
export function ProviderConnectionsPanel() {
  const { providers, busy } = useAppSelector((s) => s.providerConnections);
  const rows = useMemo(
    () => providers.slice().sort((a, b) => (a.label || a.provider).localeCompare(b.label || b.provider)),
    [providers],
  );

  const pane = (
    <section className="card">
      <div className="card-head">
        <p className="muted" style={{ margin: 0 }}>
          Provider accounts connected through KDCube's connector apps. Each
          connect grants exactly the access tiers you check; reconnect an
          account to add tiers.
        </p>
        <span className="badge badge-ok">{rows.length} providers</span>
      </div>
      {rows.length ? (
        <div className="integration-provider-list">
          {rows.map((row) => (
            <ProviderConnectCard key={row.provider} row={row} busy={busy} />
          ))}
        </div>
      ) : (
        <p className="muted">Providers appear here once this environment configures them.</p>
      )}
    </section>
  );

  return (
    <PaneGroup
      panes={[{ id: 'providers', title: 'Provider accounts', content: pane }]}
    />
  );
}
