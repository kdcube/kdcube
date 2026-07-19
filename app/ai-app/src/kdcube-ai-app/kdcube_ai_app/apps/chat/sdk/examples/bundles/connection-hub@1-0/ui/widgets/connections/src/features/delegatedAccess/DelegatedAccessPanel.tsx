import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { subscribeConnectionHubEvents } from '../../api/dataBus';
import { DelegatedResourceCatalog, operationRows } from './DelegatedResourceCatalog';
import type {
  DelegatedAccessNamedServiceOperations,
  DelegatedAccessRecord,
  DelegatedAccessResourceOption,
  DelegatedToKdcubeAccount,
} from '../../api/types';
import {
  clearIssuedDelegatedAccess,
  createDelegatedAccess,
  grantAgentAccess,
  loadDelegatedAccess,
  revokeDelegatedAccess,
} from './delegatedAccessSlice';

/** Whether a resource card matches a catalog search: its label/id, its grants
 *  (tokens and their vocabulary labels), its operations, and its named-service
 *  namespaces/tools all count — matching keeps the WHOLE card so the row stays
 *  understandable in context. */
function resourceMatchesQuery(
  item: DelegatedAccessResourceOption,
  query: string,
  grantOptionByName: Map<string, { label?: string; description?: string } | undefined>,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack: string[] = [item.label || '', item.resource];
  (item.grants || []).forEach((grant) => {
    const option = grantOptionByName.get(grant);
    haystack.push(grant, option?.label || '', option?.description || '');
  });
  (item.operations || []).forEach((operation) => {
    haystack.push(operation.name, operation.label || '', operation.description || '', ...(operation.grants || []));
  });
  (item.named_services || []).forEach((ns) => {
    haystack.push(ns.namespace, ns.label || '', ns.description || '');
    Object.entries(ns.tools || {}).forEach(([tool, option]) => {
      haystack.push(tool, option.label || '', option.description || '', ...(option.grants || []));
    });
  });
  return haystack.some((text) => text.toLowerCase().includes(q));
}

/** Human parts of a `kdcube-agent:<app>:<agent>` client id — the agent and the
 *  app it lives in, version tag stripped from the app for display. */
function parseAgentClientId(clientId: string): { agent: string; app: string } | null {
  const parts = String(clientId || '').split(':');
  if (parts[0] !== 'kdcube-agent' || parts.length < 3) return null;
  const app = parts[1].replace(/@.+$/, '');
  return { agent: parts.slice(2).join(':'), app };
}

type PendingAgentGrant = { clientId: string; resource: string; claims: string[] };

function pendingAgentGrantFromParams(get: (key: string) => string): PendingAgentGrant | null {
  if (get('pending_agent_grant') !== '1') return null;
  const clientId = get('agent_client_id').trim();
  const resource = get('resource').trim();
  if (!clientId || !resource) return null;
  const claims = get('claims').split(',').map((item) => item.trim()).filter(Boolean);
  return { clientId, resource, claims };
}

/** The pending per-agent grant a chat consent card carries here — as the
 *  `connections.hub.open` command's params passed down as PROPS (an embedded
 *  frame may not allow URL mutation), or as `pending_agent_grant` URL params
 *  on a direct deep link. Props win. */
