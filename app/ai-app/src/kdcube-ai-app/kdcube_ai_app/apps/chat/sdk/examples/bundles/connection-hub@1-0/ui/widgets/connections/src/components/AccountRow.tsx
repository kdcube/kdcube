import type { ReactNode } from 'react';

export type AccountStatusTone = 'ok' | 'warn' | 'error';

export function ConnectedBadge({ label = 'connected', tone = 'ok' }: { label?: string; tone?: AccountStatusTone }) {
  const cls = tone === 'error' ? 'badge-error' : tone === 'warn' ? 'badge-warn' : 'badge-ok';
  return <span className={`badge ${cls}`}>{label}</span>;
}

export interface AccountRowProps {
  title: string;
  subtitle?: string;
  statusLabel?: string;
  statusTone?: AccountStatusTone;
  detail?: string;
  lastError?: string;
  highlighted?: boolean;
  busy: boolean;
  actions?: ReactNode;
  onDisconnect: () => void;
}

export function AccountRow({
  title,
  subtitle,
  statusLabel,
  statusTone,
  detail,
  lastError,
  highlighted,
  busy,
  actions,
  onDisconnect,
}: AccountRowProps) {
  return (
    <li className={`account${highlighted ? ' account-highlight' : ''}`}>
      <div className="account-info">
        <div className="account-title">
          {title} <ConnectedBadge label={statusLabel || 'connected'} tone={statusTone || 'ok'} />
        </div>
        {subtitle ? <div className="account-sub">{subtitle}</div> : null}
        {detail ? <div className="account-detail">{detail}</div> : null}
        {lastError ? <div className="account-error">{lastError}</div> : null}
      </div>
      <div className="account-actions">
        {actions}
        <button className="btn btn-danger" onClick={onDisconnect} disabled={busy}>
          Disconnect
        </button>
      </div>
    </li>
  );
}
