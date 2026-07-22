/**
 * One agent, expandable. Renders its configured tools, MCP connections, and the
 * named-service namespaces it consumes — straight from the app's props (no extra
 * request), so expanding is instant. Per-account consent / connected-account
 * state is a later enrichment.
 */
import { useState } from 'react';
import type { AgentConfig } from '@kdcube/components-core/apps-config';
import { Badge } from '../primitives/Badge.tsx';
import { ConfigTree } from '../primitives/ConfigTree.tsx';

export function AgentCard({ agent }: { agent: AgentConfig }) {
  const [open, setOpen] = useState(false);
  const hasContent =
    agent.namedServices.length + agent.mcp.length + agent.tools.length + agent.models.length > 0;

  return (
    <div className={`ac-agent${open ? ' is-open' : ''}`}>
      <button
        type="button"
        className="ac-agent__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="ac-agent__caret" aria-hidden>{open ? '▾' : '▸'}</span>
        <span className="ac-agent__id">{agent.id}</span>
        {agent.isDefault && <Badge tone="accent">default</Badge>}
        <span className="ac-agent__summary">
          {agent.namedServices.length > 0 && (
            <Badge tone="success">{agent.namedServices.length} named svc</Badge>
          )}
          {agent.mcp.length > 0 && <Badge tone="warn">{agent.mcp.length} mcp</Badge>}
          {agent.tools.length > 0 && <Badge tone="muted">{agent.tools.length} tool groups</Badge>}
        </span>
      </button>

      {open && (
        <div className="ac-agent__body">
          {!hasContent && (
            <p className="ac-note ac-note--muted">No tools or services configured for this agent.</p>
          )}

          {agent.namedServices.length > 0 && (
            <div className="ac-agent__block">
              <h5 className="ac-agent__blocktitle">Named services</h5>
              {agent.namedServices.map((ns) => (
                <div key={ns.namespace} className="ac-realm">
                  <div className="ac-realm__head">
                    <Badge tone="success">{ns.namespace}</Badge>
                    {ns.alias && ns.alias !== ns.namespace && (
                      <span className="ac-kv">via {ns.alias}</span>
                    )}
                  </div>
                  <div className="ac-ops">
                    {ns.operations.map((op) => (
                      <span key={op} className="ac-op"><code>{op}</code></span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {agent.mcp.length > 0 && (
            <div className="ac-agent__block">
              <h5 className="ac-agent__blocktitle">MCP connections</h5>
              <div className="ac-chiprow">
                {agent.mcp.map((m) => (
                  <span key={m.alias} className="ac-mcp" title={m.serverId}>
                    <Badge tone={m.delegated ? 'warn' : 'neutral'}>{m.alias}</Badge>
                    {m.delegated && <span className="ac-kv">delegated</span>}
                    {m.scopes && m.scopes.length > 0 && (
                      <span className="ac-kv">{m.scopes.join(', ')}</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {agent.tools.length > 0 && (
            <div className="ac-agent__block">
              <h5 className="ac-agent__blocktitle">Tools</h5>
              {agent.tools.map((g) => (
                <div key={g.alias} className="ac-toolgroup">
                  <span className="ac-toolgroup__alias">
                    {g.alias} <span className="ac-kv">{g.kind}</span>
                  </span>
                  <div className="ac-chiprow">
                    {g.tools.map((t) => (
                      <span key={t.name} className="ac-tool" title={t.description || t.name}>
                        <code>{t.name}</code>
                        {t.strategy && t.strategy.length > 0 && (
                          <span className="ac-tool__strat">{t.strategy.join('/')}</span>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {agent.models.length > 0 && (
            <div className="ac-agent__block">
              <h5 className="ac-agent__blocktitle">Models</h5>
              <div className="ac-chiprow">
                {agent.models.map((m) => (
                  <Badge key={m.model} tone={m.model === agent.defaultModel ? 'accent' : 'muted'}>
                    {m.label || m.model}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {(agent.capabilityProvider || agent.maxTokens || agent.additionalInstructions) && (
            <div className="ac-agent__block">
              <h5 className="ac-agent__blocktitle">Settings</h5>
              <div className="ac-kvlist">
                {agent.capabilityProvider && (
                  <div><span className="ac-kv">capability provider:</span> <code>{agent.capabilityProvider}</code></div>
                )}
                {agent.maxTokens != null && (
                  <div><span className="ac-kv">max tokens:</span> <code>{agent.maxTokens}</code></div>
                )}
                {agent.additionalInstructions && (
                  <div><span className="ac-kv">additional instructions:</span> {agent.additionalInstructions}</div>
                )}
              </div>
            </div>
          )}

          <details className="ac-agent__raw">
            <summary>Raw agent config</summary>
            <ConfigTree value={agent.raw} />
          </details>
        </div>
      )}
    </div>
  );
}
