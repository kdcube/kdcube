import { FormEvent, Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { operationUrl } from '../../api/client';
import { subscribeConnectionHubEvents } from '../../api/dataBus';
import { DelegatedResourceCatalog, operationRows } from './DelegatedResourceCatalog';
import type {
  DelegatedAccessNamedServiceOperations,
  DelegatedAccessRecord,
  DelegatedAccessResourceOption,
  DelegatedAccessStoredNamedServices,
  DelegatedCatalogDrift,
  DelegatedToKdcubeAccount,
} from '../../api/types';
import {
  clearIssuedDelegatedAccess,
  createDelegatedAccess,
  grantAgentAccess,
  loadDelegatedAccess,
  revokeDelegatedAccess,
  updateDelegatedAccess,
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

type PendingAgentGrant = {
  clientId: string;
  resource: string;
  claims: string[];
  // A per-account ask (the account can do it, this agent is not bound): the
  // exact account + claim to tick, so the card names it and opens the provider.
  // The user still ticks the checkbox explicitly; the picker is default-closed.
  accountId?: string;
  accountClaim?: string;
  // The inner capability the refused call wanted. Approval grants this one
  // operation, not everything its claims allow.
  namespace?: string;
  operation?: string;
};

function pendingAgentGrantFromParams(get: (key: string) => string): PendingAgentGrant | null {
  if (get('pending_agent_grant') !== '1') return null;
  const clientId = get('agent_client_id').trim();
  const resource = get('resource').trim();
  if (!clientId || !resource) return null;
  const claims = get('claims').split(',').map((item) => item.trim()).filter(Boolean);
  const accountId = get('account_id').trim();
  const accountClaim = get('account_claim').trim();
  const namespace = get('namespace').trim();
  const operation = get('operation').trim();
  return {
    clientId,
    resource,
    claims,
    accountId: accountId || undefined,
    accountClaim: accountClaim || undefined,
    namespace: namespace || undefined,
    operation: operation || undefined,
  };
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

type ManualAccessFocus = {
  accessId: string;
  resource?: string;
  claims: string[];
  accountId?: string;
  accountClaim?: string;
};

function manualAccessFocusFromParams(get: (key: string) => string): ManualAccessFocus | null {
  const accessId = get('manual_access_id').trim();
  if (!accessId) return null;
  const resource = get('resource').trim();
  const claims = get('claims').split(',').map((item) => item.trim()).filter(Boolean);
  const accountId = get('account_id').trim();
  const accountClaim = get('account_claim').trim();
  return {
    accessId,
    resource: resource || undefined,
    claims,
    accountId: accountId || undefined,
    accountClaim: accountClaim || undefined,
  };
}

function manualAccessFocusRequest(openParams?: Record<string, string>): ManualAccessFocus | null {
  if (openParams) {
    const fromProps = manualAccessFocusFromParams((key) => String(openParams[key] ?? ''));
    if (fromProps) return fromProps;
  }
  try {
    const params = new URLSearchParams(window.location.search);
    return manualAccessFocusFromParams((key) => params.get(key) ?? '');
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

/** A DCR client registers one fixed name — every Claude Code connector arrives
 *  as "Claude". The door it is connected to is what tells two connections
 *  apart, so the card title carries a short door alias derived from the
 *  grant's resource (".../mcp/productivity" -> "productivity"). */
function doorAlias(resource?: string): string {
  if (!resource) return '';
  const path = resource.replace(/[?#].*$/, '').replace(/\*+$/, '').replace(/\/+$/, '');
  const match = path.match(/\/mcp\/([^/]+)$/);
  return match ? match[1] : '';
}

/** How many granted-access cards render before "show more". */
const GRANT_PAGE_SIZE = 5;

/** One labelled row of a grant card: small-caps key on the left, value right. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span className="card-field-label">{label}</span>
      <span className="card-field-value">{children}</span>
    </>
  );
}

/** Read-only claim/permission tokens. */
function ChipRow({ entries, title }: { entries: string[]; title?: (entry: string) => string | undefined }) {
  return (
    <span className="chip-row">
      {entries.map((entry) => (
        <code className="claim-chip" key={entry} title={title?.(entry)}>{entry}</code>
      ))}
    </span>
  );
}

/** Copy-to-clipboard icon for any identifier the operator pastes elsewhere
 *  (a door address, a client id). Confirms with a check for a moment. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button
      type="button"
      className="icon-btn"
      onClick={copy}
      title={copied ? 'Copied' : label}
      aria-label={copied ? 'Copied' : label}
    >
      {copied ? (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M3.5 8.5l3 3 6-6" fill="none" stroke="currentColor" strokeWidth="1.6"
                strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <rect x="5.6" y="5.6" width="8" height="8" rx="1.8" fill="none"
                stroke="currentColor" strokeWidth="1.4" />
          <path d="M10.4 5.6V4.2A1.8 1.8 0 0 0 8.6 2.4H4.2A1.8 1.8 0 0 0 2.4 4.2v4.4a1.8 1.8 0 0 0 1.8 1.8h1.4"
                fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      )}
    </button>
  );
}

/** One request block: a labelled, copyable command. */
function ScriptBlock({ title, script, note }: { title: string; script: string; note?: string }) {
  return (
    <div className="script-block">
      <div className="script-pop-head">
        <span>{title}</span>
        <CopyButton value={script} label="Copy command" />
      </div>
      <pre className="script-pop-body">{script}</pre>
      {note ? <p className="script-pop-note">{note}</p> : null}
    </div>
  );
}

/** What this card's caller can be done TO from a script, with its identifiers
 *  already inlined. A grant can always be revoked by access id; it can also be
 *  NARROWED in place — the card is the authority the guard resolves live, so a
 *  smaller claim set takes effect on that caller's very next call, on the
 *  credential it already holds. A caller with a stable client identity (a
 *  connected app, a hosted agent) narrows through the agent-grant op keyed on
 *  its client id; a manual automation narrows through the automation-update op
 *  keyed on its access id — same card-is-authority idea, and the token the
 *  operator copied stays valid either way. */
function RevokeScript({ item }: { item: DelegatedAccessRecord }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  const accessId = item.access_id;
  const isManual = item.source === 'manual';
  const [resource, claims] = Object.entries(item.resource_grants || {})[0] || ['', []];
  const canNarrow = Boolean(resource) && (isManual || Boolean(item.client_id));
  const revoke = [
    `curl -s -X POST \\`,
    `  "${operationUrl('delegated_access_revoke')}" \\`,
    `  -H "Authorization: Bearer $TOKEN" \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{"access_id": "${accessId}"}'`,
  ].join('\n');
  // A manual automation is narrowed by rewriting its CARD (keyed on access id):
  // the whole grant map is replaced, so the token it already holds keeps working
  // with the smaller scope. A client-identified caller narrows through the
  // agent-grant op instead. Both are the same "the card is what the guard reads
  // live" idea.
  const narrowManual = [
    `curl -s -X POST \\`,
    `  "${operationUrl('delegated_access_update')}" \\`,
    `  -H "Authorization: Bearer $TOKEN" \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{`,
    `    "access_id": "${accessId}",`,
    `    "resource_grants": {"${resource}": ${JSON.stringify(claims)}}`,
    `  }'`,
  ].join('\n');
  const narrowAgent = [
    `curl -s -X POST \\`,
    `  "${operationUrl('delegated_agent_grant_create')}" \\`,
    `  -H "Authorization: Bearer $TOKEN" \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{`,
    `    "client_id": "${item.client_id}",`,
    `    "resource": "${resource}",`,
    `    "claims": ${JSON.stringify(claims)},`,
    `    "replace": true`,
    `  }'`,
  ].join('\n');
  const narrow = !canNarrow ? '' : isManual ? narrowManual : narrowAgent;
  const script = revoke;
  return (
    <>
      <button
        type="button"
        className="icon-btn"
        onClick={() => setOpen((v) => !v)}
        title="Revoke from a script"
        aria-label="Revoke from a script"
        aria-expanded={open}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M8 7.2v4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="8" cy="4.9" r="0.85" fill="currentColor" />
        </svg>
      </button>
      {open ? (
        // A dialog, not inline content: the commands are wide and the card's
        // action column is narrow — rendering them in place stretched the whole
        // card. Fixed positioning keeps the layout untouched.
        <div
          className="script-modal"
          role="dialog"
          aria-modal="true"
          aria-label="Manage this caller from a script"
          onClick={() => setOpen(false)}
        >
          <div className="script-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="script-dialog-head">
              <div>
                <div className="script-dialog-title">Manage this caller from a script</div>
                <div className="script-dialog-sub">{item.label || item.access_id}</div>
              </div>
              <button
                type="button"
                className="icon-btn"
                onClick={() => setOpen(false)}
                title="Close"
                aria-label="Close"
              >
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                  <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor"
                        strokeWidth="1.6" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            {canNarrow ? (
              <ScriptBlock
                title="Narrow this caller"
                script={narrow}
                note="Edit the claims list to the smaller set you want. It becomes the record exactly, and applies on this caller's next call."
              />
            ) : null}
            <ScriptBlock
              title="Revoke"
              script={script}
              note="$TOKEN is a credential allowed to call this deployment's operations."
            />
          </div>
        </div>
      ) : null}
    </>
  );
}

/** A copyable identifier under a card title — truncated to one line, full value
 *  on hover. Which id it is depends on the caller: a connected app and a hosted
 *  agent are identified by their CLIENT id (what the platform authorizes and
 *  what appears in logs), while an automation's client id is minted internally
 *  and never presented by anyone - its actionable id is the ACCESS id, the one
 *  a revoke takes. */
function ClientIdRef({ value, kind }: { value: string; kind: 'client' | 'access' }) {
  const label = kind === 'access' ? 'access id' : 'client id';
  return (
    <span className="id-ref" title={`${label}: ${value}`}>
      <span className="id-kind">{label}</span>
      <code className="id-value">{value}</code>
      <CopyButton value={value} label={`Copy ${label}`} />
    </span>
  );
}

/** The door's resource: a long URL/pattern. Shown on one truncated line that
 *  expands to the full value on click (wrapped, selectable), with a copy
 *  button — the value operators paste into a client config. */
function DoorRef({ value }: { value: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="door-ref">
      <code
        className={open ? 'door-uri open' : 'door-uri'}
        title={open ? undefined : value}
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') setOpen((v) => !v); }}
      >
        {value}
      </code>
      <CopyButton value={value} label="Copy address" />
    </span>
  );
}

/** A service can expose hundreds of operations, so the card shows the COUNT
 *  and keeps the list folded away; opening reveals it as chips, and it folds
 *  back with the same control. */
function CountFold({ entries, noun }: { entries: string[]; noun: string }) {
  const [open, setOpen] = useState(false);
  if (!entries.length) return null;
  return (
    <span className="count-fold">
      <button type="button" className="inline-more" onClick={() => setOpen((v) => !v)}>
        {open ? '▾' : '▸'} {entries.length} {noun}{entries.length === 1 ? '' : 's'}
      </button>
      {open ? <ChipRow entries={entries} /> : null}
    </span>
  );
}

/** The stored selection has three forms and only one of them is a map. A
 *  wildcard names no operations to list — it means whatever the acknowledged
 *  catalog offers — so it yields no rows here and the caller says so in words. */
function isWildcardNamedServices(
  selection: DelegatedAccessRecord['named_service_operations'],
): boolean {
  return typeof selection === 'string';
}

/** What the card covers, for display and for seeding the editor.
 *
 *  The stored selection answers this only when it is an exact map. A wildcard
 *  and a pre-encoding record name no operations of their own, so the server
 *  ships the expansion it saved them against; without it both draw exactly like
 *  an explicit {}. */
function cardNamedServiceOperations(
  item: DelegatedAccessRecord,
): DelegatedAccessNamedServiceOperations {
  const effective = item.effective_named_service_operations;
  if (effective && Object.keys(effective).length) return effective;
  const selection = item.named_service_operations;
  if (!selection || isWildcardNamedServices(selection)) return {};
  return selection as DelegatedAccessNamedServiceOperations;
}

/** Whether this card's doors configure named services at all.
 *
 *  Separates "covers nothing" from "there is nothing to cover". Only the first
 *  is worth a row: a card that selected no operation reaches none of them, and
 *  drawing nothing reads as if the question did not apply. */
function cardOffersNamedServices(
  item: DelegatedAccessRecord,
  resources: DelegatedAccessResourceOption[],
): boolean {
  const offered = offeredNamedServiceOperations(
    resources,
    Object.keys(item.resource_grants || {}),
    (key) => (item.catalog_row_by_resource || {})[key] || key,
  );
  return Object.keys(offered).length > 0;
}

/** One row per namespace the card covers.
 *
 *  Rows are merged across doors. A wildcard and a pre-encoding card store ONE
 *  materialized tree for the whole card, and the projection attributes that
 *  same tree to every door the card holds — printing it once per door states a
 *  per-door fact the card does not carry. */
function namedServiceRows(item: DelegatedAccessRecord): string[] {
  const merged = new Map<string, Set<string>>();
  Object.values(cardNamedServiceOperations(item)).forEach((namespaces) => {
    Object.entries(namespaces || {}).forEach(([namespace, operations]) => {
      const held = merged.get(namespace) || new Set<string>();
      (operations || []).forEach((operation) => held.add(operation));
      merged.set(namespace, held);
    });
  });
  return Array.from(merged.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([namespace, operations]) => (
      `${namespace} (${Array.from(operations).sort().join(', ')})`
    ));
}

/** Every named-service operation the ACTIVE catalog offers for these resources.
 *
 *  The same rows the picker draws, so "all of them are ticked" is a question
 *  the panel can answer without guessing. */
function offeredNamedServiceOperations(
  resources: DelegatedAccessResourceOption[],
  forResources: string[],
  // The create form picks resources FROM the options, so its keys are already
  // catalog keys; a stored card's may not be.
  rowFor: (resource: string) => string = (resource) => resource,
): DelegatedAccessNamedServiceOperations {
  const out: DelegatedAccessNamedServiceOperations = {};
  forResources.forEach((resource) => {
    const option = catalogRowFor(resources, resource, rowFor);
    if (!option) return;
    const namespaces: Record<string, string[]> = {};
    (option.named_services || []).forEach((namespace) => {
      const operations = operationRows(namespace).map((row) => row.operation);
      if (operations.length) namespaces[namespace.namespace] = operations;
    });
    // Keyed by the CARD's resource, so the selection and this map align.
    if (Object.keys(namespaces).length) out[resource] = namespaces;
  });
  return out;
}

/** The catalog row a card resource resolves to.
 *
 *  A card key is the row's own pattern (hub-created) or a concrete URL (an
 *  OAuth client's `resource`). The server resolves the two through one matcher
 *  and ships the answer as `catalog_row_by_resource`; string equality is the
 *  fallback for a record that predates the field. */
function catalogRowFor(
  resources: DelegatedAccessResourceOption[],
  resource: string,
  rowFor: (resource: string) => string,
): DelegatedAccessResourceOption | undefined {
  const key = rowFor(resource) || resource;
  return resources.find((option) => option.resource === key);
}

/** The card-level selection as the server stores it.
 *
 *  `"*"` is not a separate mode in the design: an explicitly submitted wildcard
 *  "means the operator selected all operations shown from the current catalog",
 *  which is the one case Save may keep as `"*"` against the new catalog
 *  version. So ticking every offered box encodes as `"*"`, anything less as the
 *  exact map, and nothing as an explicit {}. */
function encodeNamedServiceSelection(
  selected: DelegatedAccessNamedServiceOperations,
  offered: DelegatedAccessNamedServiceOperations,
): DelegatedAccessStoredNamedServices {
  const entries = Object.entries(offered);
  if (!entries.length) return selected;
  const everythingTicked = entries.every(([resource, namespaces]) => (
    Object.entries(namespaces).every(([namespace, operations]) => {
      const held = new Set(selected[resource]?.[namespace] || []);
      return operations.every((operation) => held.has(operation));
    })
  ));
  return everythingTicked ? '*' : selected;
}

/** The card's coverage, kept to what the picker can actually draw.
 *
 *  Operations the active catalog no longer offers have no checkbox, so seeding
 *  them would resubmit choices the operator cannot see or untick — the design
 *  is explicit that the frontend never sends a hidden stale operation back
 *  merely to reconstruct the old card. */
function seedNamedServiceOperations(
  item: DelegatedAccessRecord,
  offered: DelegatedAccessNamedServiceOperations,
): DelegatedAccessNamedServiceOperations {
  const covered = cardNamedServiceOperations(item);
  const out: DelegatedAccessNamedServiceOperations = {};
  Object.entries(offered).forEach(([resource, namespaces]) => {
    const held = covered[resource] || {};
    const kept: Record<string, string[]> = {};
    Object.entries(namespaces).forEach(([namespace, operations]) => {
      const selected = new Set(held[namespace] || []);
      const surviving = operations.filter((operation) => selected.has(operation));
      if (surviving.length) kept[namespace] = surviving;
    });
    if (Object.keys(kept).length) out[resource] = kept;
  });
  return out;
}

function driftLabel(row: { resource: string; namespace?: string; claim?: string; operation?: string }): string {
  const parts = [doorAlias(row.resource) || row.resource];
  if (row.namespace) parts.push(row.namespace);
  parts.push(row.claim || row.operation || '');
  return parts.filter(Boolean).join(' · ');
}

/** Backend-computed drift. The panel renders what the server decided; it never
 *  compares catalogs itself. */
function CatalogDriftNotice({ drift }: { drift?: DelegatedCatalogDrift }) {
  if (!drift) return null;
  if (drift.status === 'current' || drift.status === 'no_relevant_change') return null;

  if (drift.status === 'unavailable') {
    return (
      <div className="notice" style={{ marginTop: 10, marginBottom: 10 }}>
        <strong>Service access cannot be checked right now</strong>
        <div>Editing is unavailable until the service catalog can be read again.</div>
      </div>
    );
  }

  const removed = [
    ...(drift.removed?.resources || []),
    ...(drift.removed?.claims || []),
    ...(drift.removed?.outer_operations || []),
    ...(drift.removed?.named_service_operations || []),
  ];
  const added = [
    ...(drift.added?.claims || []),
    ...(drift.added?.outer_operations || []),
    ...(drift.added?.named_service_operations || []),
  ];
  if (!removed.length && !added.length && drift.status !== 'baseline_missing') return null;

  return (
    <div className="notice" style={{ marginTop: 10, marginBottom: 10 }}>
      <strong>Service access changed since this grant was last saved</strong>
      <details>
        <summary>What changed</summary>
        {removed.length ? (
          <div>
            <div className="card-field-label">No longer available</div>
            <ul>
              {removed.map((row) => (
                <li key={`removed-${driftLabel(row)}`}>
                  <code>{driftLabel(row)}</code> — already ineffective, removed when you save
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {added.length ? (
          <div>
            <div className="card-field-label">Newly available</div>
            <ul>
              {added.map((row) => (
                <li key={`added-${driftLabel(row)}`}>
                  <code>{driftLabel(row)}</code> — not granted; select it to allow
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {drift.status === 'baseline_missing' ? (
          <div>
            This grant predates the recorded service catalog, so newly available options
            cannot be listed.
          </div>
        ) : null}
      </details>
    </div>
  );
}

export function DelegatedAccessPanel({ openParams }: { openParams?: Record<string, string> } = {}) {
  const dispatch = useAppDispatch();
  const { platformUserId, items, grantOptions, resources, issuedToken, issuedHeader, issuedAccess, busy } = useAppSelector((s) => s.delegatedAccess);
  const { providers, accounts } = useAppSelector((s) => s.delegatedToKdcube);
  const [label, setLabel] = useState('Automation access');
  const [resourceGrants, setResourceGrants] = useState<Record<string, string[]>>({});
  const [namedServiceOperations, setNamedServiceOperations] = useState<DelegatedAccessNamedServiceOperations>(
    // The demand names the operation it was refused; approval grants that one.
    () => {
      const asked = pendingAgentGrantRequest(openParams);
      if (!asked?.namespace || !asked.operation || !asked.resource) return {};
      return { [asked.resource]: { [asked.namespace]: [asked.operation] } };
    },
  );
  const [ttlSeconds, setTtlSeconds] = useState(ttlOptions[0].value);
  const [pendingGrant, setPendingGrant] = useState(() => pendingAgentGrantRequest(openParams));
  const manualFocus = useMemo(() => manualAccessFocusRequest(openParams), [openParams]);
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
  // Search + "show more" over the granted-access list (see matchedOtherItems).
  const [grantQuery, setGrantQuery] = useState('');
  const [grantLimit, setGrantLimit] = useState(GRANT_PAGE_SIZE);
  // Rename of the card being edited (a DCR client always registers "Claude").
  const [editLabel, setEditLabel] = useState('');
  // The automation-creation form is folded behind its call to action.
  const [createOpen, setCreateOpen] = useState(false);
  // Claims kept on the edited card, keyed `${resource}:${claim}`. The form
  // offers the resource's whole catalog, so only `true` counts.
  const [editPicks, setEditPicks] = useState<Record<string, boolean>>({});
  // Namespace narrowing being edited: {resource: {namespace: [operation]}}.
  const [editNamedServiceOperations, setEditNamedServiceOperations] =
    useState<DelegatedAccessNamedServiceOperations>({});
  // Card resource -> catalog row, for the record being edited.
  const [editCatalogRows, setEditCatalogRows] = useState<Record<string, string>>({});
  const editRowFor = useCallback(
    (resource: string) => editCatalogRows[resource] || resource,
    [editCatalogRows],
  );
  // Per-account claim binding being edited: {provider_id: {account_id: [claims]}}.
  // Default-closed: a provider with nothing ticked grants NO account to this
  // client; only the ticked accounts+claims are allowed.
  const [editAccountScope, setEditAccountScope] = useState<Record<string, Record<string, string[]>>>({});
  // The same binding for the card being created.
  const [createAccountScope, setCreateAccountScope] = useState<Record<string, Record<string, string[]>>>({});
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
      namedServiceOperations: encodeNamedServiceSelection(
        selectedNamedServiceOperations,
        offeredNamedServiceOperations(
          resources,
          selectedResourceEntries.map(([resource]) => resource),
        ),
      ),
      accountScope: createAccountScope,
      ttlSeconds,
    })).unwrap().catch(() => undefined);
    // Fold the form back once the credential exists — the issued token renders
    // above it, which is what the user needs to see next.
    setCreateOpen(false);
    setCreateAccountScope({});
    void dispatch(loadDelegatedAccess());
  };

  // Revoke is destructive and easy to misclick, so it is a two-step inline
  // confirm (our own .btn family, never a native browser dialog): the Revoke
  // button arms a "Revoke? Confirm / Cancel" row on the same spot.
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const revoke = async (accessId: string) => {
    setConfirmRevokeId(null);
    await dispatch(revokeDelegatedAccess({ accessId })).unwrap().catch(() => undefined);
    void dispatch(loadDelegatedAccess());
  };
  const renderRevokeControl = (item: DelegatedAccessRecord) => {
    const accessId = item.access_id;
    if (confirmRevokeId === accessId) {
      return (
        <span className="revoke-confirm">
          <span className="revoke-confirm__q">Revoke?</span>
          <button className="btn btn-danger" type="button" disabled={busy} onClick={() => revoke(accessId)}>
            Confirm
          </button>
          <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setConfirmRevokeId(null)}>
            Cancel
          </button>
        </span>
      );
    }
    return (
      <span className="action-row">
        <button className="btn btn-danger" type="button" disabled={busy} onClick={() => setConfirmRevokeId(accessId)}>
          Revoke
        </button>
        <RevokeScript item={item} />
      </span>
    );
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
    // A per-account-only change (the deep link from a binding miss: the door
    // claim is already granted, the user only ticked a per-account permission)
    // has no new door claim to send. An operation-only ask — the card holds
    // every claim the operation declares and only its boundary excludes it —
    // has none either. Still emit the resource carrying its EXISTING claims so
    // the binding and the operation reach the record.
    const operationOnlyAsk = Boolean(pendingGrant.namespace && pendingGrant.operation);
    if ((Object.keys(pendingAccountScope).length || operationOnlyAsk) && !merged[pendingGrant.resource]) {
      const existing = items.find((record) => (record.client_id || '') === pendingGrant.clientId);
      merged[pendingGrant.resource] = [...((existing?.resource_grants || {})[pendingGrant.resource] || [])];
    }
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

  // Toggle one claim on one account for a provider. An account with no claims
  // drops out; a provider with no bound accounts drops out (=> nothing granted
  // there — the runtime is default-closed for delegated callers).
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
  // Seed a per-account claim binding {provider:{account_id:[claims]}} from a
  // stored grant record: skip account "*" (unbound), expand claim "*" to the
  // account's own approved claims, and keep only claims the account still holds.
  // Shared by the Edit flow AND the pending consent card, so a re-consent for a
  // newly-demanded door claim shows the bindings already granted — not an empty
  // picker.
  const seedAccountScopeFromRecord = useCallback((item: DelegatedAccessRecord): Record<string, Record<string, string[]>> => {
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
    return scope;
  }, [providersWithAccounts]);
  const startEdit = useCallback((item: DelegatedAccessRecord) => {
    const picks: Record<string, boolean> = {};
    Object.entries(item.resource_grants || {}).forEach(([resource, grants]) => {
      grants.forEach((claim) => { picks[`${resource}:${claim}`] = true; });
    });
    setEditingAccessId(item.access_id);
    setEditPicks(picks);
    setEditCatalogRows({ ...(item.catalog_row_by_resource || {}) });
    // Seeded from what the card COVERS, not from what it names: a wildcard
    // names nothing, and an empty picker is indistinguishable from an explicit
    // {} — the operator would then narrow the card with the first box they tick
    // believing they widen it.
    setEditNamedServiceOperations(
      seedNamedServiceOperations(
        item,
        offeredNamedServiceOperations(
          resources,
          Object.keys(item.resource_grants || {}),
          (resource) => (item.catalog_row_by_resource || {})[resource] || resource,
        ),
      ),
    );
    setEditAccountScope(seedAccountScopeFromRecord(item));
    setEditLabel(item.label || '');
  }, [resources, seedAccountScopeFromRecord]);
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
  const focusedManualAccessId = useRef<string | null>(null);
  useEffect(() => {
    if (!manualFocus) {
      focusedManualAccessId.current = null;
      return;
    }
    const item = items.find(
      (candidate) => candidate.source === 'manual'
        && candidate.access_id === manualFocus.accessId,
    );
    if (!item) return;
    if (focusedManualAccessId.current !== manualFocus.accessId) {
      focusedManualAccessId.current = manualFocus.accessId;
      startEdit(item);
    }
    if (manualFocus.accountId) {
      const account = accounts.find(
        (candidate) => candidate.account_id === manualFocus.accountId,
      );
      if (account?.provider_id) {
        setExpandedAccountProviders((current) => ({
          ...current,
          [account.provider_id]: true,
        }));
      }
    }
  }, [manualFocus, items, accounts, startEdit]);
  // Per-account claim binding chosen while granting a PENDING request (consent card).
  const [pendingAccountScope, setPendingAccountScope] = useState<Record<string, Record<string, string[]>>>({});
  const togglePendingAccount = makeToggleAccountClaim(setPendingAccountScope);
  const toggleCreateAccount = makeToggleAccountClaim(setCreateAccountScope);
  // Seed the pending consent card's per-account picker ONCE (per client) from
  // the agent's existing grant, after the account list + grant registry load.
  // Without this, re-consent for a newly-demanded door claim (e.g. mail:send)
  // renders every prior per-account binding unchecked. The ref guard seeds the
  // stored state but never clobbers the user's in-progress ticks.
  const seededPendingFor = useRef<string | null>(null);
  useEffect(() => {
    if (!pendingGrant) { seededPendingFor.current = null; return; }
    if (seededPendingFor.current === pendingGrant.clientId) return;
    if (!accounts.length) return; // wait for the account list
    const existing = items.find(
      (record) => (record.client_id || '') === pendingGrant.clientId
        && !!record.account_scope && Object.keys(record.account_scope).length > 0,
    );
    seededPendingFor.current = pendingGrant.clientId;
    // Guided per-account ask: the denial named the exact account + claim. Open
    // that provider's section so the user lands on the checkboxes — but tick
    // NOTHING: granting is always the user's explicit decision. The guide
    // block names the account and claim to tick.
    if (pendingGrant.accountId && pendingGrant.accountClaim) {
      const account = accounts.find((item) => (item.account_id || '') === pendingGrant.accountId);
      const provider = account?.provider_id || '';
      if (provider) {
        setExpandedAccountProviders((current) => ({ ...current, [provider]: true }));
      }
    }
    // Restore only what this agent was ALREADY granted before (re-consent) —
    // that is existing state, not a new pre-tick.
    if (existing) setPendingAccountScope(seedAccountScopeFromRecord(existing));
  }, [pendingGrant, items, accounts, seedAccountScopeFromRecord]);

  // The per-account permission picker: a disclosure per provider showing
  // "<n>/<m> accounts" (or "no accounts yet" when unbound) so a large account
  // list stays legible; expanding shows EACH connected account with its own
  // approved claims as checkboxes — so "read+write on one account, read-only on
  // another" is picked here. Reused by the Edit blocks and the pending consent
  // card.
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
                {boundCount ? `${boundCount}/${total} accounts` : 'no accounts yet'}
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
          Tick the permissions {who} may use on each account. Nothing is granted until you tick it — a provider with no ticks gives {who} no account access there.
        </div>
      </div>
    );
  };

  // Same cascade as the create form: ticking an operation adds the claims it
  // declares, dropping a claim drops the operations that required it.
  const toggleEditClaim = (resource: string, claim: string, checked: boolean) => {
    setEditPicks((current) => ({ ...current, [`${resource}:${claim}`]: checked }));
    if (checked) return;
    const resourceOption = catalogRowFor(resources, resource, editRowFor);
    if (!resourceOption) return;
    setEditNamedServiceOperations((current) => {
      const existingNamespaces = current[resource];
      if (!existingNamespaces) return current;
      const removesSurfaceAccess = commonOperationGrants(resourceOption).includes(claim);
      const nextNamespaces: Record<string, string[]> = {};
      (resourceOption.named_services || []).forEach((namespace) => {
        const disallowed = new Set(
          operationRows(namespace)
            .filter((row) => removesSurfaceAccess || row.grants.includes(claim))
            .map((row) => row.operation),
        );
        const remaining = (existingNamespaces[namespace.namespace] || [])
          .filter((operation) => !disallowed.has(operation));
        if (remaining.length) nextNamespaces[namespace.namespace] = remaining;
      });
      return { ...current, [resource]: nextNamespaces };
    });
  };

  const toggleEditNamedServiceOperation = (
    resource: string,
    namespace: string,
    operation: string,
    grants: string[],
    checked: boolean,
  ) => {
    if (checked) {
      const resourceOption = catalogRowFor(resources, resource, editRowFor);
      const required = [
        ...grants,
        ...(resourceOption ? commonOperationGrants(resourceOption) : []),
      ];
      setEditPicks((current) => ({
        ...current,
        ...Object.fromEntries(required.map((grant) => [`${resource}:${grant}`, true])),
      }));
    }
    setEditNamedServiceOperations((current) => {
      const namespaces = { ...(current[resource] || {}) };
      const existing = namespaces[namespace] || [];
      const updated = checked
        ? Array.from(new Set([...existing, operation]))
        : existing.filter((item) => item !== operation);
      if (updated.length) namespaces[namespace] = updated;
      else delete namespaces[namespace];
      // The resource key stays when empty: absent means no narrowing, present
      // and empty means nothing allowed.
      return { ...current, [resource]: namespaces };
    });
  };

  const clearEditState = () => {
    setEditingAccessId(null);
    setEditPicks({});
    setEditNamedServiceOperations({});
    setEditAccountScope({});
    setEditLabel('');
  };

  // The catalog's claims (so one can be added) union the record's own (so a
  // claim the catalog dropped is still shown and removable).
  const editableClaimsFor = (item: DelegatedAccessRecord, resource: string): string[] => {
    const option = catalogRowFor(
      resources, resource, (key) => (item.catalog_row_by_resource || {})[key] || key,
    );
    return Array.from(new Set([
      ...((item.resource_grants || {})[resource] || []),
      ...(option ? grantsForResource(option) : []),
    ]));
  };

  /** Claims this card holds that the active catalog no longer offers, per the
   *  backend's comparison. */
  const withdrawnClaims = (item: DelegatedAccessRecord, resource: string): Set<string> => {
    const rows = item.catalog_drift?.removed?.claims || [];
    return new Set(
      rows.filter((row) => row.resource === resource && row.claim).map((row) => row.claim as string),
    );
  };

  const saveEdit = async (item: DelegatedAccessRecord) => {
    const kept: Record<string, string[]> = {};
    Object.keys(item.resource_grants || {}).forEach((resource) => {
      kept[resource] = editableClaimsFor(item, resource)
        .filter((claim) => editPicks[`${resource}:${claim}`] === true);
    });
    const anyKept = Object.values(kept).some((claims) => claims.length > 0);
    if (!anyKept) {
      // Removing everything is a revoke, not an edit.
      await dispatch(revokeDelegatedAccess({ accessId: item.access_id })).unwrap().catch(() => undefined);
      clearEditState();
      void dispatch(loadDelegatedAccess());
      return;
    }
    // One save for every family: the card is the authority, keyed by access_id,
    // and the edit replaces the authority the operator reviewed. The credential
    // is untouched — a copied manual token, an agent's reusable bearer and an
    // OAuth client's handles all keep working, on their very next call.
    const prunedKept = Object.fromEntries(
      Object.entries(kept).filter(([, claims]) => claims.length > 0),
    );
    // The edited selection, minus resources fully unchecked above.
    const keptNamedServiceOperations = Object.fromEntries(
      Object.entries(editNamedServiceOperations)
        .filter(([resource]) => prunedKept[resource]),
    );
    // Nothing offered anywhere means there is nothing to say about the inner
    // boundary; omitting preserves the card's own policy. Otherwise the save
    // is explicit — `"*"` when every offered box is ticked, the exact map
    // otherwise, {} when the operator cleared them all.
    const offered = offeredNamedServiceOperations(
      resources, Object.keys(prunedKept), editRowFor,
    );
    await dispatch(updateDelegatedAccess({
      accessId: item.access_id,
      label: editLabel.trim() || item.label || 'Automation access',
      resourceGrants: prunedKept,
      namedServiceOperations: Object.keys(offered).length
        ? encodeNamedServiceSelection(keptNamedServiceOperations, offered)
        : undefined,
      accountScope: editAccountScope,
      // What this editor was opened on. The server refuses the save when
      // either moved.
      expectedCardRevision: item.card_revision,
      expectedCatalogVersion: item.catalog_drift?.current_version || item.catalog_version,
    })).unwrap().catch(() => undefined);
    clearEditState();
    void dispatch(loadDelegatedAccess());
  };

  // The full delegable catalog (resource -> grant chips -> named-service
  // operation rows), bound to the shared selection state. Rendered in the
  // manual create flow AND in the pending agent card's "add more" section.
  // The search narrows the CARDS; selections live outside the filter, so a
  // grant checked earlier stays selected while the user searches on.
  const visibleResources = resources.filter((item) => resourceMatchesQuery(item, resourceQuery, grantOptionByName));
  const searching = Boolean(resourceQuery.trim());
  // The identity the card in progress has already committed to, or '' while
  // nothing is selected and every door is still reachable.
  const committedIdentityScope = (() => {
    const scopes = new Set(
      resources
        .filter((item) => (resourceGrants[item.resource] || []).length)
        .map((item) => item.identity_scope || 'grantor'),
    );
    return scopes.size === 1 ? Array.from(scopes)[0] : '';
  })();
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
        // One card issues ONE credential, so every door on it must run under
        // the same identity. The server refuses a mixture; offering it is what
        // makes that refusal a surprise.
        const scope = item.identity_scope || 'grantor';
        const scopeBlocked = Boolean(committedIdentityScope) && scope !== committedIdentityScope;
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
                {scopeBlocked ? (
                  <p className="resource-boundaries-empty">
                    This endpoint runs under <code>{scope}</code>, and this card is
                    already committed to <code>{committedIdentityScope}</code>. One
                    card carries one identity - grant it on a separate card.
                  </p>
                ) : null}
                <div className="resource-grants">
                  {grants.map((grant) => {
                    const option = grantOptionByName.get(grant);
                    return (
                      <label
                        className={`grant-chip${scopeBlocked ? ' grant-chip-blocked' : ''}`}
                        key={`${item.resource}:${grant}`}
                        title={scopeBlocked
                          ? `Runs under ${scope}; this card is already committed to ${committedIdentityScope}`
                          : (option?.label || undefined)}
                      >
                        <input
                          type="checkbox"
                          disabled={scopeBlocked}
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
  const pendingAccountLabel = pendingGrant?.accountId
    ? (accountLabelById.get(pendingGrant.accountId) || pendingGrant.accountId)
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
        act on your behalf on <strong>{pendingResourceLabel || 'this resource'}</strong>.{pendingGrant.claims.length ? ' It is asking for:' : ''}
      </p>
      {pendingGrant.accountClaim ? (
        <div className="notice" style={{ marginTop: 0, marginBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Why you are here</div>
          <div style={{ marginBottom: 8 }}>
            This agent tried to use <code>{pendingGrant.accountClaim}</code> on{' '}
            <strong>{pendingAccountLabel || 'your connected account'}</strong> and was stopped —
            nothing was done. Your account allows it, but this agent has not been granted it.
            You decide here what it may use.
          </div>
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            <li>
              Below, under the opened provider, find{' '}
              <strong>{pendingAccountLabel || 'the account you want to allow'}</strong> —
              or any other account you prefer.
            </li>
            <li>Tick <code>{pendingGrant.accountClaim}</code> on that account. Tick only what you want to allow.</li>
            <li>Press <strong>Grant access</strong>.</li>
            <li>Go back and retry the request — it will use exactly what you granted.</li>
          </ol>
        </div>
      ) : null}
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
      <div className="row pending-actions">
        <button
          className="btn"
          type="button"
          disabled={busy || (!pendingCheckedClaims.length && !selectedResourceEntries.length && !Object.keys(pendingAccountScope).length)}
          onClick={grantPending}
        >
          {pendingCheckedClaims.length < (pendingGrant.claims.length || 0) || selectedResourceEntries.length || Object.keys(pendingAccountScope).length
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
  const allOtherItems: DelegatedAccessRecord[] = [];
  items.forEach((item) => {
    if (item.source === 'agent' && item.client_id) {
      const group = agentGroups.get(item.client_id) || [];
      group.push(item);
      agentGroups.set(item.client_id, group);
    } else {
      allOtherItems.push(item);
    }
  });
  // A user accumulates grants (every reconnect mints a client), so the list is
  // searchable by name/id/door and shows the most RECENT first, capped — older
  // ones stay one click away instead of scrolling forever.
  const grantQ = grantQuery.trim().toLowerCase();
  const matchedOtherItems = (grantQ
    ? allOtherItems.filter((item) => [
        item.label || '', item.client_id || '', item.access_id,
        ...Object.keys(item.resource_grants || {}),
      ].join(' ').toLowerCase().includes(grantQ))
    : allOtherItems
  ).slice().sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const otherItems = matchedOtherItems.slice(0, grantLimit);
  const hiddenGrantCount = matchedOtherItems.length - otherItems.length;
  // Agent cards obey the same search + page size, so one control governs the
  // whole tab no matter which kind of caller accumulated.
  const allAgentEntries = Array.from(agentGroups.entries());
  const matchedAgentEntries = (grantQ
    ? allAgentEntries.filter(([clientId, records]) => {
        const who = parseAgentClientId(clientId);
        return [
          clientId, who?.agent || '', who?.app || '',
          ...records.flatMap((record) => [
            record.label || '', ...Object.keys(record.resource_grants || {}),
          ]),
        ].join(' ').toLowerCase().includes(grantQ);
      })
    : allAgentEntries
  ).slice().sort((a, b) => {
    const newest = (records: DelegatedAccessRecord[]) =>
      records.reduce((max, record) => Math.max(max, record.created_at || 0), 0);
    return newest(b[1]) - newest(a[1]);
  });
  const agentEntries = matchedAgentEntries.slice(0, grantLimit);
  const hiddenAgentCount = matchedAgentEntries.length - agentEntries.length;
  const totalGrantCount = allAgentEntries.length + allOtherItems.length;
  const matchedGrantCount = matchedAgentEntries.length + matchedOtherItems.length;
  const resourceLabelFor = (resource: string): string =>
    resources.find((r) => r.resource === resource)?.label || '';
  // The REAL consent is the claim token — that is what renders in the rows
  // (as chips; the vocabulary label rides along as the chip's tooltip).

  const grantedPane = (
    <section className="card">
      <div className="card-head">
        <p className="muted" style={{ margin: 0 }}>
          Access this user granted to agents, automations, and external clients —
          each grant is per caller: an agent or connected app gets exactly what
          you approve here, nothing more. Edit narrows or extends it live;
          revoking stops that caller immediately.
        </p>
        {platformUserId ? (
          <span className="whose-list" title={`Signed in as ${platformUserId}`}>
            <span className="badge badge-ok">you</span>
            <code className="whose-list-id">{platformUserId}</code>
          </span>
        ) : null}
      </div>

      {agentEntries.length ? (
        <div>
          {agentEntries.map(([clientId, records]) => {
            const who = parseAgentClientId(clientId);
            return (
              <div className="resource-option resource-option-stack" key={clientId}>
                <span>
                  <strong>
                    {who ? `${who.agent} · ${who.app}` : clientId}
                    <span className="badge badge-ok">agent</span>
                  </strong>
                  <ClientIdRef value={clientId} kind="client" />
                </span>
                <ul className="accounts">
                  {records.map((item) => {
                    const editing = editingAccessId === item.access_id;
                    return (
                      <li className="account" key={item.access_id}>
                        <div>
                          {/* Edit mode keeps the per-claim checkboxes; the
                              read-only view is the same labelled-row card the
                              connected-app grants use. */}
                          {editing ? Object.entries(item.resource_grants || {}).map(([resource, grants]) => {
                            const resourceOption = catalogRowFor(
                              resources, resource, (key) => (item.catalog_row_by_resource || {})[key] || key,
                            );
                            const editedGrants = grants.filter(
                              (claim) => editPicks[`${resource}:${claim}`] !== false,
                            );
                            return (
                            <div key={resource}>
                              <div className="account-title">{doorAlias(resource) || resourceLabelFor(resource) || resource}</div>
                              {resource !== '*' ? <DoorRef value={resource} /> : null}
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
                              {resourceOption ? (
                                <DelegatedResourceCatalog
                                  resource={resourceOption}
                                  selectedGrants={editedGrants}
                                  selectedOperations={editNamedServiceOperations[resource] || {}}
                                  onOperationChange={(namespace, operation, operationGrants, checked) => (
                                    toggleEditNamedServiceOperation(
                                      resource, namespace, operation, operationGrants, checked,
                                    )
                                  )}
                                  providers={providers}
                                  accounts={accounts}
                                />
                              ) : null}
                            </div>
                            );
                          }) : (
                            <div className="card-fields">
                              {/* Door and Access are paired per door, so which claims
                                  belong to which door survives on a multi-door grant.
                                  Connected apps flatten to one Access row; naming the
                                  row the same way keeps the two cards readable as the
                                  same kind of entry. */}
                              {Object.entries(item.resource_grants || {}).map(([resource, grants]) => (
                                <Fragment key={resource}>
                                  <Field label="Door">
                                    <span className="door-line">
                                      <b>{doorAlias(resource) || (resource === '*' ? 'all resources' : resourceLabelFor(resource) || resource)}</b>
                                      {resource !== '*' ? <DoorRef value={resource} /> : null}
                                    </span>
                                  </Field>
                                  <Field label="Access">
                                    <ChipRow entries={grants} title={(claim) => grantOptionByName.get(claim)?.label || undefined} />
                                  </Field>
                                </Fragment>
                              ))}
                              {namedServiceRows(item).length ? (
                                <Field label="Services">
                                  <CountFold entries={namedServiceRows(item)} noun="service" />
                                  {isWildcardNamedServices(item.named_service_operations) ? (
                                    <small>
                                      Every operation these services offered when this card was
                                      last saved. Operations added since are not included.
                                    </small>
                                  ) : null}
                                </Field>
                              ) : cardOffersNamedServices(item, resources) ? (
                                // The door offers named services and none were
                                // selected: an empty selection reaches nothing,
                                // so this may not read as "not narrowed".
                                <Field label="Services">
                                  <small>
                                    None selected - this card reaches no named-service
                                    operation on this door.
                                  </small>
                                </Field>
                              ) : (
                                <Field label="Operations">
                                  Every operation the permissions above allow - this grant was
                                  not narrowed to a shorter list.
                                </Field>
                              )}
                              {Object.keys(item.account_scope || {}).length ? (
                                <Field label="Accounts">
                                  {Object.entries(item.account_scope || {}).map(([provider, accountsMap]) => (
                                    <span className="acct-block" key={provider}>
                                      <span className="acct-provider">{providers[provider]?.label || provider}</span>
                                      {Object.entries(accountsMap || {}).map(([accountId, claims]) => (
                                        <span className="acct-line" key={accountId}>
                                          <span className="acct-name" title={accountId}>
                                            {accountId === '*'
                                              ? 'any account'
                                              : (accountLabelById.get(accountId)
                                                  || <>account no longer connected <span className="acct-stale">(binding kept)</span></>)}
                                          </span>
                                          <ChipRow entries={(claims || []).includes('*') ? ['all'] : (claims || [])} />
                                        </span>
                                      ))}
                                    </span>
                                  ))}
                                </Field>
                              ) : null}
                            </div>
                          )}
                          {editing ? renderAccountScopePicker(editAccountScope, toggleEditAccount, 'this agent') : null}
                          <div className="card-fields">
                            <Field label="Granted">
                              {formatDate(item.created_at) || 'unknown'}
                              {' · expires '}{formatDate(item.expires_at) || 'unknown'}
                            </Field>
                          </div>
                        </div>
                        <div className="account-actions">
                          {editing ? (
                            <>
                              <button className="btn" type="button" disabled={busy} onClick={() => saveEdit(item)}>
                                Save
                              </button>
                              <button className="btn" type="button" disabled={busy} onClick={clearEditState}>
                                Cancel
                              </button>
                            </>
                          ) : (
                            <>
                              <span className="action-row">
                                <button className="btn" type="button" disabled={busy} onClick={() => startEdit(item)}>
                                  Edit
                                </button>
                                <span className="action-slot" aria-hidden="true" />
                              </span>
                              {renderRevokeControl(item)}
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

      {totalGrantCount > GRANT_PAGE_SIZE || grantQ ? (
        <div className="grant-search">
          <input
            type="search"
            value={grantQuery}
            placeholder="Search connections by name, client id, or door"
            onChange={(event) => { setGrantQuery(event.target.value); setGrantLimit(GRANT_PAGE_SIZE); }}
          />
          <span className="account-sub">
            {matchedGrantCount} of {totalGrantCount}
          </span>
        </div>
      ) : null}

      {otherItems.length ? (
        <ul className="accounts">
          {otherItems.map((item) => {
            // Both callers here are editable in place: the card is the authority
            // the guard resolves live, so ticking/unticking claims narrows or
            // extends what the caller may do on the credential it already holds —
            // an OAuth app (Claude Code) on the bearer it connected with, a
            // manual automation on the token the operator already copied. Neither
            // re-issues a credential; only the scope (and label) change.
            const editable = (item.source === 'oauth' && Boolean(item.client_id))
              || item.source === 'manual';
            const editing = editable && editingAccessId === item.access_id;
            const door = Array.from(new Set(
              Object.keys(item.resource_grants || {}).map(doorAlias).filter(Boolean),
            )).join(', ');
            return (
              <li className="account" key={item.access_id}>
                <div>
                  <div className="account-title">
                    {item.label || item.access_id}
                    {door && !(item.label || '').includes(door)
                      ? <span className="door-suffix">· {door}</span>
                      : null}
                    {item.source === 'oauth'
                      ? <span className="badge badge-ok">connected app</span>
                      : <span className="badge badge-warn">manual token</span>}
                  </div>
                  {item.source === 'manual'
                    ? <ClientIdRef value={item.access_id} kind="access" />
                    : (item.client_id && item.client_id !== item.label
                        ? <ClientIdRef value={item.client_id} kind="client" /> : null)}
                  {manualFocus?.accessId === item.access_id ? (
                    <div className="notice" style={{ marginTop: 10, marginBottom: 10 }}>
                      <strong>Access update required</strong>
                      {manualFocus.accountClaim ? (
                        <div>
                          Allow <code>{manualFocus.accountClaim}</code>
                          {manualFocus.accountId ? <> on <code>{manualFocus.accountId}</code></> : null},
                          then save and retry the operation.
                        </div>
                      ) : manualFocus.claims.length ? (
                        <div>
                          Review <code>{manualFocus.claims.join(', ')}</code>, save, and retry the operation.
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  <CatalogDriftNotice drift={item.catalog_drift} />
                  {editing ? (
                    <label className="rename-row">
                      <span className="card-field-label">Name</span>
                      <input
                        type="text"
                        value={editLabel}
                        placeholder={item.label || 'Name this connection'}
                        onChange={(event) => setEditLabel(event.target.value)}
                      />
                    </label>
                  ) : null}
                  {item.resource_grants && Object.keys(item.resource_grants).length ? (
                    editing ? (
                      Object.keys(item.resource_grants).map((resource) => {
                        const resourceOption = catalogRowFor(
                          resources, resource, (key) => (item.catalog_row_by_resource || {})[key] || key,
                        );
                        const editedGrants = editableClaimsFor(item, resource)
                          .filter((claim) => editPicks[`${resource}:${claim}`] === true);
                        return (
                          <div key={resource}>
                            <div className="account-title">{resource === '*' ? 'all resources' : (doorAlias(resource) || resourceLabelFor(resource) || resource)}</div>
                            {resource !== '*' ? <DoorRef value={resource} /> : null}
                            <div className="resource-grants">
                              {editableClaimsFor(item, resource).map((claim) => {
                                const stale = withdrawnClaims(item, resource).has(claim);
                                return (
                                  <label
                                    className={stale ? 'grant-chip grant-chip-stale' : 'grant-chip'}
                                    key={`${resource}:${claim}`}
                                    title={
                                      stale
                                        ? 'No longer offered by the service catalog — already ineffective, removed when you save'
                                        : grantOptionByName.get(claim)?.label || undefined
                                    }
                                  >
                                    <input
                                      type="checkbox"
                                      checked={editPicks[`${resource}:${claim}`] === true}
                                      disabled={stale}
                                      onChange={(event) => toggleEditClaim(resource, claim, event.target.checked)}
                                    />
                                    <span>{claim}</span>
                                    {stale ? <span className="badge badge-warn">withdrawn</span> : null}
                                  </label>
                                );
                              })}
                            </div>
                            {/* Every family: the card type decides how the
                                credential is managed, not whether its grantor
                                may change authority. */}
                            {resourceOption ? (
                              <DelegatedResourceCatalog
                                resource={resourceOption}
                                selectedGrants={editedGrants}
                                selectedOperations={editNamedServiceOperations[resource] || {}}
                                onOperationChange={(namespace, operation, operationGrants, checked) => (
                                  toggleEditNamedServiceOperation(
                                    resource, namespace, operation, operationGrants, checked,
                                  )
                                )}
                                providers={providers}
                                accounts={accounts}
                              />
                            ) : null}
                          </div>
                        );
                      })
                    ) : null
                  ) : null}
                  {/* Read-only view: labelled rows, values as chips. A grant can
                      carry a long resource URL and dozens of operations, so the
                      card shows structure at a glance and folds the long lists. */}
                  {!editing ? (
                    <div className="card-fields">
                      {Object.keys(item.resource_grants || {}).length ? (
                        <>
                          <Field label="Door">
                            {Object.keys(item.resource_grants || {}).map((resource) => (
                              <span className="door-line" key={resource}>
                                <b>{doorAlias(resource) || (resource === '*' ? 'all resources' : resourceLabelFor(resource) || resource)}</b>
                                {resource !== '*' ? <DoorRef value={resource} /> : null}
                              </span>
                            ))}
                          </Field>
                          <Field label="Access">
                            <ChipRow
                              entries={Array.from(new Set(Object.values(item.resource_grants || {}).flat()))}
                              title={(claim) => grantOptionByName.get(claim)?.label || undefined}
                            />
                          </Field>
                        </>
                      ) : null}
                      {item.operations?.length ? (
                        <Field label="Operations"><CountFold entries={item.operations} noun="operation" /></Field>
                      ) : null}
                      {(() => {
                        const services = namedServiceRows(item);
                        if (services.length) {
                          return <Field label="Services"><CountFold entries={services} noun="service" /></Field>;
                        }
                        if (!cardOffersNamedServices(item, resources)) return null;
                        return (
                          <Field label="Services">
                            <small>
                              None selected — this card reaches no named-service
                              operation on this door.
                            </small>
                          </Field>
                        );
                      })()}
                      {Object.keys(item.account_scope || {}).length ? (
                        <Field label="Accounts">
                          {Object.entries(item.account_scope || {}).map(([provider, accountsMap]) => (
                            <span className="acct-block" key={provider}>
                              <span className="acct-provider">{providers[provider]?.label || provider}</span>
                              {Object.entries(accountsMap || {}).map(([accountId, claims]) => (
                                <span className="acct-line" key={accountId}>
                                  <span className="acct-name" title={accountId}>
                                    {accountId === '*'
                                      ? 'any account'
                                      : (accountLabelById.get(accountId)
                                          || <>account no longer connected <span className="acct-stale">(binding kept)</span></>)}
                                  </span>
                                  <ChipRow entries={(claims || []).includes('*') ? ['all'] : (claims || [])} />
                                </span>
                              ))}
                            </span>
                          ))}
                        </Field>
                      ) : null}
                      <Field label={item.source === 'oauth' ? 'Approved' : 'Created'}>
                        {formatDate(item.created_at) || 'unknown'}
                        {' · expires '}{formatDate(item.expires_at) || 'unknown'}
                        {item.last_four ? (
                          // The credential's last characters: a fingerprint that
                          // identifies WHICH saved token this card is, without
                          // ever redisplaying it. Rendered as a value, not prose.
                          <> · token ends with <code className="claim-chip">{item.last_four}</code></>
                        ) : null}
                      </Field>
                    </div>
                  ) : null}
                  {editing
                    ? renderAccountScopePicker(
                        editAccountScope,
                        toggleEditAccount,
                        item.source === 'manual' ? 'this automation' : 'this app',
                      )
                    : null}
                </div>
                <div className="account-actions">
                  {editing ? (
                    <>
                      <button className="btn" type="button" disabled={busy} onClick={() => saveEdit(item)}>
                        Save
                      </button>
                      <button className="btn" type="button" disabled={busy} onClick={clearEditState}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      {editable ? (
                        <span className="action-row">
                          <button className="btn" type="button" disabled={busy} onClick={() => startEdit(item)}>
                            Edit
                          </button>
                          <span className="action-slot" aria-hidden="true" />
                        </span>
                      ) : null}
                      {renderRevokeControl(item)}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}

      {hiddenGrantCount + hiddenAgentCount > 0 ? (
        <button
          type="button"
          className="inline-more"
          onClick={() => setGrantLimit((n) => n + GRANT_PAGE_SIZE)}
        >
          Show more ({hiddenGrantCount + hiddenAgentCount} older)
        </button>
      ) : null}
      {grantQ && !matchedGrantCount ? (
        <p className="muted">No connection matches “{grantQuery}”.</p>
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

  // The minted token is a one-time secret and must be visible REGARDLESS of the
  // create form's open/closed state — folding the form on submit must never hide
  // it. So it renders at the top level (below), not inside `createPane`.
  const issuedTokenPanel = issuedToken ? (
    <section className="card">
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
    </section>
  ) : null;

  const createPane = (
    <section className="card">
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
        {renderAccountScopePicker(createAccountScope, toggleCreateAccount, 'this credential')}
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
        <div className="form-actions">
          <button className="btn" type="submit" disabled={busy || !canSubmit}>
            Create automation access
          </button>
          <button className="btn btn-ghost" type="button" onClick={() => setCreateOpen(false)}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );

  // The creation surface is summoned, not resident: its trigger sits in the
  // tab's action row and the pane exists only while it is open, so the list
  // spans the full width the rest of the time.
  return (
    <>
      {issuedTokenPanel}
      {!createOpen ? (
        <div className="tab-actions">
          <button className="btn" type="button" onClick={() => setCreateOpen(true)}>
            Create automation access
          </button>
        </div>
      ) : null}
      <PaneGroup
        panes={[
          ...(pendingGrantPane ? [{
            id: 'pending-grant',
            // The claims ride the pane title so the ask reads from the bar alone.
            title: `Agent access request — ${pendingGrant?.claims.join(', ') || ''}`,
            content: pendingGrantPane,
            // The request is THE pending action: it leads the tab — full-row,
            // generous height, claims and Grant never below the fold — while
            // Granted access stays visible beneath.
            lead: true,
          }] : []),
          // A summoned creation surface is the active task: it leads the tab
          // (full row, at the top) while it is open; the list follows.
          ...(createOpen ? [{
            id: 'create', title: 'Create automation access', content: createPane, lead: true,
          }] : []),
          { id: 'granted', title: 'Granted access', content: grantedPane },
        ]}
      />
    </>
  );
}
