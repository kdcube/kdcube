import { useState } from 'react';
import type { DelegatedToKdcubeAccount, DelegatedToKdcubeProvider } from '../../api/types';

// Shown when the user arrives from a chat consent card. Turns the deep-link
// parameters into an explicit plan: what is already in place, what still
// needs their action, and one primary button for the next step. The requested
// claims are the USER'S choice: each still-to-approve claim is a preselected
// checkbox chip they may untick before connecting; claims the account already
// holds render locked as granted (the provider-tab tier-picker convention).

export interface ConsentPlanRequest {
  provider?: DelegatedToKdcubeProvider;
  /** The connector app the guarded service resolved for this connect - its
   *  allowed_claims narrow the listed vocabulary. */
  connectorAppId?: string;
  providerLabel: string;
  requestedClaims: string[];
  account?: DelegatedToKdcubeAccount;
}

export type ConsentPlanAction = 'connect' | 'reconnect' | 'approve' | 'done';

export interface ConsentPlanState {
  connected: boolean;
  healthy: boolean;
  approvedClaims: string[];
  missingClaims: string[];
  action: ConsentPlanAction;
}

export function consentPlanState(request: ConsentPlanRequest): ConsentPlanState {
  const account = request.account;
  const connected = Boolean(account);
  const status = account?.credential_status || account?.status || '';
  const healthy = connected
    && !account?.reconnect_required
    && !['reconnect_required', 'missing', 'revoked'].includes(status);
  const approved = new Set(account?.claims || []);
  const approvedClaims = request.requestedClaims.filter((claim) => approved.has(claim));
  const missingClaims = request.requestedClaims.filter((claim) => !approved.has(claim));
  const action: ConsentPlanAction = !connected
    ? 'connect'
    : !healthy
      ? 'reconnect'
      : missingClaims.length
        ? 'approve'
        : 'done';
  return { connected, healthy, approvedClaims, missingClaims, action };
}

const ACTION_BUTTON: Record<Exclude<ConsentPlanAction, 'done'>, string> = {
  connect: 'Connect account',
  reconnect: 'Reconnect account',
  approve: 'Approve access',
};

interface StepProps {
  done: boolean;
  index: number;
  children: React.ReactNode;
}

function PlanStep({ done, index, children }: StepProps) {
  return (
    <li className={`plan-step${done ? ' plan-step-done' : ''}`}>
      <span className="plan-step-mark">{done ? '✓' : index}</span>
      <span className="plan-step-body">{children}</span>
    </li>
  );
}

/** Set when the connect step was reached because an AGENT needed a per-account
 *  claim: after the provider step, the panel offers a one-click hand-off to the
 *  agent's grant card, pre-filled with the account + claim. */
export interface ConsentPlanAgentHandoff {
  clientId: string;
  resource: string;
  accountId: string;
  claim: string;
  /** The DOOR claims the agent grant must cover — the hand-off card submits
   *  these. Without them a FRESH flow (no recorded pending demand) grants
   *  nothing and the create fails with requires_resource_grants. */
  claims?: string[];
}

export interface ConsentPlanProps {
  request: ConsentPlanRequest;
  claimLabel: (claimId: string) => string;
  busy: boolean;
  /** Runs the plan's next step with the claims to submit: the account's held
   *  claims plus the ones the user kept ticked. */
  onAction: (action: Exclude<ConsentPlanAction, 'done'>, claims: string[]) => void;
  onDismiss: () => void;
  agentHandoff?: ConsentPlanAgentHandoff;
}

// The short agent name from a `kdcube-agent:<app>:<agent>` client id.
function agentName(clientId: string): string {
  const parts = clientId.split(':');
  return parts.length ? parts[parts.length - 1] : clientId;
}

const AUTOMATION_CLIENT_PREFIX = 'automation:';

// The agent-grant card URL: this same widget, on the Delegated by KDCube tab,
// with the pending agent grant pre-filled (account + claim focused).
//
// A manual automation takes `manual_access_id` instead: the pending pane's only
// save is delegated_agent_grant_create, which resolves a card by a deterministic
// key. A manual record is keyed by a random access_id, so that save answers
// "this client has no existing grant to extend" for a card in the same list.
// The backend denial builder applies the same rule; this is the second entry
// point into that pane.
function agentCardHref(handoff: ConsentPlanAgentHandoff): string {
  const url = new URL(window.location.href);
  url.searchParams.set('tab', 'delegated_by_kdcube');
  if (handoff.clientId.startsWith(AUTOMATION_CLIENT_PREFIX)) {
    url.searchParams.set('manual_access_id', handoff.clientId.split(':')[1] || '');
    url.searchParams.delete('pending_agent_grant');
  } else {
    url.searchParams.set('pending_agent_grant', '1');
  }
  url.searchParams.set('agent_client_id', handoff.clientId);
  url.searchParams.set('resource', handoff.resource);
  if (handoff.accountId) url.searchParams.set('account_id', handoff.accountId);
  if (handoff.claim) url.searchParams.set('account_claim', handoff.claim);
  // Drop the connect step's own params so the agent card reads a clean state.
  ['provider_id', 'connector_app_id', 'claims', 'agent_resource', 'agent_claims', 'tool_name'].forEach(
    (key) => url.searchParams.delete(key),
  );
  if (handoff.claims && handoff.claims.length) {
    // After the cleanup above: these are the AGENT-GRANT claims the card
    // submits, not the connect step's provider claims.
    url.searchParams.set('claims', handoff.claims.join(','));
  }
  return url.toString();
}

