import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppDispatch } from '../../app/hooks';
import { AccountRow } from '../../components/AccountRow';
import type { ConnectionsAccount, ConnectionsClaimTier, ConnectionsProviderRow } from '../../api/types';
import {
  disconnectProviderConnection,
  loadProviderConnections,
  startProviderConnectionsOAuth,
} from './providerConnectionsSlice';

// Parsed ?provider=…&tiers=…&account_id=… deep link (see the panel); the
// panel hands it only to the matching provider's card.
export interface ProviderDeepLink {
  provider: string;
  tiers: string[];
  accountId: string;
}

function providerLabel(row: ConnectionsProviderRow): string {
  return row.label || row.provider;
}

function tierLabel(tier: ConnectionsClaimTier): string {
  return tier.label || tier.id;
}

function accountTitle(account: ConnectionsAccount): string {
  return account.display_name || account.email || account.workspace || account.account_id;
}

function accountSubtitle(account: ConnectionsAccount): string | undefined {
  const bits = [account.workspace, account.email].filter(
    (bit): bit is string => Boolean(bit && bit !== account.display_name),
  );
  return bits.length ? bits.join(' · ') : undefined;
}

function heldTierIds(account: ConnectionsAccount, tiers: ConnectionsClaimTier[]): string[] {
  return tiers.filter((tier) => account.tier_coverage?.[tier.id]).map((tier) => tier.id);
}

