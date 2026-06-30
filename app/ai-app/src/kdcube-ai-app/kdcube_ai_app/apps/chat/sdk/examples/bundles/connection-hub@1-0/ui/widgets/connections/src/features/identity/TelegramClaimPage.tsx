import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  claimTelegramLinkChallenge,
  loadConnectionEdges,
  loadTelegramLinkChallengeStatus,
} from './identitySlice';
import { signOutPlatformSession, startPlatformSignIn } from '../../api/platformAuth';
import type { ConnectionEdge, DelegationGrantOption } from '../../api/types';

interface TelegramClaimPageProps {
  challengeId: string;
}

type ClaimStatus = 'checking' | 'confirm' | 'claiming' | 'connected' | 'signin_required' | 'failed';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isSignInRequired(error: string): boolean {
  const normalized = error.toLowerCase();
  return (
    normalized.includes('requires_authenticated_user') ||
    normalized.includes('require_authenticated_user') ||
    normalized.includes('login_required') ||
    normalized.includes('not authenticated') ||
    normalized.includes('user is required') ||
    normalized.includes('user required') ||
    normalized.includes('unauthorized')
  );
}

async function startKdcubeSignIn() {
  const started = await startPlatformSignIn(window.location.href);
  if (!started) window.location.assign(`${window.location.origin}/platform/chat`);
}