export function ConsentPlan({ request, claimLabel, busy, onAction, onDismiss, agentHandoff }: ConsentPlanProps) {
  const state = consentPlanState(request);
  // The tool asked for every requested claim, so all still-to-approve claims
  // start ticked; the user unticks what they choose to keep to themselves.
  const [selected, setSelected] = useState<string[]>(() => request.requestedClaims.slice());
  const accountName = request.account
    ? (request.account.display_name || request.account.email || request.account.workspace || request.account.account_id)
    : '';

  const toggleClaim = (claimId: string) => {
    setSelected((current) => (
      current.includes(claimId) ? current.filter((item) => item !== claimId) : [...current, claimId]
    ));
  };

  // The list shows the provider's WHOLE claim vocabulary (the connect pane
  // convention), not only the tool's ask: already-consented claims render
  // granted, the tool's ask starts ticked, everything else is opt-in.
  const connectorApp = request.connectorAppId
    ? request.provider?.connector_apps?.[request.connectorAppId]
    : undefined;
  const allowed = connectorApp?.allowed_claims?.length
    ? new Set(connectorApp.allowed_claims)
    : null;
  const vocabulary = Object.keys(request.provider?.claims || {})
    .filter((claimId) => !allowed || allowed.has(claimId))
    .sort();
  const listedClaims = vocabulary.length
    ? [...vocabulary, ...request.requestedClaims.filter((c) => !vocabulary.includes(c))]
    : request.requestedClaims;
  // Held = everything the ACCOUNT already approved (account-wide, not just the
  // tool's ask) - submitting must never narrow an existing authorization.
  const heldSet = new Set(request.account?.claims || []);
  // Submit = held ∪ ticked-and-not-yet-held (ticks may extend past the ask).
  const selectedNew = listedClaims.filter((claimId) => !heldSet.has(claimId) && selected.includes(claimId));
  const submitClaims = [...heldSet, ...selectedNew];
  const actionDisabled = busy
    || submitClaims.length === 0
    || (state.action === 'approve' && selectedNew.length === 0);

  return (
    <div className="plan">
      <div className="plan-head">
        <div>
          <div className="form-title">A KDCube tool needs your {request.providerLabel} account</div>
          <p className="muted">
            Complete the steps below, then retry your request in chat.
          </p>
        </div>
        <button className="btn btn-ghost" type="button" onClick={onDismiss}>Dismiss</button>
      </div>
      <ol className="plan-steps">
        <PlanStep done={state.connected} index={1}>
          {state.connected
            ? <>Account connected: <strong>{accountName}</strong></>
            : <>Connect your {request.providerLabel} account</>}
        </PlanStep>
        <PlanStep done={state.connected && state.healthy} index={2}>
          {state.connected && !state.healthy
            ? <>Its stored access no longer works — reconnect it</>
            : <>Account access is working</>}
        </PlanStep>
        <PlanStep done={state.connected && state.missingClaims.length === 0} index={3}>
          <span className="plan-claims">
            Approve what the tool needs — untick anything you keep to yourself:{' '}
            {listedClaims.map((claimId) => {
              const granted = heldSet.has(claimId);
              if (granted) {
                return (
                  <span key={claimId} className="claim-chip claim-chip-done">
                    ✓ {claimLabel(claimId)}
                  </span>
                );
              }
              const ticked = selected.includes(claimId);
              return (
                <label
                  key={claimId}
                  className={`claim-chip claim-chip-toggle${ticked ? ' claim-chip-missing' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={ticked}
                    onChange={() => toggleClaim(claimId)}
                    disabled={busy}
                  />
                  {claimLabel(claimId)}
                </label>
              );
            })}
          </span>
          {state.missingClaims.length ? (
            <span className="plan-claims-note">
              Grant what you choose — a tool that needs an unticked capability
              asks again in chat.
            </span>
          ) : null}
        </PlanStep>
      </ol>
      {state.action === 'done' ? (
        agentHandoff ? (
          <div className="notice success">
            <p style={{ margin: '0 0 8px' }}>
              Approved for <strong>{accountName || 'this account'}</strong>. One more step —
              grant it to the agent that needs it.
            </p>
            <a className="btn" href={agentCardHref(agentHandoff)}>
              Continue — grant it to {agentName(agentHandoff.clientId)}
            </a>
          </div>
        ) : (
          <p className="notice success">All set — go back to chat and retry your request.</p>
        )
      ) : (
        <>
          <button
            className="btn"
            type="button"
            disabled={actionDisabled}
            onClick={() => onAction(state.action as Exclude<ConsentPlanAction, 'done'>, submitClaims)}
          >
            {ACTION_BUTTON[state.action as Exclude<ConsentPlanAction, 'done'>]}
          </button>
          {agentHandoff ? (
            <p className="muted" style={{ marginTop: 8 }}>
              Then you'll grant it to <strong>{agentName(agentHandoff.clientId)}</strong> — a
              Continue button appears here once this is approved.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
