import { FormEvent, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  clearIssuedDelegatedAccess,
  createDelegatedAccess,
  loadDelegatedAccess,
  revokeDelegatedAccess,
} from './delegatedAccessSlice';

const ttlOptions = [
  { value: 3600, label: '1 hour' },
  { value: 12 * 3600, label: '12 hours' },
  { value: 7 * 24 * 3600, label: '7 days' },
];

function formatDate(seconds?: number): string {
  if (!seconds) return '';
  try {
    return new Date(seconds * 1000).toLocaleString();
  } catch {
    return '';
  }
}

export function DelegatedAccessPanel() {
  const dispatch = useAppDispatch();
  const { platformUserId, items, grantOptions, resources, issuedToken, issuedHeader, issuedAccess, busy } = useAppSelector((s) => s.delegatedAccess);
  const [label, setLabel] = useState('Automation access');
  const [resourceGrants, setResourceGrants] = useState<Record<string, string[]>>({});
  const [ttlSeconds, setTtlSeconds] = useState(ttlOptions[0].value);
  const grantOptionByName = useMemo(
    () => new Map(grantOptions.map((item) => [item.grant, item])),
    [grantOptions],
  );
  const selectedResourceEntries = useMemo(
    () => Object.entries(resourceGrants).filter(([, grants]) => grants.length > 0),
    [resourceGrants],
  );
  const canSubmit = selectedResourceEntries.length > 0;

  const grantsForResource = (resource: typeof resources[number]): string[] => {
    const grants = resource.grants?.length
      ? resource.grants
      : Array.from(new Set((resource.operations || []).flatMap((operation) => operation.grants || [])));
    return grants.filter(Boolean);
  };

  const toggleResourceGrant = (resource: string, grant: string, checked: boolean) => {
    setResourceGrants((current) => {
      const next = { ...current };
      const existing = next[resource] || [];
      const updated = checked
        ? Array.from(new Set([...existing, grant]))
        : existing.filter((item) => item !== grant);
      if (updated.length) next[resource] = updated;
      else delete next[resource];
      return next;
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    await dispatch(createDelegatedAccess({
      label: label.trim() || 'Automation access',
      resourceGrants,
      ttlSeconds,
    })).unwrap().catch(() => undefined);
    void dispatch(loadDelegatedAccess());
  };

  const revoke = async (accessId: string) => {
    await dispatch(revokeDelegatedAccess({ accessId })).unwrap().catch(() => undefined);
    void dispatch(loadDelegatedAccess());
  };

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Delegated by KDCube</h2>
          {platformUserId ? <p className="muted">Platform user: <code>{platformUserId}</code></p> : null}
        </div>
      </div>

      <p className="muted">
        Access this user granted to external automations and clients — created
        here as a token, or approved through an OAuth connect (for example an
        MCP client). Revoking a grant stops that automation from calling KDCube.
      </p>

      {issuedToken ? (
        <div className="issued-token">
          <div className="issued-token-head">
            <div>
              <div className="form-title">New automation credential</div>
              <p className="muted">Copy this token now. It will not be shown again.</p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={() => dispatch(clearIssuedDelegatedAccess())}>
              Dismiss
            </button>
          </div>
          {issuedAccess ? (
            <div className="account-sub">
              {issuedAccess.label || issuedAccess.access_id} · expires {formatDate(issuedAccess.expires_at)}
            </div>
          ) : null}
          <textarea className="token-output" readOnly value={issuedHeader || `Bearer ${issuedToken}`} />
        </div>
      ) : null}

      <div className="form-title">Granted access</div>
      {items.length ? (
        <ul className="accounts">
          {items.map((item) => (
            <li className="account" key={item.access_id}>
              <div>
                <div className="account-title">
                  {item.label || item.access_id}
                  {item.source === 'oauth'
                    ? <span className="badge badge-ok">connected app</span>
                    : <span className="badge badge-warn">manual token</span>}
                </div>
                {item.client_id && item.client_id !== item.label ? <div className="account-sub">{item.client_id}</div> : null}
                {item.resource_grants && Object.keys(item.resource_grants).length ? (
                  <div className="account-sub">
                    Access: {Object.entries(item.resource_grants).map(([resource, grants]) => `${resource === '*' ? 'all resources' : resource} → ${grants.join(', ')}`).join('; ')}
                  </div>
                ) : null}
                {item.operations?.length ? <div className="account-sub">Operations: {item.operations.join(', ')}</div> : null}
                <div className="account-sub">
                  {item.source === 'oauth' ? 'Approved' : 'Created'} {formatDate(item.created_at) || 'unknown'}
                  {' · '}expires {formatDate(item.expires_at) || 'unknown'}
                  {item.last_four ? ` · token ends ${item.last_four}` : ''}
                </div>
              </div>
              <button className="btn btn-danger" type="button" disabled={busy} onClick={() => revoke(item.access_id)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          Nothing granted yet. Access appears here when you create a token below
          or approve an external client's OAuth connect.
        </p>
      )}

      <form className="form" onSubmit={submit}>
        <div className="form-title">Create automation access</div>
        <input
          className="input"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="display label"
        />
        {resources.length ? (
          <div className="resource-scope">
            <div className="form-title">Resources</div>
            <p className="muted">
              Select the grants inside every surface where this credential can be used.
            </p>
            <div className="resource-list">
              {resources.map((item) => {
                const grants = grantsForResource(item);
                return (
                  <div className="resource-option resource-option-stack" key={item.resource}>
                    <span>
                      <strong>
                        {item.label || item.resource}
                        {item.admin_only ? <span className="badge badge-admin">admin</span> : null}
                      </strong>
                      <small>{item.resource}</small>
                    </span>
                    <div className="resource-grants">
                      {grants.map((grant) => {
                        const option = grantOptionByName.get(grant);
                        return (
                          <label className="grant-chip" key={`${item.resource}:${grant}`}>
                            <input
                              type="checkbox"
                              checked={(resourceGrants[item.resource] || []).includes(grant)}
                              onChange={(event) => toggleResourceGrant(item.resource, grant, event.target.checked)}
                            />
                            <span>{option?.label || grant}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
        <select className="input" value={ttlSeconds} onChange={(event) => setTtlSeconds(Number(event.target.value))}>
          {ttlOptions.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>
        {!resources.length ? (
          <p className="muted">No delegable resources are configured.</p>
        ) : null}
        {resources.length && !canSubmit ? (
          <p className="muted">Select at least one resource grant.</p>
        ) : null}
        <button className="btn" type="submit" disabled={busy || !canSubmit}>
          Create automation access
        </button>
      </form>
    </section>
  );
}