export function TelegramClaimPage({ challengeId }: TelegramClaimPageProps) {
  const dispatch = useAppDispatch();
  const { telegramChallenge, edges, platformUserId, error } = useAppSelector((s) => s.identity);
  const [status, setStatus] = useState<ClaimStatus>('checking');
  const [localError, setLocalError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [authBusy, setAuthBusy] = useState(false);
  const [consentChecked, setConsentChecked] = useState(false);
  const [selectedGrants, setSelectedGrants] = useState<string[]>([]);

  const loadChallengeStatus = useCallback(async (isCancelled: () => boolean) => {
    setStatus('checking');
    setLocalError('');
    return dispatch(loadTelegramLinkChallengeStatus(challengeId)).unwrap()
      .then(async (result) => {
        if (isCancelled()) return;
        await dispatch(loadConnectionEdges()).unwrap().catch(() => undefined);
        if (isCancelled()) return;
        const challengeStatus = String(result.challenge?.status || '');
        const nextStatus = challengeStatus === 'completed' && result.edge ? 'connected' : 'confirm';
        setStatus(nextStatus);
        if (nextStatus === 'confirm') setConsentChecked(false);
      })
      .catch((err) => {
        if (isCancelled()) return;
        const text = errorText(err);
        setStatus(isSignInRequired(text) ? 'signin_required' : 'failed');
        setLocalError(text);
      });
  }, [challengeId, dispatch]);

  useEffect(() => {
    let cancelled = false;
    void loadChallengeStatus(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [attempt, loadChallengeStatus]);

  useEffect(() => {
    const onAuthChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ authenticated?: boolean }>).detail;
      if (detail?.authenticated) setAttempt((value) => value + 1);
    };
    window.addEventListener('kdcube-auth-changed', onAuthChanged);
    return () => window.removeEventListener('kdcube-auth-changed', onAuthChanged);
  }, []);

  const edge = useMemo<ConnectionEdge | null>(() => {
    const fromChallenge = telegramChallenge?.edge;
    if (fromChallenge?.from?.provider === 'telegram') return fromChallenge;
    const subject = telegramChallenge?.challenge?.provider_subject;
    if (subject) {
      return edges.find((item) => item.from?.provider === 'telegram' && item.from?.subject === subject) || null;
    }
    return edges.find((item) => item.from?.provider === 'telegram') || null;
  }, [edges, telegramChallenge]);

  const source = edge?.from || {};
  const target = edge?.to || {};
  const displayName = source.label || source.subject || telegramChallenge?.challenge?.label || 'Telegram';
  const telegramUserId = source.subject || telegramChallenge?.challenge?.provider_subject || '';
  const kdcubeUserId = (
    target.user_id
    || platformUserId
    || telegramChallenge?.platform_user_id
    || telegramChallenge?.target_user_id
    || telegramChallenge?.challenge?.target_user_id
    || ''
  );
  const message = localError || error;
  const grantOptions = useMemo<DelegationGrantOption[]>(() => (
    Array.isArray(telegramChallenge?.delegation_options) ? telegramChallenge.delegation_options : []
  ), [telegramChallenge]);
  const defaultGrantSelection = useMemo(() => (
    grantOptions
      .filter((option) => option.default !== false)
      .map((option) => String(option.grant || '').trim())
      .filter(Boolean)
  ), [grantOptions]);

  useEffect(() => {
    if (status !== 'confirm') return;
    const existing = telegramChallenge?.challenge?.grants;
    const initial = Array.isArray(existing) && existing.length
      ? existing.map((grant) => String(grant || '').trim()).filter(Boolean)
      : defaultGrantSelection;
    setSelectedGrants(initial);
  }, [challengeId, defaultGrantSelection, status, telegramChallenge?.challenge?.grants]);

  const toggleGrant = useCallback((grant: string, checked: boolean) => {
    const normalized = String(grant || '').trim();
    if (!normalized) return;
    setSelectedGrants((current) => {
      const set = new Set(current);
      if (checked) set.add(normalized);
      else set.delete(normalized);
      return Array.from(set);
    });
  }, []);

  const claimLink = useCallback(async () => {
    setStatus('claiming');
    setLocalError('');
    try {
      await dispatch(claimTelegramLinkChallenge({ challengeId, grants: selectedGrants })).unwrap();
      await dispatch(loadConnectionEdges()).unwrap().catch(() => undefined);
      setStatus('connected');
    } catch (err) {
      const text = errorText(err);
      setStatus(isSignInRequired(text) ? 'signin_required' : 'failed');
      setLocalError(text);
    }
  }, [challengeId, dispatch, selectedGrants]);

  const clearPlatformSession = useCallback(async () => {
    setAuthBusy(true);
    try {
      await signOutPlatformSession();
      setStatus('signin_required');
      setLocalError('');
    } finally {
      setAuthBusy(false);
    }
  }, []);

  return (
    <div className="page claim-page">
      <section className="card claim-card">
        <span className="badge badge-ok">Connection Hub</span>
        <h1>Link Telegram to KDCube</h1>

        {status === 'checking' ? (
          <div className="notice">Checking KDCube sign-in and Telegram link details...</div>
        ) : null}

        {status === 'confirm' ? (
          <>
            <div className="notice">
              Confirm that you want to link this Telegram account to the KDCube
              account signed in in this browser.
            </div>
            <div className="account account-compact">
              <div>
                <div className="account-title">{displayName}</div>
                {telegramUserId ? <div className="account-sub">Telegram user id: {telegramUserId}</div> : null}
                {displayName && telegramUserId && displayName !== telegramUserId ? (
                  <div className="account-sub">Telegram nickname: {displayName}</div>
                ) : null}
                {kdcubeUserId ? <div className="account-sub">KDCube user id: {kdcubeUserId}</div> : null}
              </div>
            </div>
            <div className="notice">
              After linking, KDCube can recognize the same person when requests
              arrive from this Telegram account. Select exactly what Telegram
              may derive from this signed-in KDCube account.
            </div>
            {grantOptions.length ? (
              <div className="grant-list" aria-label="Delegated capabilities">
                {grantOptions.map((option) => {
                  const grant = String(option.grant || '').trim();
                  if (!grant) return null;
                  return (
                    <label key={grant} className="grant-option">
                      <input
                        type="checkbox"
                        checked={selectedGrants.includes(grant)}
                        onChange={(event) => toggleGrant(grant, event.target.checked)}
                      />
                      <span>
                        <strong>{option.label || grant}</strong>
                        {option.description ? <small>{option.description}</small> : null}
                        <code>{grant}</code>
                      </span>
                    </label>
                  );
                })}
              </div>
            ) : (
              <div className="notice">
                KDCube did not return any delegable capabilities for this account.
              </div>
            )}
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={consentChecked}
                onChange={(event) => setConsentChecked(event.target.checked)}
              />
              <span>I confirm this Telegram account may use the selected KDCube capabilities.</span>
            </label>
            <div className="button-row">
              <button
                type="button"
                className="btn"
                onClick={() => void claimLink()}
                disabled={!consentChecked || selectedGrants.length === 0}
              >
                Link this Telegram account
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void clearPlatformSession()}
                disabled={authBusy}
              >
                Sign out of KDCube
              </button>
            </div>
            <p className="muted">
              Use sign out if this is not the KDCube account you want to attach
              to Telegram.
            </p>
          </>
        ) : null}

        {status === 'claiming' ? (
          <div className="notice">Linking this Telegram account to KDCube...</div>
        ) : null}

        {status === 'connected' ? (
          <>
            <div className="notice success">
              Telegram is linked to your KDCube account.
            </div>
            <div className="account account-compact">
              <div>
                <div className="account-title">{displayName}</div>
                {telegramUserId ? <div className="account-sub">Telegram user id: {telegramUserId}</div> : null}
                {displayName && telegramUserId && displayName !== telegramUserId ? (
                  <div className="account-sub">Telegram nickname: {displayName}</div>
                ) : null}
                {kdcubeUserId ? <div className="account-sub">KDCube user id: {kdcubeUserId}</div> : null}
                {edge?.grants?.length ? (
                  <div className="account-sub">Delegated grants: {edge.grants.join(', ')}</div>
                ) : null}
              </div>
            </div>
            <div className="button-row">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void clearPlatformSession()}
                disabled={authBusy}
              >
                Sign out of KDCube
              </button>
            </div>
            <p className="muted">
              You can close this browser tab and return to Telegram. The Mini App
              will update the Connect tab.
            </p>
          </>
        ) : null}

        {status === 'signin_required' ? (
          <>
            <div className="notice">
              Sign in to KDCube in this browser to attach this Telegram account
              to your platform user.
            </div>
            <button type="button" className="btn" onClick={() => void startKdcubeSignIn()}>
              Sign in to KDCube
            </button>
            <p className="muted">
              This page uses KDCube platform sign-in from the runtime
              descriptor. After sign-in it will return here and retry the link.
            </p>
          </>
        ) : null}

        {status === 'failed' ? (
          <>
            <div className="error" role="alert">
              {message || 'Telegram connection did not finish.'}
            </div>
            <p className="muted">
              Return to Telegram and start the link again. If you are not signed
              into KDCube in this browser, sign in first and reopen the link.
            </p>
          </>
        ) : null}
      </section>
    </div>
  );
}