function pendingAgentGrantRequest(openParams?: Record<string, string>): PendingAgentGrant | null {
  if (openParams) {
    const fromProps = pendingAgentGrantFromParams((key) => String(openParams[key] ?? ''));
    if (fromProps) return fromProps;
  }
  try {
    const p = new URLSearchParams(window.location.search);
    return pendingAgentGrantFromParams((key) => p.get(key) ?? '');
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

export function DelegatedAccessPanel({ openParams }: { openParams?: Record<string, string> } = {}) {
  const dispatch = useAppDispatch();
  const { platformUserId, items, grantOptions, resources, issuedToken, issuedHeader, issuedAccess, busy } = useAppSelector((s) => s.delegatedAccess);
  const { providers, accounts } = useAppSelector((s) => s.delegatedToKdcube);
  const [label, setLabel] = useState('Automation access');
  const [resourceGrants, setResourceGrants] = useState<Record<string, string[]>>({});
  const [namedServiceOperations, setNamedServiceOperations] = useState<DelegatedAccessNamedServiceOperations>({});
  const [ttlSeconds, setTtlSeconds] = useState(ttlOptions[0].value);
  const [pendingGrant, setPendingGrant] = useState(() => pendingAgentGrantRequest(openParams));
  useEffect(() => {
    console.info(
      '[consent-route] pending pane state on mount:',
      pendingGrant ? JSON.stringify(pendingGrant) : 'NONE',
      'openParams=', openParams ? JSON.stringify(openParams) : 'NONE',
      'location.search=', window.location.search,
    );
    // Mount-time diagnostic only — the open command remounts this panel by key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Which of the ASKED claims the user keeps checked — the request is a
  // proposal, not a bundle: granting a subset is always allowed.
  const [pendingClaimPicks, setPendingClaimPicks] = useState<Record<string, boolean>>(
    () => Object.fromEntries((pendingAgentGrantRequest(openParams)?.claims || []).map((c) => [c, true])),
  );
  // Per-record EDIT state for granted agent rows: access_id being edited and
  // the checkbox set keyed `${resource}:${claim}`.
  const [editingAccessId, setEditingAccessId] = useState<string | null>(null);
  const [editPicks, setEditPicks] = useState<Record<string, boolean>>({});
  // Per-account claim binding being edited: {provider_id: {account_id: [claims]}}.
  // A provider left untouched means "any account"; once any account is ticked,
  // only the ticked accounts+claims are allowed.
  const [editAccountScope, setEditAccountScope] = useState<Record<string, Record<string, string[]>>>({});
  // Catalog search: narrows the delegable-resource cards (labels, grants,
  // named-service rows) wherever the shared list renders.
  const [resourceQuery, setResourceQuery] = useState('');
  // Accordion state per resource card. Undefined = derived default: open while
  // it matches an active search or already carries a selection, else closed —
  // the list reads as compact rows in the small pane, and only what the user
  // works with takes vertical space.
  const [openResources, setOpenResources] = useState<Record<string, boolean>>({});
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

  const pendingCheckedClaims = pendingGrant
    ? pendingGrant.claims.filter((claim) => pendingClaimPicks[claim] !== false)
    : [];

  const grantPending = async () => {
    if (!pendingGrant) return;
    // What the user KEPT CHECKED of the ask, PLUS anything else they picked
    // from the catalog below — merged per resource (one grant record per
    // resource, so the runtime's per-resource token lookup keys stay intact).
    const merged: Record<string, string[]> = {};
    if (pendingCheckedClaims.length) {
      merged[pendingGrant.resource] = [...pendingCheckedClaims];
    }
    selectedResourceEntries.forEach(([resource, grants]) => {
      const current = merged[resource] || [];
      merged[resource] = [...current, ...grants.filter((grant) => !current.includes(grant))];
    });
    let first = true;
    for (const [resource, claims] of Object.entries(merged)) {
      await dispatch(grantAgentAccess({
        clientId: pendingGrant.clientId,
        resource,
        claims,
        namedServiceOperations: namedServiceOperations[resource],
        // The account binding is per-client: send it once with the first grant.
        ...(first && Object.keys(pendingAccountScope).length ? { accountScope: pendingAccountScope } : {}),
      })).unwrap().catch(() => undefined);
      first = false;
    }
    setPendingGrant(null);
    setResourceGrants({});
    setPendingAccountScope({});
    setNamedServiceOperations({});
    void dispatch(loadDelegatedAccess());
  };

  const startEdit = (item: DelegatedAccessRecord) => {
    const picks: Record<string, boolean> = {};
    Object.entries(item.resource_grants || {}).forEach(([resource, grants]) => {
      grants.forEach((claim) => { picks[`${resource}:${claim}`] = true; });
    });
    setEditingAccessId(item.access_id);
    setEditPicks(picks);
    // Seed the per-account claim binding from the record. An account "*" (any
    // account) stays unbound in the editor; a claim "*" (any claim) materializes
    // into that account's own approved claims, all checked.
    const scope: Record<string, Record<string, string[]>> = {};
    Object.entries(item.account_scope || {}).forEach(([provider, accountsMap]) => {
      const providerAccounts = providersWithAccounts.get(provider) || [];
      const seeded: Record<string, string[]> = {};
      Object.entries(accountsMap || {}).forEach(([accountId, claims]) => {
        if (accountId === '*') return;
        const supported = providerAccounts.find((a) => a.account_id === accountId)?.claims || [];
        const list = (claims || []).includes('*')
          ? [...supported]
          : (claims || []).filter((claim) => supported.includes(claim));
        if (list.length) seeded[accountId] = list;
      });
      if (Object.keys(seeded).length) scope[provider] = seeded;
    });
    setEditAccountScope(scope);
  };
  // Toggle one claim on one account for a provider. An account with no claims
  // drops out; a provider with no bound accounts drops out (=> any account).
  const makeToggleAccountClaim = (
    setScope: React.Dispatch<React.SetStateAction<Record<string, Record<string, string[]>>>>,
  ) => (provider: string, accountId: string, claim: string, checked: boolean) => {
    setScope((current) => {
      const providerMap: Record<string, string[]> = { ...(current[provider] || {}) };
      const held = new Set(providerMap[accountId] || []);
      if (checked) held.add(claim); else held.delete(claim);
      if (held.size) providerMap[accountId] = Array.from(held); else delete providerMap[accountId];
      const next = { ...current };
      if (Object.keys(providerMap).length) next[provider] = providerMap; else delete next[provider];
      return next;
    });
  };
  const toggleEditAccount = makeToggleAccountClaim(setEditAccountScope);
  // Providers with at least one connected account — the account-binding editor
  // only shows a provider the user actually has accounts for.
  const providersWithAccounts = useMemo(() => {
    const byProvider = new Map<string, DelegatedToKdcubeAccount[]>();
    accounts.forEach((account) => {
      const list = byProvider.get(account.provider_id) || [];
      list.push(account);
      byProvider.set(account.provider_id, list);
    });
    return byProvider;
  }, [accounts]);
  // Human label for one connected account (falls back to the id).
  const accountLabelById = useMemo(() => {
    const map = new Map<string, string>();
    accounts.forEach((account) => {
      map.set(account.account_id, account.email || account.display_name || account.workspace || account.account_id);
    });
    return map;
  }, [accounts]);
  // Which provider account-lists are expanded (the "+ choose" disclosure).
  const [expandedAccountProviders, setExpandedAccountProviders] = useState<Record<string, boolean>>({});
  // Per-account claim binding chosen while granting a PENDING request (consent card).
  const [pendingAccountScope, setPendingAccountScope] = useState<Record<string, Record<string, string[]>>>({});
  const togglePendingAccount = makeToggleAccountClaim(setPendingAccountScope);

  // The per-account permission picker: a disclosure per provider showing
  // "<n>/<m> accounts" (or "any account" when unbound) so a large account list
  // stays legible; expanding shows EACH connected account with its own approved
  // claims as checkboxes — so "read+write on one account, read-only on another"
  // is picked here. Reused by the Edit blocks and the pending consent card.
  const renderAccountScopePicker = (
    scope: Record<string, Record<string, string[]>>,
    onToggle: (provider: string, accountId: string, claim: string, checked: boolean) => void,
    who: string,
  ) => {
    if (!providersWithAccounts.size) return null;
    return (
      <div style={{ marginTop: 8 }}>
        <div className="account-title">Which accounts and permissions may {who} use?</div>
        {Array.from(providersWithAccounts.entries()).map(([provider, providerAccounts]) => {
          const providerScope = scope[provider] || {};
          const boundCount = Object.keys(providerScope).filter((id) => id !== '*').length;
          const total = providerAccounts.length;
          const open = Boolean(expandedAccountProviders[provider]);
          return (
            <details
              key={provider}
              open={open}
              onToggle={(event) => setExpandedAccountProviders((current) => ({
                ...current, [provider]: (event.target as HTMLDetailsElement).open,
              }))}
            >
              <summary className="muted" style={{ cursor: 'pointer' }}>
                {providers[provider]?.label || provider}
                {' — '}
                {boundCount ? `${boundCount}/${total} accounts` : 'any account'}
                {open ? null : <span className="account-sub"> · + choose</span>}
              </summary>
              <div style={{ marginTop: 6 }}>
                {providerAccounts.map((account) => {
                  const held = new Set(providerScope[account.account_id] || []);
                  const supported = account.claims || [];
                  return (
                    <div key={account.account_id} style={{ marginTop: 6 }}>
                      <div className="account-sub">
                        {account.email || account.display_name || account.workspace || account.account_id}
                      </div>
                      {supported.length ? (
                        <div className="resource-grants">
                          {supported.map((claim) => (
                            <label className="grant-chip" key={claim} title={grantOptionByName.get(claim)?.label || undefined}>
                              <input
                                type="checkbox"
                                checked={held.has(claim)}
                                onChange={(event) => onToggle(provider, account.account_id, claim, event.target.checked)}
                              />
                              <span>{claim}</span>
                            </label>
                          ))}
                        </div>
                      ) : (
                        <div className="account-sub">No approved permissions on this account yet.</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          );
        })}
        <div className="account-sub" style={{ marginTop: 4 }}>
          Tick the permissions {who} may use on each account. Leave a provider untouched to allow any of its accounts.
        </div>
      </div>
    );
  };

  const saveEdit = async (item: DelegatedAccessRecord) => {
    if (!item.client_id) return;
    const entries = Object.entries(item.resource_grants || {});
    const kept: Record<string, string[]> = {};
    entries.forEach(([resource, grants]) => {
      kept[resource] = grants.filter((claim) => editPicks[`${resource}:${claim}`] !== false);
    });
    const anyKept = Object.values(kept).some((claims) => claims.length > 0);
    if (!anyKept) {
      // Removing everything is a revoke, not an edit.
      await dispatch(revokeDelegatedAccess({ accessId: item.access_id })).unwrap().catch(() => undefined);
    } else {
      // The account binding is a per-client (not per-resource) edit, so send it
      // once with the first resource; `replace` makes the submitted scope
      // authoritative (an unchecked provider clears its binding -> any account).
      const accountScope = editAccountScope;
      let first = true;
      for (const [resource, claims] of Object.entries(kept)) {
        if (!claims.length) continue;
        await dispatch(grantAgentAccess({
          clientId: item.client_id,
          resource,
          claims,
          replace: true,
          ...(first ? { accountScope } : {}),
        })).unwrap().catch(() => undefined);
        first = false;
      }
    }
    setEditingAccessId(null);
    setEditPicks({});
    setEditAccountScope({});
    void dispatch(loadDelegatedAccess());
  };

  // The full delegable catalog (resource -> grant chips -> named-service
  // operation rows), bound to the shared selection state. Rendered in the
  // manual create flow AND in the pending agent card's "add more" section.
  // The search narrows the CARDS; selections live outside the filter, so a
  // grant checked earlier stays selected while the user searches on.
  const visibleResources = resources.filter((item) => resourceMatchesQuery(item, resourceQuery, grantOptionByName));
  const searching = Boolean(resourceQuery.trim());
  const renderResourceList = () => (
    <div className="resource-list">
      <input
        className="input"
        type="search"
        value={resourceQuery}
        onChange={(event) => setResourceQuery(event.target.value)}
        placeholder="Search resources and access (e.g. memories, read, slack)"
        aria-label="Search delegable resources and access"
      />
      {searching && !visibleResources.length ? (
        <p className="muted">Nothing delegable matches “{resourceQuery.trim()}”.</p>
      ) : null}
      {visibleResources.map((item) => {
        const grants = grantsForResource(item);
        const selectedCount = (resourceGrants[item.resource] || []).length;
        const isOpen = openResources[item.resource] ?? (searching || selectedCount > 0);
        return (
          <div className="resource-option resource-option-stack" key={item.resource}>
            <button
              type="button"
              onClick={() => setOpenResources((current) => ({ ...current, [item.resource]: !isOpen }))}
              aria-expanded={isOpen}
              style={{
                display: 'flex', width: '100%', alignItems: 'baseline', gap: 8,
                background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                textAlign: 'left', font: 'inherit', color: 'inherit',
              }}
            >
              <span aria-hidden="true" className="muted">{isOpen ? '▾' : '▸'}</span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <strong>
                  {item.label || item.resource}
                  {item.admin_only ? <span className="badge badge-admin">admin</span> : null}
                </strong>
                {isOpen ? <small style={{ display: 'block' }}>{item.resource}</small> : null}
              </span>
              {selectedCount
                ? <span className="badge badge-ok">{selectedCount}/{grants.length} selected</span>
                : <span className="muted"><small>{grants.length} options</small></span>}
            </button>
            {isOpen ? (
              <>
                <div className="resource-grants">
                  {grants.map((grant) => {
                    const option = grantOptionByName.get(grant);
                    return (
                      <label className="grant-chip" key={`${item.resource}:${grant}`} title={option?.label || undefined}>
                        <input
                          type="checkbox"
                          checked={(resourceGrants[item.resource] || []).includes(grant)}
                          onChange={(event) => toggleResourceGrant(item.resource, grant, event.target.checked)}
                        />
                        <span>{grant}</span>
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
              </>
            ) : null}
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
    <section className="card card-attention">
      <div className="card-head">
        <div className="form-title">An agent is asking for your permission</div>
      </div>
      <p style={{ marginTop: 0 }}>
        {pendingAgent ? (
          <>The agent <strong>{pendingAgent.agent}</strong>
          {pendingAgent.app ? <> of the app <strong>{pendingAgent.app}</strong></> : null}</>
        ) : (
          <>The connected client <strong>{pendingGrant.clientId}</strong></>
        )} wants to
        act on your behalf on <strong>{pendingResourceLabel || 'this resource'}</strong>. It is asking for:
      </p>
      <ul className="accounts">
        {pendingGrant.claims.map((claim) => {
          const option = grantOptionByName.get(claim);
          return (
            <li className="account" key={claim}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'baseline', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={pendingClaimPicks[claim] !== false}
                  onChange={(event) => setPendingClaimPicks((current) => ({ ...current, [claim]: event.target.checked }))}
                />
                <div>
                  <div className="account-title"><code>{claim}</code></div>
                  {option?.label ? <div className="account-sub">{option.label}</div> : null}
                  {option?.description ? <div className="account-sub">{option.description}</div> : null}
                </div>
              </label>
            </li>
          );
        })}
      </ul>
      {renderAccountScopePicker(pendingAccountScope, togglePendingAccount, 'this agent')}
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
        <button
          className="btn"
          type="button"
          disabled={busy || (!pendingCheckedClaims.length && !selectedResourceEntries.length)}
          onClick={grantPending}
        >
          {pendingCheckedClaims.length < (pendingGrant.claims.length || 0) || selectedResourceEntries.length
            ? 'Grant selected access'
            : 'Grant access'}
        </button>
        <button
          className="btn"
          type="button"
          disabled={busy}
          onClick={() => { setPendingGrant(null); setPendingAccountScope({}); }}
        >
          Not now
        </button>
      </div>
    </section>
  ) : null;

  // One CARD PER AGENT: every record of the same agent client (a per-resource
  // grant) lists inside it as an individually revocable permission row — so
  // "what can lg-react do for me" reads in one place, and dropping one
  // permission never touches the others. Non-agent grants keep the flat rows.
  const agentGroups = new Map<string, DelegatedAccessRecord[]>();
  const otherItems: DelegatedAccessRecord[] = [];
  items.forEach((item) => {
    if (item.source === 'agent' && item.client_id) {
      const group = agentGroups.get(item.client_id) || [];
      group.push(item);
      agentGroups.set(item.client_id, group);
    } else {
      otherItems.push(item);
    }
  });
  const resourceLabelFor = (resource: string): string =>
    resources.find((r) => r.resource === resource)?.label || '';
  // The REAL consent is the claim token — that is what renders in the rows.
  const claimLabel = (claim: string): string => claim;

  const grantedPane = (
    <section className="card">
      <div className="card-head">
        <p className="muted" style={{ margin: 0 }}>
          Access this user granted to agents, automations, and external clients —
          each grant is per caller: an agent or connected app gets exactly what
          you approve here, nothing more. Edit narrows or extends it live;
          revoking stops that caller immediately.
        </p>
        {platformUserId ? <span className="badge badge-ok" title={platformUserId}>you</span> : null}
      </div>

      {agentGroups.size ? (
        <div>
          {Array.from(agentGroups.entries()).map(([clientId, records]) => {
            const who = parseAgentClientId(clientId);
            return (
              <div className="resource-option resource-option-stack" key={clientId}>
                <span>
                  <strong>
                    {who ? `${who.agent} · ${who.app}` : clientId}
                    <span className="badge badge-ok">agent</span>
                  </strong>
                  <small>{clientId}</small>
                </span>
                <ul className="accounts">
                  {records.map((item) => {
                    const editing = editingAccessId === item.access_id;
                    return (
                      <li className="account" key={item.access_id}>
                        <div>
                          {Object.entries(item.resource_grants || {}).map(([resource, grants]) => (
                            <div key={resource}>
                              <div className="account-title">{resourceLabelFor(resource) || resource}</div>
                              {editing ? (
                                <div className="resource-grants">
                                  {grants.map((claim) => (
                                    <label className="grant-chip" key={`${resource}:${claim}`} title={grantOptionByName.get(claim)?.label || undefined}>
                                      <input
                                        type="checkbox"
                                        checked={editPicks[`${resource}:${claim}`] !== false}
                                        onChange={(event) => setEditPicks((current) => ({
                                          ...current, [`${resource}:${claim}`]: event.target.checked,
                                        }))}
                                      />
                                      <span>{claim}</span>
                                    </label>
                                  ))}
                                </div>
                              ) : (
                                <div className="account-sub">{grants.map(claimLabel).join(', ')}</div>
                              )}
                              {resourceLabelFor(resource) ? <div className="account-sub"><code>{resource}</code></div> : null}
                            </div>
                          ))}
                          {item.named_service_operations && Object.keys(item.named_service_operations).length ? (
                            <div className="account-sub">
                              Named services: {Object.values(item.named_service_operations)
                                .flatMap((namespaces) => Object.entries(namespaces))
                                .map(([namespace, operations]) => `${namespace} (${operations.join(', ')})`)
                                .join('; ')}
                            </div>
                          ) : (
                            <div className="account-sub">
                              Operation scope: every operation these claims cover (no
                              per-operation narrowing was selected).
                            </div>
                          )}
                          {editing ? renderAccountScopePicker(editAccountScope, toggleEditAccount, 'this agent')
                            : (Object.keys(item.account_scope || {}).length ? (
                            <div className="account-sub">
                              Accounts: {Object.entries(item.account_scope || {})
                                .map(([provider, accountsMap]) => `${providers[provider]?.label || provider} → ${Object.entries(accountsMap || {}).map(([accountId, claims]) => `${accountId === '*' ? 'any account' : (accountLabelById.get(accountId) || accountId)} (${(claims || []).includes('*') ? 'all' : (claims || []).join(', ')})`).join(', ')}`)
                                .join('; ')}
                            </div>
                          ) : null)}
                          <div className="account-sub">
                            Granted {formatDate(item.created_at) || 'unknown'}
                            {' · '}expires {formatDate(item.expires_at) || 'unknown'}
                          </div>
                        </div>
                        <div className="row" style={{ flexDirection: 'column', gap: 6, alignItems: 'stretch' }}>
                          {editing ? (
                            <>
                              <button className="btn" type="button" disabled={busy} onClick={() => saveEdit(item)}>
                                Save
                              </button>
                              <button className="btn" type="button" disabled={busy} onClick={() => { setEditingAccessId(null); setEditPicks({}); setEditAccountScope({}); }}>
                                Cancel
                              </button>
                            </>
                          ) : (
                            <>
                              <button className="btn" type="button" disabled={busy} onClick={() => startEdit(item)}>
                                Edit
                              </button>
                              <button className="btn btn-danger" type="button" disabled={busy} onClick={() => revoke(item.access_id)}>
                                Revoke
                              </button>
                            </>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      ) : null}

      {otherItems.length ? (
        <ul className="accounts">
          {otherItems.map((item) => {
            // A connected external client (an OAuth app — Claude Code) is
            // editable in place: its card is the authority the guard resolves
            // live, so ticking/unticking claims narrows or extends what the
            // client may do on the bearer it already holds. Manual tokens keep
            // their own credential flow (only revoke).
            const editable = item.source === 'oauth' && Boolean(item.client_id);
            const editing = editable && editingAccessId === item.access_id;
            return (
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
                    editing ? (
                      Object.entries(item.resource_grants).map(([resource, grants]) => (
                        <div key={resource}>
                          <div className="account-title">{resource === '*' ? 'all resources' : (resourceLabelFor(resource) || resource)}</div>
                          <div className="resource-grants">
                            {grants.map((claim) => (
                              <label className="grant-chip" key={`${resource}:${claim}`} title={grantOptionByName.get(claim)?.label || undefined}>
                                <input
                                  type="checkbox"
                                  checked={editPicks[`${resource}:${claim}`] !== false}
                                  onChange={(event) => setEditPicks((current) => ({
                                    ...current, [`${resource}:${claim}`]: event.target.checked,
                                  }))}
                                />
                                <span>{claim}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="account-sub">
                        Access: {Object.entries(item.resource_grants).map(([resource, grants]) => `${resource === '*' ? 'all resources' : resource} → ${grants.join(', ')}`).join('; ')}
                      </div>
                    )
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
                  {editing ? renderAccountScopePicker(editAccountScope, toggleEditAccount, 'this app')
                    : (Object.keys(item.account_scope || {}).length ? (
                    <div className="account-sub">
                      Accounts: {Object.entries(item.account_scope || {})
                        .map(([provider, accountsMap]) => `${providers[provider]?.label || provider} → ${Object.entries(accountsMap || {}).map(([accountId, claims]) => `${accountId === '*' ? 'any account' : (accountLabelById.get(accountId) || accountId)} (${(claims || []).includes('*') ? 'all' : (claims || []).join(', ')})`).join(', ')}`)
                        .join('; ')}
                    </div>
                  ) : null)}
                  <div className="account-sub">
                    {item.source === 'oauth' ? 'Approved' : 'Created'} {formatDate(item.created_at) || 'unknown'}
                    {' · '}expires {formatDate(item.expires_at) || 'unknown'}
                    {item.last_four ? ` · token ends ${item.last_four}` : ''}
                  </div>
                </div>
                <div className="row" style={{ flexDirection: 'column', gap: 6, alignItems: 'stretch' }}>
                  {editing ? (
                    <>
                      <button className="btn" type="button" disabled={busy} onClick={() => saveEdit(item)}>
                        Save
                      </button>
                      <button className="btn" type="button" disabled={busy} onClick={() => { setEditingAccessId(null); setEditPicks({}); setEditAccountScope({}); }}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      {editable ? (
                        <button className="btn" type="button" disabled={busy} onClick={() => startEdit(item)}>
                          Edit
                        </button>
                      ) : null}
                      <button className="btn btn-danger" type="button" disabled={busy} onClick={() => revoke(item.access_id)}>
                        Revoke
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}

      {!items.length ? (
        <p className="muted">
          Nothing granted yet. Access appears here when an agent asks and you
          approve, when you create an automation token, or when you approve an
          external client's OAuth connect.
        </p>
      ) : null}
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
        ...(pendingGrantPane ? [{
          id: 'pending-grant',
          // The claims ride the pane title so the ask reads from the bar alone.
          title: `Agent access request — ${pendingGrant?.claims.join(', ') || ''}`,
          content: pendingGrantPane,
          // The request is THE pending action: it leads the tab — full-row,
          // generous height, claims and Grant never below the fold — while
          // Granted access and Create stay visible beneath.
          lead: true,
        }] : []),
        { id: 'granted', title: 'Granted access', content: grantedPane },
        { id: 'create', title: 'Create automation access', content: createPane },
      ]}
    />
  );
}
