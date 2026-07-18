import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { subscribeConnectionHubEvents } from '../../api/dataBus';
import { DelegatedResourceCatalog, operationRows } from './DelegatedResourceCatalog';
import type {
  DelegatedAccessNamedServiceOperations,
  DelegatedAccessResourceOption,
} from '../../api/types';
import {
  clearIssuedDelegatedAccess,
  createDelegatedAccess,
  grantAgentAccess,
  loadDelegatedAccess,
  revokeDelegatedAccess,
} from './delegatedAccessSlice';

/** Human parts of a `kdcube-agent:<app>:<agent>` client id — the agent and the
 *  app it lives in, version tag stripped from the app for display. */
function parseAgentClientId(clientId: string): { agent: string; app: string } | null {
  const parts = String(clientId || '').split(':');
  if (parts[0] !== 'kdcube-agent' || parts.length < 3) return null;
  const app = parts[1].replace(/@.+$/, '');
  return { agent: parts.slice(2).join(':'), app };
}

/** The pending per-agent grant a chat consent banner deep-links here (the
 *  `pending_agent_grant` params), so this panel offers a one-click grant. */
function pendingAgentGrantFromLocation(): { clientId: string; resource: string; claims: string[] } | null {
  try {
    const p = new URLSearchParams(window.location.search);
    if (p.get('pending_agent_grant') !== '1') return null;
    const clientId = (p.get('agent_client_id') || '').trim();
    const resource = (p.get('resource') || '').trim();
    if (!clientId || !resource) return null;
    const claims = (p.get('claims') || '').split(',').map((item) => item.trim()).filter(Boolean);
    return { clientId, resource, claims };
  } catch {
    return null;
  }
}

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

function commonOperationGrants(resource: DelegatedAccessResourceOption): string[] {
  const operations = resource.operations || [];
  if (!operations.length) return [];
  const [first, ...rest] = operations.map((operation) => new Set(operation.grants || []));
  return Array.from(first).filter((grant) => rest.every((grants) => grants.has(grant)));
}

