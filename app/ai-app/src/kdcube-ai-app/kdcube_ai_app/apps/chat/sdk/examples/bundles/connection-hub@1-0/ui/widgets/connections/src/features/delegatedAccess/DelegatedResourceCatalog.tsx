import type {
  DelegatedAccessConnectedAccountRequirement,
  DelegatedAccessNamedServiceNamespaceOption,
  DelegatedAccessResourceOption,
  DelegatedToKdcubeAccount,
  DelegatedToKdcubeProvider,
} from '../../api/types';
import { consentPlanState, type ConsentPlanAction } from '../delegatedToKdcube/ConsentPlan';

interface NamedServiceOperationRow {
  operation: string;
  label: string;
  description: string;
  grants: string[];
}

interface DelegatedResourceCatalogProps {
  resource: DelegatedAccessResourceOption;
  selectedGrants: string[];
  selectedOperations: Record<string, string[]>;
  onOperationChange: (
    namespace: string,
    operation: string,
    grants: string[],
    checked: boolean,
  ) => void;
  providers: Record<string, DelegatedToKdcubeProvider>;
  accounts: DelegatedToKdcubeAccount[];
}

const CONSENT_ACTION_LABEL: Record<Exclude<ConsentPlanAction, 'done'>, string> = {
  connect: 'Connect account',
  reconnect: 'Reconnect account',
  approve: 'Approve access',
};

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.reduce<string[]>((out, item) => {
    const text = String(item || '').trim();
    if (text && !seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
    return out;
  }, []);
}

// Keep parity with OAuth consent: direct tools win over equivalent generic
// call entries, and no action subtype is synthesized downstream.
export function operationRows(namespace: DelegatedAccessNamedServiceNamespaceOption): NamedServiceOperationRow[] {
  const rows: NamedServiceOperationRow[] = [];
  const seen = new Set<string>();
  const tools = Object.entries(namespace.tools || {}).sort((left, right) => (
    Number(Boolean(right[1].operation)) - Number(Boolean(left[1].operation))
  ));
  tools.forEach(([toolName, tool]) => {
    const operations = tool.operations || {};
    if (Object.keys(operations).length) {
      Object.entries(operations).forEach(([operation, policy]) => {
        if (seen.has(operation)) return;
        const grants = strings(policy.grants);
        seen.add(operation);
        rows.push({
          operation,
          label: policy.label || operation || toolName,
          description: policy.description || tool.description || '',
          grants,
        });
      });
      return;
    }
    const operation = tool.operation || toolName;
    if (seen.has(operation)) return;
    const grants = strings(tool.grants);
    seen.add(operation);
    rows.push({
      operation,
      label: tool.label || operation,
      description: tool.description || '',
      grants,
    });
  });
  return rows;
}

function operationKeyAllowed(operationKey: string, operations: string[]): boolean {
  return operations.some((operation) => (
    operationKey === operation || operationKey.startsWith(`${operation}.`)
  ));
}

function claimBranches(
  requirement: DelegatedAccessConnectedAccountRequirement,
  operations: string[],
): Array<{ operation: string; claims: string[] }> {
  return Object.entries(requirement.claims_by_operation || {})
    .filter(([operation]) => operationKeyAllowed(operation, operations))
    .map(([operation, claims]) => ({ operation, claims: strings(claims) }))
    .filter((item) => item.claims.length > 0);
}

function requiredClaims(
  requirement: DelegatedAccessConnectedAccountRequirement,
  operations: string[],
): string[] {
  const branches = claimBranches(requirement, operations);
  if (Object.keys(requirement.claims_by_operation || {}).length) {
    return strings(branches.flatMap((item) => item.claims));
  }
  return strings(requirement.claims);
}

