import { FormEvent, useMemo, useState } from 'react';
import type { AuthenticatorRow } from '../../api/types';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import { loadAuthenticators, removeAuthenticator, upsertAuthenticator } from './authenticatorsSlice';

const providerOrder = ['telegram', 'slack', 'oidc', 'google', 'webhook', 'api-key'];

function prettyJson(value: Record<string, unknown> | undefined): string {
  const obj = value && typeof value === 'object' ? value : {};
  return Object.keys(obj).length ? JSON.stringify(obj, null, 2) : '';
}

function parseJsonObject(value: string, field: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  const parsed = JSON.parse(trimmed) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${field} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function newAuthenticatorId(provider: string): string {
  const suffix = Math.random().toString(36).slice(2, 7);
  return `${provider}.${suffix}`;
}

export function AuthenticatorsPanel() {
  const dispatch = useAppDispatch();
  const { items, supportedProviders, busy } = useAppSelector((s) => s.authenticators);
  const providerOptions = useMemo(() => {
    const known = new Set(providerOrder);
    for (const row of supportedProviders) known.add(row.provider);
    for (const row of items) known.add(row.provider);
    return Array.from(known).filter(Boolean);
  }, [items, supportedProviders]);

  const [editing, setEditing] = useState<AuthenticatorRow | null>(null);
  const [provider, setProvider] = useState(providerOptions[0] || 'telegram');
  const [authenticatorId, setAuthenticatorId] = useState(newAuthenticatorId(providerOptions[0] || 'telegram'));
  const [authorityId, setAuthorityId] = useState('');
  const [label, setLabel] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [roleProviding, setRoleProviding] = useState(false);
  const [subjectNamespace, setSubjectNamespace] = useState('');
  const [secretRef, setSecretRef] = useState('identity.authenticators.telegram_default.bot_token');
  const [selectorJson, setSelectorJson] = useState('');
  const [verifierJson, setVerifierJson] = useState('');
  const [propertiesJson, setPropertiesJson] = useState('');
  const [localError, setLocalError] = useState('');

  const beginEdit = (row: AuthenticatorRow) => {
    setEditing(row);
    setProvider(row.provider || 'telegram');
    setAuthenticatorId(row.authenticator_id);
    setAuthorityId(row.authority_id || row.authenticator_id);
    setLabel(row.label || '');
    setEnabled(row.enabled !== false);
    setRoleProviding(row.role_providing === true);
    setSubjectNamespace(row.subject_namespace || '');
    setSecretRef(row.secret_ref || '');
    setSelectorJson(prettyJson(row.selector));
    setVerifierJson(prettyJson(row.verifier));
    setPropertiesJson(prettyJson(row.properties));
    setLocalError('');
  };

  const resetForm = (nextProvider = provider) => {
    setEditing(null);
    setProvider(nextProvider);
    const nextId = newAuthenticatorId(nextProvider);
    setAuthenticatorId(nextId);
    setAuthorityId(nextId);
    setLabel('');
    setEnabled(true);
    setRoleProviding(false);
    setSubjectNamespace('');
    setSecretRef(nextProvider === 'telegram' ? `identity.authenticators.${nextId.replace(/[^A-Za-z0-9_]+/g, '_')}.bot_token` : '');
    setSelectorJson('');
    setVerifierJson('');
    setPropertiesJson('');
    setLocalError('');
  };

  const onProviderChange = (value: string) => {
    if (editing) {
      setProvider(value);
      return;
    }
    resetForm(value);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLocalError('');
    try {
      const selector = parseJsonObject(selectorJson, 'Selector');
      const verifier = parseJsonObject(verifierJson, 'Verifier');
      const properties = parseJsonObject(propertiesJson, 'Properties');
      await dispatch(upsertAuthenticator({
        authenticatorId: authenticatorId.trim(),
        provider: provider.trim(),
        authorityId: authorityId.trim(),
        label: label.trim(),
        enabled,
        roleProviding,
        subjectNamespace: subjectNamespace.trim(),
        secretRef: secretRef.trim(),
        selector,
        verifier,
        properties,
      })).unwrap();
      await dispatch(loadAuthenticators()).unwrap().catch(() => undefined);
      resetForm(provider);
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (row: AuthenticatorRow) => {
    if (row.source !== 'postgres') return;
    await dispatch(removeAuthenticator(row.authenticator_id)).unwrap().catch(() => undefined);
    void dispatch(loadAuthenticators());
    if (editing?.authenticator_id === row.authenticator_id) resetForm(row.provider);
  };

  const rows = useMemo(
    () => items.slice().sort((a, b) => `${a.provider}:${a.authenticator_id}`.localeCompare(`${b.provider}:${b.authenticator_id}`)),
    [items],
  );

  const listPane = (
    <section className="card">
      <p className="muted" style={{ margin: 0 }}>
        Authenticator modules that can prove an incoming request identity.
        Secrets are referenced here, but stored through the platform
        bundle-secret lifecycle.
      </p>

      {localError ? <div className="error" role="alert">{localError}</div> : null}

      <div className="auth-list" style={{ marginTop: 12 }}>
          {rows.length ? rows.map((row) => (
            <div className="auth-row" key={row.authenticator_id}>
              <div>
                <div className="account-title">
                  {row.label || row.authenticator_id}
                  <span className={`badge ${row.enabled === false ? '' : 'badge-ok'}`}>{row.provider}</span>
                  {row.implemented === false ? <span className="badge">planned</span> : null}
                </div>
                <div className="account-sub">
                  <code>{row.authenticator_id}</code>
                  {' · '}
                  authority <code>{row.authority_id || row.authenticator_id}</code>
                  {' · '}
                  {row.source === 'config' ? 'descriptor' : 'postgres'}
                  {' · '}
                  {row.where || 'built-in'}
                  {' · '}
                  {row.role_providing ? 'role-providing' : 'linked identity'}
                  {' · '}
                  secret {row.secret_configured ? 'configured' : 'missing'}: <code>{row.secret_ref || 'none'}</code>
                </div>
              </div>
              <div className="row-actions">
                <button className="btn btn-ghost" type="button" onClick={() => beginEdit(row)}>
                  Edit
                </button>
                {row.source === 'postgres' ? (
                  <button className="btn btn-danger" type="button" disabled={busy} onClick={() => remove(row)}>
                    Remove
                  </button>
                ) : null}
              </div>
            </div>
          )) : (
            <p className="muted">No request authenticators configured yet.</p>
          )}
      </div>
    </section>
  );

  const formPane = (
    <section className="card">
        <form className="form form-flush auth-form" onSubmit={submit}>
          <div className="form-title">{editing ? 'Edit authenticator metadata' : 'Add authenticator metadata'}</div>
          <p className="muted">
            This form writes metadata only. `authority_id` names the identity/grant
            realm, and `authenticator_id` names the verifier row. Put secret values
            in `bundles.secrets.yaml` or the configured secrets provider at the
            `secret_ref` path.
          </p>
          <div className="inline-fields">
            <select className="input input-inline" value={provider} onChange={(event) => onProviderChange(event.target.value)}>
              {providerOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
            <label className="checkbox-line">
              <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
              Enabled
            </label>
            <label className="checkbox-line">
              <input type="checkbox" checked={roleProviding} onChange={(event) => setRoleProviding(event.target.checked)} />
              Role-providing
            </label>
          </div>
          <input className="input" value={authenticatorId} onChange={(event) => setAuthenticatorId(event.target.value)} placeholder="authenticator id, e.g. telegram.support" />
          <input className="input" value={authorityId} onChange={(event) => setAuthorityId(event.target.value)} placeholder="authority id, e.g. telegram.support" />
          <input className="input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="display label" />
          <input className="input" value={subjectNamespace} onChange={(event) => setSubjectNamespace(event.target.value)} placeholder="subject namespace override (optional)" />
          <input className="input" value={secretRef} onChange={(event) => setSecretRef(event.target.value)} placeholder="secret ref, e.g. identity.authenticators.telegram_support.bot_token" />
          <textarea className="input textarea" value={selectorJson} onChange={(event) => setSelectorJson(event.target.value)} placeholder="selector JSON object (optional)" />
          <textarea className="input textarea" value={verifierJson} onChange={(event) => setVerifierJson(event.target.value)} placeholder="verifier JSON object (optional)" />
          <textarea className="input textarea" value={propertiesJson} onChange={(event) => setPropertiesJson(event.target.value)} placeholder="properties JSON object (optional)" />
          <div className="row-actions">
            <button className="btn" type="submit" disabled={busy || !authenticatorId.trim() || !provider.trim()}>
              {editing ? 'Save authenticator' : 'Add authenticator'}
            </button>
            {editing ? (
              <button className="btn btn-ghost" type="button" onClick={() => resetForm(provider)}>
                Cancel
              </button>
            ) : null}
          </div>
        </form>
    </section>
  );

  return (
    <PaneGroup
      panes={[
        { id: 'authenticators', title: 'Request authenticators', content: listPane },
        { id: 'authenticator-form', title: editing ? 'Edit authenticator' : 'Add authenticator', content: formPane },
      ]}
    />
  );
}
