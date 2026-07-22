import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { clearDcrAllowlistError, loadDcrAllowlist, saveDcrAllowlist } from './dcrAllowlistSlice';

/** Admin editor for the dynamic-client-registration redirect allowlist.
 *  DCR runs before any user authenticates; this list is the fence that keeps
 *  a registered client's redirect pointed at a known app callback or loopback.
 *  Loopback entries (localhost / 127.0.0.1) match any PORT at enforcement
 *  time, but scheme, host, and path must match exactly. Saving writes the
 *  bundle prop; the OAuth adapter picks it up on the next request. */

export function DcrAllowlistCard() {
  const dispatch = useAppDispatch();
  const { uris, effective, defaults, loading, loaded, busy, error, allowed } = useAppSelector(
    (s) => s.dcrAllowlist,
  );
  const [draft, setDraft] = useState<string[] | null>(null);
  const [newUri, setNewUri] = useState('');

  useEffect(() => {
    if (!loaded && !loading) void dispatch(loadDcrAllowlist());
  }, [dispatch, loaded, loading]);

  if (!allowed) return null;

  const rows = draft ?? uris;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(uris);
  const usingDefaults = !rows.length;

  const addUri = () => {
    const value = newUri.trim();
    if (!value) return;
    if (!rows.includes(value)) setDraft([...rows, value]);
    setNewUri('');
  };
  const removeUri = (uri: string) => setDraft(rows.filter((item) => item !== uri));
  const save = async () => {
    await dispatch(saveDcrAllowlist(rows));
    setDraft(null);
  };

  return (
    <section className="card">
      <div className="account-title">
        <strong>Client registration redirect allowlist</strong>
        <span className="badge">admin only</span>
        {usingDefaults ? <span className="badge">built-in defaults</span> : null}
      </div>
      <p className="muted">
        External clients that are not pre-listed register through dynamic client registration —
        before any user has signed in. A registration is accepted only if its redirect URI is on
        this list, so an authorization code can only be delivered to a known app callback or to
        the user&apos;s own machine. Loopback entries (<code>localhost</code>, <code>127.0.0.1</code>)
        match any port; scheme, host, and path must match exactly. An empty list falls back to the
        built-in defaults.
      </p>
      {error ? (
        <div className="error" role="alert" onClick={() => dispatch(clearDcrAllowlistError())}>{error}</div>
      ) : null}
      {loading && !loaded ? (
        <p className="muted" style={{ margin: 0 }}>Loading the allowlist…</p>
      ) : (
        <>
          <div className="access-map-entries">
            {(usingDefaults ? effective : rows).map((uri) => (
              <div className="access-map-entry" key={uri}>
                <span className="access-map-entry-op"><code>{uri}</code></span>
                {usingDefaults ? (
                  <span className="badge">default</span>
                ) : (
                  <button
                    className="btn btn-ghost"
                    type="button"
                    disabled={busy}
                    onClick={() => removeUri(uri)}
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="row-actions" style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <input
              className="input"
              value={newUri}
              onChange={(event) => setNewUri(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') addUri();
              }}
              placeholder="https://app.example/callback or http://localhost/callback"
            />
            <button className="btn btn-ghost" type="button" disabled={busy || !newUri.trim()} onClick={addUri}>
              Add
            </button>
            <button className="btn" type="button" disabled={busy || !dirty} onClick={() => void save()}>
              {busy ? 'Saving…' : 'Save allowlist'}
            </button>
            {dirty ? (
              <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDraft(null)}>
                Discard
              </button>
            ) : null}
          </div>
          {usingDefaults && defaults.length ? (
            <p className="muted" style={{ marginBottom: 0 }}>
              Add an entry to replace the defaults with an explicit list.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