function matchingAccounts(
  requirement: DelegatedAccessConnectedAccountRequirement,
  accounts: DelegatedToKdcubeAccount[],
): DelegatedToKdcubeAccount[] {
  const providerId = String(requirement.provider_id || '').trim();
  const connectorAppId = String(requirement.connector_app_id || '').trim();
  return accounts.filter((account) => (
    account.provider_id === providerId
    && (!connectorAppId || !account.connector_app_id || account.connector_app_id === connectorAppId)
  ));
}

function bestAccount(
  requirement: DelegatedAccessConnectedAccountRequirement,
  claims: string[],
  accounts: DelegatedToKdcubeAccount[],
  provider: DelegatedToKdcubeProvider | undefined,
  providerLabel: string,
): DelegatedToKdcubeAccount | undefined {
  return matchingAccounts(requirement, accounts)
    .map((account) => {
      const state = consentPlanState({ provider, providerLabel, requestedClaims: claims, account });
      const actionRank = state.action === 'done' ? 3 : state.action === 'approve' ? 2 : 1;
      return { account, rank: actionRank * 1000 + state.approvedClaims.length };
    })
    .sort((left, right) => right.rank - left.rank)[0]?.account;
}

function consentHref(
  requirement: DelegatedAccessConnectedAccountRequirement,
  claims: string[],
  account?: DelegatedToKdcubeAccount,
): string {
  const providerId = String(requirement.provider_id || '').trim();
  if (!providerId || !claims.length) return '';
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('tab', 'delegated_to_kdcube');
    url.searchParams.set('provider_id', providerId);
    const connectorAppId = String(requirement.connector_app_id || '').trim();
    if (connectorAppId) url.searchParams.set('connector_app_id', connectorAppId);
    else url.searchParams.delete('connector_app_id');
    url.searchParams.set('claims', claims.join(','));
    if (account?.account_id) url.searchParams.set('account_id', account.account_id);
    else url.searchParams.delete('account_id');
    return url.toString();
  } catch {
    return '';
  }
}

function claimLabel(
  claim: string,
  requirement: DelegatedAccessConnectedAccountRequirement,
  provider?: DelegatedToKdcubeProvider,
): string {
  // The REAL consent is the claim token; friendly labels stay metadata.
  void requirement;
  void provider;
  return claim;
}

