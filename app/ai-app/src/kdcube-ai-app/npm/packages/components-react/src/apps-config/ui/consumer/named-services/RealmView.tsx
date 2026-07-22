/**
 * One named-service namespace for an agent: the operations wired into its roster
 * (`connected`) and the operations the provider serves but the agent hasn't wired
 * yet (`available` — the "connect more" set). The connect action is P2; here the
 * available ops are shown as connectable candidates.
 */
import type { NamedServiceRealm, NamedServiceOp } from '@kdcube/components-core/apps-config';
import { Badge } from '../../primitives/Badge.tsx';

function OpChip({ op, connected }: { op: NamedServiceOp; connected: boolean }) {
  return (
    <span className={`ac-op${connected ? '' : ' ac-op--available'}`} title={op.description || op.op}>
      <code>{op.op}</code>
      {op.tool && <span className="ac-op__tool">{op.tool}</span>}
    </span>
  );
}

export function RealmView({ realm }: { realm: NamedServiceRealm }) {
  const accounts = realm.connectedAccounts || [];
  return (
    <div className="ac-realm">
      <div className="ac-realm__head">
        <Badge tone="success">{realm.namespace}</Badge>
        {realm.alias && realm.alias !== realm.namespace && (
          <span className="ac-kv">alias: {realm.alias}</span>
        )}
        {accounts.length > 0 && (
          <span className="ac-kv">
            accounts: {accounts.map((a) => a.label || a.accountId).join(', ')}
          </span>
        )}
      </div>

      {realm.connected.length > 0 && (
        <div className="ac-realm__group">
          <span className="ac-realm__grouplabel">connected</span>
          <div className="ac-ops">
            {realm.connected.map((op) => (
              <OpChip key={op.op} op={op} connected />
            ))}
          </div>
        </div>
      )}

      {realm.available.length > 0 && (
        <div className="ac-realm__group">
          <span className="ac-realm__grouplabel">available to connect</span>
          <div className="ac-ops">
            {realm.available.map((op) => (
              <OpChip key={op.op} op={op} connected={false} />
            ))}
          </div>
        </div>
      )}

      {realm.connected.length === 0 && realm.available.length === 0 && (
        <p className="ac-note ac-note--muted">No operations advertised.</p>
      )}
    </div>
  );
}
