import { useMemo, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { ProviderConnectCard, type ProviderDeepLink } from './ProviderConnectCard';

// Consent cards / claim-upgrade denials deep-link here:
// ?tab=provider_connections&provider=slack&tiers=read,write&account_id=… —
// land on the provider's card with the requested tiers preselected
// (reconnect mode when account_id names an existing account). Mirrors the
// delegatedToKdcube tab's URL-param parsing; unknown values degrade to the
// plain tab.
function providerDeepLinkFromLocation(): ProviderDeepLink {
  const params = new URLSearchParams(window.location.search);
  return {
    provider: params.get('provider') || '',
    tiers: (params.get('tiers') || '').split(',').map((item) => item.trim()).filter(Boolean),
    accountId: params.get('account_id') || '',
  };
}

// A runtime summon (scene surface command `connections.hub.open`) carries the
// same fields as the URL deep link plus a nonce; a fresh nonce remounts the
// targeted card so it re-seeds its tier/reconnect state and scrolls into view.
export interface ProviderSummon extends ProviderDeepLink {
  nonce: number;
}

// Provider connections tab: one connect card per connections-catalog provider
// (Slack, Gmail, …), sorted by label. Tokens land in the shared connection
// store the `connections` named service resolves from.
export function ProviderConnectionsPanel({ summon }: { summon?: ProviderSummon }) {
  const { providers, busy } = useAppSelector((s) => s.providerConnections);
  const [urlDeepLink] = useState<ProviderDeepLink>(providerDeepLinkFromLocation);
  const deepLink: ProviderDeepLink = summon ?? urlDeepLink;
  const summonNonce = summon?.nonce ?? 0;
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
          {rows.map((row) => {
            const targeted = deepLink.provider === row.provider;
            return (
              <ProviderConnectCard
                key={targeted ? `${row.provider}:${summonNonce}` : row.provider}
                row={row}
                busy={busy}
                deepLink={targeted ? deepLink : undefined}
              />
            );
          })}
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
