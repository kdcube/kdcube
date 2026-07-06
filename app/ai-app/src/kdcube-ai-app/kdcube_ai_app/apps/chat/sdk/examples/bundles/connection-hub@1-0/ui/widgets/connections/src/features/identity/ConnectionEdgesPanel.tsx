import { FormEvent, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { PaneGroup } from '../../components/Pane';
import {
  clearTelegramLinkChallenge,
  createTelegramLinkChallenge,
  loadConnectionEdges,
  removeConnectionEdge,
  upsertConnectionEdge,
} from './identitySlice';

const providerOptions = ['google', 'telegram', 'slack', 'bundle'];

interface ConnectionEdgesPanelProps {
  telegramConnectStatus?: 'idle' | 'connecting' | 'connected' | 'failed';
}

export function ConnectionEdgesPanel({ telegramConnectStatus = 'idle' }: ConnectionEdgesPanelProps) {
  const dispatch = useAppDispatch();
  const { platformUserId, edges, telegramChallenge, busy } = useAppSelector((s) => s.identity);
  const [provider, setProvider] = useState(providerOptions[0]);
  const [subject, setSubject] = useState('');
  const [label, setLabel] = useState('');
  const challenge = telegramChallenge?.challenge;
  const challengePending = challenge?.status === 'pending';
  const telegramLinked = challenge?.status === 'completed' || telegramConnectStatus === 'connected';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!provider.trim() || !subject.trim()) return;
    await dispatch(upsertConnectionEdge({ provider: provider.trim(), providerSubject: subject.trim(), label: label.trim() })).unwrap().catch(() => undefined);
    setSubject('');
    setLabel('');
    void dispatch(loadConnectionEdges());
  };

  const remove = async (providerValue: string, subjectValue: string) => {
    await dispatch(removeConnectionEdge({ provider: providerValue, providerSubject: subjectValue })).unwrap().catch(() => undefined);
    void dispatch(loadConnectionEdges());
  };

  const startTelegramLink = async () => {
    await dispatch(createTelegramLinkChallenge()).unwrap().catch(() => undefined);
  };

  const linkedPane = (
    <section className="card">
      <div className="card-head">
        <p className="muted" style={{ margin: 0 }}>
          External identities that may represent this platform user, with
          explicit delegated grants.
        </p>
        {platformUserId ? <span className="badge badge-ok" title={platformUserId}>you</span> : null}
      </div>

      <div className="proof-link">
        <div>
          <div className="form-title">Connect Telegram</div>
          <p className="muted">
            Use this when you are signed into KDCube and want to attach your Telegram account.
          </p>
          <ol className="flow-steps">
            <li className={challenge ? 'done' : 'active'}>Create a short-lived link code here.</li>
            <li className={challengePending ? 'active' : telegramLinked ? 'done' : ''}>Open the Telegram Mini App that owns this bot and enter the code.</li>
            <li className={telegramLinked ? 'done' : ''}>Return here. KDCube will show the account as connected.</li>
          </ol>
        </div>
        <button className="btn" type="button" disabled={busy || challengePending} onClick={startTelegramLink}>
          Start Telegram connection
        </button>
      </div>

      {telegramChallenge?.challenge && (
        <div className={`challenge ${telegramLinked ? 'challenge-ok' : ''}`}>
          <div className="challenge-row">
            <span>Status</span>
            <strong>{telegramLinked ? 'connected' : telegramChallenge.challenge.status}</strong>
          </div>
          <div className="challenge-row muted-row">
            <span>Link code</span>
            <code>{telegramChallenge.challenge.challenge_id}</code>
          </div>
          {telegramLinked ? (
            <p className="muted">Telegram is connected to this KDCube user.</p>
          ) : (
            <p className="muted">
              Open the Telegram Mini App that owns this account and enter this
              link code there.
            </p>
          )}
          {telegramLinked && (
            <button className="btn btn-ghost" type="button" onClick={() => dispatch(clearTelegramLinkChallenge())}>
              Dismiss
            </button>
          )}
        </div>
      )}

      {edges.length ? (
        <ul className="accounts">
          {edges.map((edge) => {
            const source = edge.from || {};
            const providerValue = source.provider || '';
            const subjectValue = source.subject || '';
            return (
            <li className="account" key={edge.edge_id || `${providerValue}:${subjectValue}`}>
              <div>
                <div className="account-title">
                  {source.label || subjectValue}
                  <span className="badge badge-ok">{providerValue}</span>
                </div>
                <div className="account-sub">{subjectValue}</div>
                {edge.grants?.length ? <div className="account-sub">Grants: {edge.grants.join(', ')}</div> : null}
              </div>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => remove(providerValue, subjectValue)}
              >
                Unlink
              </button>
            </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">No connection edges yet.</p>
      )}
    </section>
  );

  const addPane = (
    <section className="card">
      <form className="form form-flush" onSubmit={submit}>
        <p className="muted" style={{ margin: 0 }}>
          Record an external identity (email, Telegram id, external user id)
          that may represent this platform user.
        </p>
        <div className="inline-fields">
          <select className="input input-inline" value={provider} onChange={(event) => setProvider(event.target.value)}>
            {providerOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <input
            className="input"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          placeholder="provider subject, email, telegram id, external user id"
          />
        </div>
        <input
          className="input"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="optional display label"
        />
        <button className="btn" type="submit" disabled={busy || !subject.trim()}>
          Add edge
        </button>
      </form>
    </section>
  );

  return (
    <PaneGroup
      panes={[
        { id: 'edges', title: 'Linked identities', content: linkedPane },
        { id: 'edge-add', title: 'Add connection edge', content: addPane },
      ]}
    />
  );
}
