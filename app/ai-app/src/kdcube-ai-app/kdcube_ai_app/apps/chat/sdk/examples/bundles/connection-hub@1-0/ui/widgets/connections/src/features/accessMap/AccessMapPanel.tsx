import { useEffect } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { clearAccessMapError, loadAccessMap } from './accessMapSlice';
import { DcrAllowlistCard } from '../dcrAllowlist/DcrAllowlistCard';
import type { AccessMapNamespace, AccessMapResource } from '../../api/types';

/** Read-only admin map of what this deployment delegates to external
 *  clients: each OAuth RESOURCE, the named-service NAMESPACES it exposes,
 *  each namespace's operations with their GRANTS, the grant vocabulary
 *  itself, and the provider-backed connected-account claims beside it.
 *  Everything renders from live config (`config.connections.*`); changing
 *  it means editing the descriptor — this view is deliberately one-way,
 *  with ONE exception: the DCR redirect allowlist card is editable, because
 *  connecting a new native client (a new loopback callback path) is a
 *  runtime admin action, not a redeploy. */

function GrantChips({ grants }: { grants: string[] }) {
  if (!grants.length) return null;
  return (
    <span className="grant-chips">
      {grants.map((grant) => (
        <code className="badge" key={grant}>{grant}</code>
      ))}
    </span>
  );
}

function NamespaceBlock({ namespace }: { namespace: AccessMapNamespace }) {
  return (
    <div className="access-map-namespace">
      <div className="account-title">
        <code>{namespace.namespace}</code>
        {namespace.label ? <span>{namespace.label}</span> : null}
        {namespace.authority_id ? <span className="badge">{namespace.authority_id}</span> : null}
        <GrantChips grants={namespace.grants} />
      </div>
      {namespace.description ? <div className="account-sub">{namespace.description}</div> : null}
      <div className="access-map-entries">
        {namespace.entries.map((entry) => (
          <div className="access-map-entry" key={`${namespace.namespace}.${entry.tool}`}>
            <span className="access-map-entry-op">
              {entry.label ? <span>{entry.label}</span> : null}
              <code>{entry.operation}</code>
            </span>
            <GrantChips grants={entry.grants} />
          </div>
        ))}
        {!namespace.entries.length ? <p className="muted">No single-operation entries declared.</p> : null}
      </div>
    </div>
  );
}

function ResourceCard({ resource }: { resource: AccessMapResource }) {
  return (
    <section className="card">
      <div className="account-title">
        <strong>{resource.label || resource.resource}</strong>
        {resource.admin_only ? <span className="badge">admin only</span> : null}
        <GrantChips grants={resource.grants} />
      </div>
      <div className="account-sub"><code>{resource.resource}</code></div>
      {resource.description ? <p className="muted">{resource.description}</p> : null}
      {resource.tools.length ? (
        <div className="access-map-entries" style={{ marginTop: 8 }}>
          {resource.tools.map((tool) => (
            <div className="access-map-entry" key={tool.name}>
              <span className="access-map-entry-op">
                {tool.label ? <span>{tool.label}</span> : null}
                <code>{tool.name}</code>
              </span>
              <GrantChips grants={tool.grants} />
            </div>
          ))}
        </div>
      ) : null}
      {resource.namespaces.map((namespace) => (
        <NamespaceBlock key={namespace.namespace} namespace={namespace} />
      ))}
    </section>
  );
}

export function AccessMapPanel() {
  const dispatch = useAppDispatch();
  const { data, loading, loaded, error, allowed } = useAppSelector((s) => s.accessMap);

  useEffect(() => {
    if (!loaded && !loading) void dispatch(loadAccessMap());
  }, [dispatch, loaded, loading]);

  if (!allowed) {
    return (
      <section className="card">
        <p className="muted" style={{ margin: 0 }}>
          The delegated access map is available to platform administrators only.
        </p>
      </section>
    );
  }
  if (loading || !loaded) {
    return (
      <section className="card">
        <p className="muted" style={{ margin: 0 }}>Loading the access map…</p>
      </section>
    );
  }

  const grants = data?.grants ?? [];
  const resources = data?.resources ?? [];
  const providers = data?.providers ?? [];
  const unknown = data?.unknown_grants ?? [];

  return (
    <div className="access-map-body">
      {error ? (
        <div className="error" role="alert" onClick={() => dispatch(clearAccessMapError())}>{error}</div>
      ) : null}
      <section className="card">
        <div className="account-title">
          <strong>Delegated access map</strong>
          <span className={`badge ${data?.enabled ? 'badge-ok' : ''}`}>
            {data?.enabled ? 'delegated OAuth enabled' : 'delegated OAuth disabled'}
          </span>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          What this deployment exposes to external delegated clients: each resource, the named-service
          namespaces under it, and the grants gating every operation. Read-only — resolved from the
          app configuration (<code>connections.delegated_credentials.oauth</code>); change it by editing
          the descriptor.
        </p>
        {unknown.length ? (
          <div className="error" role="alert" style={{ marginTop: 8 }}>
            Referenced but not declared in the grant vocabulary: {unknown.join(', ')}
          </div>
        ) : null}
      </section>

      <DcrAllowlistCard />

      <section className="card">
        <div className="account-title"><strong>Grant vocabulary</strong></div>
        <div className="access-map-entries">
          {grants.map((grant) => (
            <div className="access-map-entry" key={grant.grant}>
              <span className="access-map-entry-op">
                <span>{grant.label || grant.grant}</span>
                <code>{grant.grant}</code>
                {grant.admin_only ? <span className="badge">admin only</span> : null}
              </span>
              <span className="access-map-entry-detail">
                {grant.description ? <span className="muted">{grant.description}</span> : null}
                <GrantChips grants={grant.delegable_roles ?? []} />
              </span>
            </div>
          ))}
          {!grants.length ? <p className="muted">No grant vocabulary declared.</p> : null}
        </div>
      </section>

      {resources.map((resource) => (
        <ResourceCard key={resource.resource} resource={resource} />
      ))}

      <section className="card">
        <div className="account-title"><strong>Provider-backed claims</strong></div>
        <p className="muted">
          Connected-account claims beside the grants: realms like mail and Slack execute through the
          approving user&apos;s connected accounts, gated by these claims.
        </p>
        {providers.map((provider) => (
          <div className="access-map-namespace" key={provider.provider_id}>
            <div className="account-title">
              <strong>{provider.label || provider.provider_id}</strong>
              <span className={`badge ${provider.enabled ? 'badge-ok' : ''}`}>
                {provider.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>
            {provider.connector_apps.map((app) => (
              <div className="access-map-entry" key={app.id}>
                <span className="access-map-entry-op">
                  <span>{app.label || app.id}</span>
                  <code>{app.id}</code>
                </span>
                <GrantChips grants={app.allowed_claims} />
              </div>
            ))}
            <div className="access-map-entries">
              {provider.claims.map((claim) => (
                <div className="access-map-entry" key={claim.claim}>
                  <span className="access-map-entry-op">
                    <span>{claim.label || claim.claim}</span>
                    <code>{claim.claim}</code>
                  </span>
                  {claim.description ? <span className="muted">{claim.description}</span> : null}
                </div>
              ))}
            </div>
          </div>
        ))}
        {!providers.length ? <p className="muted">No provider-backed connector apps configured.</p> : null}
      </section>
    </div>
  );
}