export function DelegatedAccessPanel() {
  const dispatch = useAppDispatch();
  const { platformUserId, items, grantOptions, resources, issuedToken, issuedHeader, issuedAccess, busy } = useAppSelector((s) => s.delegatedAccess);
  const { providers, accounts } = useAppSelector((s) => s.delegatedToKdcube);
  const [label, setLabel] = useState('Automation access');
  const [resourceGrants, setResourceGrants] = useState<Record<string, string[]>>({});
  const [namedServiceOperations, setNamedServiceOperations] = useState<DelegatedAccessNamedServiceOperations>({});
  const [ttlSeconds, setTtlSeconds] = useState(ttlOptions[0].value);
  const [pendingGrant, setPendingGrant] = useState(pendingAgentGrantFromLocation);
  const grantOptionByName = useMemo(
    () => new Map(grantOptions.map((item) => [item.grant, item])),
    [grantOptions],
  );
  const selectedResourceEntries = useMemo(
    () => Object.entries(resourceGrants).filter(([, grants]) => grants.length > 0),
    [resourceGrants],
  );
  const canSubmit = selectedResourceEntries.length > 0;

  // Live delivery: a grant can land out-of-band (an OAuth consent completing
  // in another tab/app) or be revoked elsewhere — refetch when the registry
  // announces a change for this user over the data bus.
  useEffect(() => {
    return subscribeConnectionHubEvents((event) => {
      if (event.type !== 'connection_hub.delegated_access.changed') return;
      void dispatch(loadDelegatedAccess());
    });
  }, [dispatch]);

  const grantsForResource = (resource: DelegatedAccessResourceOption): string[] => {
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
    if (!checked) {
      const resourceOption = resources.find((item) => item.resource === resource);
      setNamedServiceOperations((current) => {
        const existingNamespaces = current[resource];
        if (!existingNamespaces || !resourceOption) return current;
        const nextNamespaces: Record<string, string[]> = {};
        const removesSurfaceAccess = commonOperationGrants(resourceOption).includes(grant);
        (resourceOption.named_services || []).forEach((namespace) => {
          const disallowed = new Set(
            operationRows(namespace)
              .filter((row) => removesSurfaceAccess || row.grants.includes(grant))
              .map((row) => row.operation),
          );
          const remaining = (existingNamespaces[namespace.namespace] || [])
            .filter((operation) => !disallowed.has(operation));
          if (remaining.length) nextNamespaces[namespace.namespace] = remaining;
        });
        const next = { ...current };
        if (Object.keys(nextNamespaces).length) next[resource] = nextNamespaces;
        else delete next[resource];
        return next;
      });
    }
  };

  const toggleNamedServiceOperation = (
    resource: string,
    namespace: string,
    operation: string,
    grants: string[],
    checked: boolean,
  ) => {
    if (checked) {
      const resourceOption = resources.find((item) => item.resource === resource);
      const requiredGrants = [
        ...grants,
        ...(resourceOption ? commonOperationGrants(resourceOption) : []),
      ];
      setResourceGrants((current) => ({
        ...current,
        [resource]: Array.from(new Set([...(current[resource] || []), ...requiredGrants])),
      }));
    }
    setNamedServiceOperations((current) => {
      const next = { ...current };
      const namespaces = { ...(next[resource] || {}) };
      const existing = namespaces[namespace] || [];
      const updated = checked
        ? Array.from(new Set([...existing, operation]))
        : existing.filter((item) => item !== operation);
      if (updated.length) namespaces[namespace] = updated;
      else delete namespaces[namespace];
      if (Object.keys(namespaces).length) next[resource] = namespaces;
      else delete next[resource];
      return next;
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const selectedNamedServiceOperations = Object.fromEntries(
      selectedResourceEntries.map(([resource]) => [
        resource,
        namedServiceOperations[resource] || {},
      ]),
    );
    await dispatch(createDelegatedAccess({
      label: label.trim() || 'Automation access',
      resourceGrants,
      namedServiceOperations: selectedNamedServiceOperations,
      ttlSeconds,
    })).unwrap().catch(() => undefined);
    void dispatch(loadDelegatedAccess());
  };

  const revoke = async (accessId: string) => {
    await dispatch(revokeDelegatedAccess({ accessId })).unwrap().catch(() => undefined);
    void dispatch(loadDelegatedAccess());
  };

  const grantPending = async () => {
    if (!pendingGrant) return;
    // What's in hand right now, PLUS anything else the user picked from the
    // catalog below — merged per resource (one grant record per resource, so
    // the runtime's per-resource token lookup keys stay intact).
    const merged: Record<string, string[]> = {
      [pendingGrant.resource]: [...pendingGrant.claims],
    };
    selectedResourceEntries.forEach(([resource, grants]) => {
      const current = merged[resource] || [];
      merged[resource] = [...current, ...grants.filter((grant) => !current.includes(grant))];
    });
    for (const [resource, claims] of Object.entries(merged)) {
      await dispatch(grantAgentAccess({
        clientId: pendingGrant.clientId,
        resource,
        claims,
        namedServiceOperations: namedServiceOperations[resource],
      })).unwrap().catch(() => undefined);
    }
    setPendingGrant(null);
    setResourceGrants({});
    setNamedServiceOperations({});
    void dispatch(loadDelegatedAccess());
  };

  // The full delegable catalog (resource -> grant chips -> named-service
  // operation rows), bound to the shared selection state. Rendered in the
  // manual create flow AND in the pending agent card's "add more" section.
  const renderResourceList = () => (
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
            <DelegatedResourceCatalog
              resource={item}
              selectedGrants={resourceGrants[item.resource] || []}
              selectedOperations={namedServiceOperations[item.resource] || {}}
              onOperationChange={(namespace, operation, operationGrants, checked) => (
                toggleNamedServiceOperation(
                  item.resource,
                  namespace,
                  operation,
                  operationGrants,
                  checked,
                )
              )}
              providers={providers}
              accounts={accounts}
            />
          </div>
        );
      })}
    </div>
  );

  // The landing view must explain itself: WHO asks (the agent, in words),
  // WHAT exactly (each claim with its grant-vocabulary label), ON WHAT (the
  // resource's configured label), and what granting means. Raw identifiers
  // demote to small code hints.
  const pendingAgent = pendingGrant ? parseAgentClientId(pendingGrant.clientId) : null;
  const pendingResourceLabel = pendingGrant
    ? (resources.find((r) => r.resource === pendingGrant.resource)?.label || '')
    : '';
  const pendingGrantPane = pendingGrant ? (
    <section className="card">
      <div className="card-head">
        <div className="form-title">An agent is asking for your permission</div>
      </div>
      <p style={{ marginTop: 0 }}>
        The agent <strong>{pendingAgent?.agent || 'agent'}</strong>
        {pendingAgent?.app ? <> of the app <strong>{pendingAgent.app}</strong></> : null} wants to
        act on your behalf on <strong>{pendingResourceLabel || 'this resource'}</strong>. It is asking for:
      </p>
      <ul className="accounts">
        {pendingGrant.claims.map((claim) => {
          const option = grantOptionByName.get(claim);
          return (
            <li className="account" key={claim}>
              <div>
                <div className="account-title">{option?.label || claim}</div>
                {option?.description ? <div className="account-sub">{option.description}</div> : null}
                <div className="account-sub"><code>{claim}</code></div>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="muted">
        Granting lets exactly this agent do exactly this for you — nothing else.
        The grant appears under Granted access below, where you can revoke it at
        any time; revocation is immediate.
      </p>
      <div className="account-sub" style={{ marginBottom: 12 }}>
        <code>{pendingGrant.clientId}</code>{' → '}<code>{pendingGrant.resource}</code>
      </div>
      {resources.length ? (
        <details style={{ marginBottom: 12 }}>
          <summary className="muted" style={{ cursor: 'pointer' }}>
            Give this agent more access (optional) — pick from anything delegable here
          </summary>
          <div style={{ marginTop: 8 }}>
            {renderResourceList()}
          </div>
        </details>
      ) : null}
      <div className="row">
        <button className="btn" type="button" disabled={busy} onClick={grantPending}>
          {selectedResourceEntries.length ? 'Grant requested + selected access' : 'Grant access'}
        </button>
        <button className="btn" type="button" disabled={busy} onClick={() => setPendingGrant(null)}>
          Not now
        </button>
      </div>
    </section>
  ) : null;

  const grantedPane = (
    <section className="card">
      <div className="card-head">
        <p className="muted" style={{ margin: 0 }}>
          Access this user granted to external automations and clients. Revoking
          a grant stops that automation from calling KDCube.
        </p>
        {platformUserId ? <span className="badge badge-ok" title={platformUserId}>you</span> : null}
      </div>

      {items.length ? (
        <ul className="accounts">
          {items.map((item) => (
            <li className="account" key={item.access_id}>
              <div>
                <div className="account-title">
                  {item.label || item.access_id}
                  {item.source === 'oauth'
                    ? <span className="badge badge-ok">connected app</span>
                    : item.source === 'agent'
                      ? <span className="badge badge-ok">agent</span>
                      : <span className="badge badge-warn">manual token</span>}
                </div>
                {item.client_id && item.client_id !== item.label ? <div className="account-sub">{item.client_id}</div> : null}
                {item.resource_grants && Object.keys(item.resource_grants).length ? (
                  <div className="account-sub">
                    Access: {Object.entries(item.resource_grants).map(([resource, grants]) => `${resource === '*' ? 'all resources' : resource} → ${grants.join(', ')}`).join('; ')}
                  </div>
                ) : null}
                {item.operations?.length ? <div className="account-sub">Operations: {item.operations.join(', ')}</div> : null}
                {item.named_service_operations && Object.keys(item.named_service_operations).length ? (
                  <div className="account-sub">
                    Named services: {Object.values(item.named_service_operations)
                      .flatMap((namespaces) => Object.entries(namespaces))
                      .map(([namespace, operations]) => `${namespace} (${operations.join(', ')})`)
                      .join('; ')}
                  </div>
                ) : null}
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
          Nothing granted yet. Access appears here when you create an automation
          token or approve an external client's OAuth connect.
        </p>
      )}
    </section>
  );

  const createPane = (
    <section className="card">
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

      <form className="form form-flush" onSubmit={submit}>
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
            {renderResourceList()}
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

  return (
    <PaneGroup
      panes={[
        ...(pendingGrantPane ? [{ id: 'pending-grant', title: 'Agent access request', content: pendingGrantPane }] : []),
        { id: 'granted', title: 'Granted access', content: grantedPane },
        { id: 'create', title: 'Create automation access', content: createPane },
      ]}
    />
  );
}
