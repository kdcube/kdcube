import { useEffect, useState } from 'react';
import { callOperation } from '../store/apiClient';
import type { TelegramIdentityLinkResult } from '../store/types';

interface ConnectionLinkPageProps {
  challengeId?: string;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function ConnectionLinkPage({ challengeId }: ConnectionLinkPageProps) {
  const [draft, setDraft] = useState(challengeId || '');
  const [result, setResult] = useState<TelegramIdentityLinkResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(Boolean(challengeId));
  const telegramFirst = !challengeId;
  const linked = Boolean(result?.link?.platform_user_id || result?.challenge?.platform_user_id);
  const readyForKdcube = telegramFirst && Boolean(result?.ok && result?.platform_claim_url);

  async function startTelegramFirstLink() {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const response = await callOperation<TelegramIdentityLinkResult>('telegram_identity_link_start', {});
      if (response?.ok === false) {
        setError(response.message || response.error || 'Telegram link start failed');
      } else {
        setResult(response);
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }

  async function complete(nextChallengeId = draft) {
    const value = String(nextChallengeId || '').trim();
    if (!value) {
      setError('Challenge id is required');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const response = await callOperation<TelegramIdentityLinkResult>('telegram_identity_link_complete', {
        challenge_id: value,
      });
      if (response?.ok === false) {
        setError(response.message || response.error || 'Telegram link failed');
      } else {
        setResult(response);
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!challengeId) {
      void startTelegramFirstLink();
      return undefined;
    }
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError('');
      try {
        const response = await callOperation<TelegramIdentityLinkResult>('telegram_identity_link_complete', {
          challenge_id: challengeId,
        });
        if (cancelled) return;
        if (response?.ok === false) {
          setError(response.message || response.error || 'Telegram link failed');
        } else {
          setResult(response);
        }
      } catch (e) {
        if (!cancelled) setError(message(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [challengeId]);

  return (
    <section className="link-card">
      <div className="link-badge">Connection Hub</div>
      <h1>Link Telegram</h1>
      <p className="muted">
        {telegramFirst
          ? 'Start here in Telegram, then open KDCube to attach this Telegram account to your signed-in KDCube user.'
          : 'KDCube sent you here to confirm this Telegram account.'}
      </p>

      <ol className="link-steps">
        <li className={result?.ok ? 'done' : 'active'}>
          <strong>1. Confirm this Telegram account</strong>
          <span>Telegram tells KDCube which account is open now.</span>
        </li>
        <li className={linked ? 'done' : readyForKdcube ? 'active' : ''}>
          <strong>2. Open KDCube</strong>
          <span>Sign in there and finish connecting the account.</span>
        </li>
        <li className={linked ? 'done' : ''}>
          <strong>3. Connected</strong>
          <span>After KDCube confirms it, Telegram is linked.</span>
        </li>
      </ol>

      {(challengeId || result?.challenge?.challenge_id || draft.trim()) && !readyForKdcube && (
        <div className="link-field">
          <span>Link code</span>
          {challengeId || result?.challenge?.challenge_id ? (
            <code>{challengeId || result?.challenge?.challenge_id}</code>
          ) : (
            <input
              className="link-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Paste link code"
            />
          )}
        </div>
      )}

      {telegramFirst && !result?.ok && (
        <button className="link-button" type="button" disabled={loading} onClick={() => void startTelegramFirstLink()}>
          1. Confirm this Telegram account
        </button>
      )}
      {readyForKdcube && (
        <a className="link-button link-anchor" href={result.platform_claim_url} target="_blank" rel="noreferrer">
          2. Open KDCube to finish
        </a>
      )}
      {telegramFirst && !result?.platform_claim_url && draft.trim() && (
        <button className="link-button secondary" type="button" disabled={loading} onClick={() => void complete()}>
          Complete pasted KDCube link code
        </button>
      )}

      {loading && <div className="notice">{challengeId ? 'Connecting this Telegram account…' : 'Confirming this Telegram account…'}</div>}
      {error && <div className="notice error">{error}</div>}
      {!loading && readyForKdcube && (
        <div className="notice success">
          Telegram is confirmed. Tap “Open KDCube to finish”, then sign in there to connect it.
        </div>
      )}
      {result?.ok && !result.platform_claim_url && (
        <div className="notice success">
          {result.link?.platform_user_id || result.challenge?.platform_user_id ? (
            <>
              Connected. This Telegram account is linked to KDCube user <code>{result.link?.platform_user_id || result.challenge?.platform_user_id}</code>.
            </>
          ) : (
            <>Telegram is confirmed. Return to KDCube to finish connecting it.</>
          )}
        </div>
      )}
      {result?.link && (
        <div className="link-field">
          <span>Telegram</span>
          <code>{result.link.provider_subject}</code>
        </div>
      )}
    </section>
  );
}