// One provider connect card: claim-tier picker (when the provider declares
// tiers) + Connect, and the user's connected accounts with reconnect (held
// tiers stay granted; checked tiers ride along) and disconnect. A deep link
// scrolls the card into view and preselects its tiers/account.
export function ProviderConnectCard({
  row,
  busy,
  deepLink,
}: {
  row: ConnectionsProviderRow;
  busy: boolean;
  deepLink?: ProviderDeepLink;
}) {
  const dispatch = useAppDispatch();
  const label = providerLabel(row);
  const tiers = useMemo<ConnectionsClaimTier[]>(() => row.claim_tiers ?? [], [row]);
  const apps = useMemo(() => (row.apps ?? []).filter((app) => app.enabled !== false), [row]);
  const accounts = row.accounts ?? [];

  // Deep-link seeding (mount-time): only tiers this provider declares count,
  // and account_id must name one of the user's accounts — anything else
  // degrades to the plain card.
  const declaredIds = tiers.map((tier) => tier.id);
  const requestedTiers = (deepLink?.tiers ?? []).filter((id) => declaredIds.includes(id));
  const deepLinkAccount = deepLink?.accountId
    ? accounts.find((account) => account.account_id === deepLink.accountId) ?? null
    : null;

  const [appId, setAppId] = useState('');
  const selectedAppId = appId || apps[0]?.app_id || '';
  // Reconnect targets an existing account: its held tiers stay granted, the
  // checked ones are added on top.
  const [reconnectAccount, setReconnectAccount] = useState<ConnectionsAccount | null>(deepLinkAccount);
  // Connect default: the requested (deep-linked) tiers, else the first tier
  // (the read tier) checked. In reconnect mode `checked` holds only the tiers
  // being added; held tiers ride along.
  const [checked, setChecked] = useState<string[]>(() => {
    if (deepLinkAccount) return requestedTiers;
    if (requestedTiers.length) return requestedTiers;
    return tiers[0]?.id ? [tiers[0].id] : [];
  });

  // Deep-linked users land straight on this card.
  const cardRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (deepLink) cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const held = reconnectAccount ? heldTierIds(reconnectAccount, tiers) : [];
  const submitted = useMemo(() => {
    const union = [...held];
    for (const id of checked) {
      if (!union.includes(id)) union.push(id);
    }
    return union;
  }, [held, checked]);

  const canConnect = Boolean(row.configured && apps.length && (tiers.length === 0 || submitted.length > 0));

  const toggleTier = (tierId: string) => {
    if (held.includes(tierId)) return; // granted tiers stay in the request
    setChecked((current) => (
      current.includes(tierId) ? current.filter((item) => item !== tierId) : [...current, tierId]
    ));
  };

  const connect = async () => {
    const result = await dispatch(startProviderConnectionsOAuth({
      provider: row.provider,
      appId: apps.length > 1 ? selectedAppId : undefined,
      tiers: tiers.length ? submitted : undefined,
    })).unwrap().catch(() => undefined);
    if (result?.authorize_url) {
      // Arms the one-shot focus refresh in App: when the user returns from
      // the provider tab, the widget re-fetches once (no standing polling).
      try {
        sessionStorage.setItem('kdc-oauth-pending', '1');
      } catch {
        // Storage unavailable: the user can refresh manually.
      }
      window.open(result.authorize_url, '_blank', 'noopener,noreferrer');
    }
  };

  const beginReconnect = (account: ConnectionsAccount) => {
    setReconnectAccount(account);
    setChecked([]);
  };

  const resetToConnect = () => {
    setReconnectAccount(null);
    setChecked(tiers[0]?.id ? [tiers[0].id] : []);
  };

  const disconnect = (accountId: string) => {
    void dispatch(disconnectProviderConnection({ provider: row.provider, accountId }))
      .then(() => dispatch(loadProviderConnections()));
  };

  return (
    <div className="integration-provider" ref={cardRef}>
      <div className="integration-provider-head">
        <div>
          <div className="account-title">{label}</div>
          {reconnectAccount ? (
            <div className="account-sub">
              Reconnecting {accountTitle(reconnectAccount)} — granted tiers stay granted; check a tier to add it,
              then approve on {label}'s page.
            </div>
          ) : (
            <div className="account-sub">
              {tiers.length
                ? `Check the access tiers this connect should grant, then approve on ${label}'s page.`
                : `Approve the connection on ${label}'s page; the account appears below.`}
            </div>
          )}
        </div>
        {row.configured && apps.length ? (
          <div className="oauth-connect">
            {apps.length > 1 ? (
              <select
                className="input input-inline"
                value={selectedAppId}
                onChange={(event) => setAppId(event.target.value)}
                disabled={busy}
              >
                {apps.map((app) => (
                  <option key={app.app_id} value={app.app_id}>
                    {app.label || app.app_id}
                  </option>
                ))}
              </select>
            ) : null}
            <button className="btn" type="button" disabled={busy || !canConnect} onClick={() => void connect()}>
              {reconnectAccount ? `Reconnect ${label}` : `Connect ${label}`}
            </button>
            {reconnectAccount ? (
              <button className="btn btn-ghost" type="button" disabled={busy} onClick={resetToConnect}>
                Connect another account
              </button>
            ) : null}
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            Available once an operator configures a {label} client app.
          </p>
        )}
      </div>

      {row.configured && apps.length && tiers.length ? (
        <div className="tier-list">
          {tiers.map((tier) => {
            const granted = held.includes(tier.id);
            return (
              <label className="tier-item" key={tier.id}>
                <input
                  type="checkbox"
                  checked={granted || checked.includes(tier.id)}
                  onChange={() => toggleTier(tier.id)}
                  disabled={busy || granted}
                />
                <span className="tier-body">
                  <span className="tier-name">
                    {tierLabel(tier)}
                    {granted ? <span className="tier-granted">granted</span> : null}
                  </span>
                  {tier.description ? <span className="tier-desc">{tier.description}</span> : null}
                </span>
              </label>
            );
          })}
        </div>
      ) : null}

      {accounts.length ? (
        <ul className="accounts">
          {accounts.map((account) => {
            const connected = account.has_token !== false;
            const grantedLabels = tiers
              .filter((tier) => account.tier_coverage?.[tier.id])
              .map((tier) => tierLabel(tier));
            return (
              <AccountRow
                key={account.account_id}
                title={accountTitle(account)}
                subtitle={accountSubtitle(account)}
                statusLabel={connected ? 'connected' : 'reconnect required'}
                statusTone={connected ? 'ok' : 'error'}
                detail={tiers.length && grantedLabels.length ? `Granted: ${grantedLabels.join(' · ')}` : undefined}
                highlighted={deepLink?.accountId === account.account_id}
                busy={busy}
                actions={tiers.length ? (
                  <button
                    className={connected ? 'btn btn-ghost' : 'btn'}
                    type="button"
                    disabled={busy}
                    onClick={() => beginReconnect(account)}
                  >
                    Reconnect
                  </button>
                ) : undefined}
                onDisconnect={() => disconnect(account.account_id)}
              />
            );
          })}
        </ul>
      ) : row.configured && apps.length ? (
        <p className="muted">Accounts you connect appear here.</p>
      ) : null}
    </div>
  );
}