export function DelegatedResourceCatalog({
  resource,
  selectedGrants,
  selectedOperations,
  onOperationChange,
  providers,
  accounts,
}: DelegatedResourceCatalogProps) {
  const namespaces = resource.named_services || [];
  if (!namespaces.length) return null;

  /* Every offered row, so "all of them" is a question this component can both
   * ask and answer. Ticking all of them is what the save encodes as `"*"`. */
  const offeredRows = namespaces.flatMap((namespace) => (
    operationRows(namespace).map((row) => ({ namespace: namespace.namespace, row }))
  ));
  const selectedCount = namespaces.reduce((total, namespace) => (
    total + (selectedOperations[namespace.namespace] || []).length
  ), 0);

  const setEveryOperation = (checked: boolean) => {
    offeredRows.forEach(({ namespace, row }) => {
      const held = new Set(selectedOperations[namespace] || []);
      if (held.has(row.operation) === checked) return;
      onOperationChange(namespace, row.operation, row.grants, checked);
    });
  };

  return (
    <div className="resource-boundaries">
      <div className="resource-boundaries-head">
        <strong>Named-service access</strong>
        <span className="resource-boundaries-bulk">
          <button
            type="button"
            onClick={() => setEveryOperation(true)}
            disabled={selectedCount >= offeredRows.length}
          >
            Select all currently available
          </button>
          <button
            type="button"
            onClick={() => setEveryOperation(false)}
            disabled={!selectedCount}
          >
            Clear
          </button>
        </span>
        <span className="badge">{namespaces.length} namespaces</span>
      </div>
      {offeredRows.length && !selectedCount ? (
        <p className="resource-boundaries-empty">
          No operations selected - this card will reach no named-service
          operation on this door.
        </p>
      ) : null}
      {namespaces.map((namespace) => {
        const rows = operationRows(namespace);
        const selected = new Set(selectedOperations[namespace.namespace] || []);
        const includedRows = rows.filter((row) => selected.has(row.operation));
        const includedOperations = Array.from(selected);
        const requirements = (namespace.connected_accounts || [])
          .map((requirement) => ({
            requirement,
            claims: requiredClaims(requirement, includedOperations),
            branches: claimBranches(requirement, includedOperations),
          }))
          .filter((item) => item.claims.length > 0);

        return (
          <details
            className="namespace-boundary"
            key={namespace.namespace}
            open={includedRows.length > 0}
          >
            <summary>
              <span>
                <strong>{namespace.label || namespace.namespace}</strong>
                <small>{namespace.description || namespace.namespace}</small>
              </span>
              <span className={`badge${includedRows.length ? ' badge-ok' : ''}`}>
                {includedRows.length}/{rows.length}
              </span>
            </summary>

            <div className="namespace-operation-list">
              {rows.map((row) => {
                const included = includedRows.includes(row);
                const grantsReady = row.grants.every((grant) => selectedGrants.includes(grant));
                return (
                  <label
                    className={`namespace-operation${included ? ' namespace-operation-included' : ''}`}
                    key={`${namespace.namespace}:${row.operation}:${row.grants.join(':')}`}
                  >
                    <input
                      type="checkbox"
                      checked={included}
                      onChange={(event) => onOperationChange(
                        namespace.namespace,
                        row.operation,
                        row.grants,
                        event.target.checked,
                      )}
                    />
                    <span>
                      <strong>{row.label}</strong>
                      {row.description ? <small>{row.description}</small> : null}
                    </span>
                    <span className="namespace-operation-grants">
                      {row.grants.map((grant) => (
                        <code
                          className={grantsReady ? 'namespace-operation-grant-ready' : ''}
                          key={`${row.operation}:${grant}`}
                        >
                          {grant}
                        </code>
                      ))}
                    </span>
                  </label>
                );
              })}
            </div>

            {requirements.length ? (
              <div className="provider-requirements">
                {requirements.map(({ requirement, claims, branches }) => {
                  const providerId = String(requirement.provider_id || '').trim();
                  const provider = providers[providerId];
                  const providerLabel = provider?.label || String(requirement.provider_label || providerId);
                  const account = bestAccount(requirement, claims, accounts, provider, providerLabel);
                  const state = consentPlanState({
                    provider,
                    providerLabel,
                    requestedClaims: claims,
                    account,
                  });
                  const href = consentHref(requirement, claims, account);
                  const requirementKey = [
                    namespace.namespace,
                    providerId,
                    String(requirement.connector_app_id || ''),
                    claims.join(','),
                  ].join(':');
                  return (
                    <div className="provider-requirement" key={requirementKey}>
                      <div className="provider-requirement-head">
                        <span>
                          <strong>{providerLabel}</strong>
                          <small>{String(requirement.connector_app_id || 'connected account')}</small>
                        </span>
                        {state.action === 'done' ? (
                          <span className="badge badge-ok">ready</span>
                        ) : href ? (
                          <a
                            className="btn btn-ghost provider-requirement-action"
                            href={href}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {CONSENT_ACTION_LABEL[state.action]}
                          </a>
                        ) : null}
                      </div>
                      {branches.length ? (
                        <div className="provider-claim-branches">
                          {branches.map((branch) => (
                            <div className="provider-claim-branch" key={`${requirementKey}:${branch.operation}`}>
                              <code>{branch.operation}</code>
                              <span>
                                {branch.claims.map((claim) => (
                                  <em key={`${branch.operation}:${claim}`}>
                                    {claimLabel(claim, requirement, provider)}
                                  </em>
                                ))}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="provider-claim-list">
                          {claims.map((claim) => (
                            <em key={`${requirementKey}:${claim}`}>{claimLabel(claim, requirement, provider)}</em>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : null}
          </details>
        );
      })}
    </div>
  );
}
