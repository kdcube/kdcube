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

export interface ConsentPlanProps {
  request: ConsentPlanRequest;
  claimLabel: (claimId: string) => string;
  busy: boolean;
  /** Runs the plan's next step with the claims to submit: the account's held
   *  claims plus the ones the user kept ticked. */
  onAction: (action: Exclude<ConsentPlanAction, 'done'>, claims: string[]) => void;
  onDismiss: () => void;
}

export function ConsentPlan({ request, claimLabel, busy, onAction, onDismiss }: ConsentPlanProps) {
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

  // Submit = held ∪ ticked. Ticks only count for claims still to approve, so
  // a catalog refresh that promotes a claim to approved keeps the set honest.
  const selectedMissing = state.missingClaims.filter((claimId) => selected.includes(claimId));
  const submitClaims = [...state.approvedClaims, ...selectedMissing];
  const actionDisabled = busy
    || submitClaims.length === 0
    || (state.action === 'approve' && selectedMissing.length === 0);

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
            {request.requestedClaims.map((claimId) => {
              const granted = state.approvedClaims.includes(claimId);
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
        <p className="notice success">All set — go back to chat and retry your request.</p>
      ) : (
        <button
          className="btn"
          type="button"
          disabled={actionDisabled}
          onClick={() => onAction(state.action as Exclude<ConsentPlanAction, 'done'>, submitClaims)}
        >
          {ACTION_BUTTON[state.action as Exclude<ConsentPlanAction, 'done'>]}
        </button>
      )}
    </div>
  );
}
