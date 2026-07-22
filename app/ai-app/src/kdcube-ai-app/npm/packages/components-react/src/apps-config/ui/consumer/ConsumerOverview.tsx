/** The as_consumer section: the agents this app runs + shared MCP services. */
import type { ConsumerOverview as ConsumerOverviewModel } from '@kdcube/components-core/apps-config';
import { Section } from '../primitives/Section.tsx';
import { Badge } from '../primitives/Badge.tsx';
import { AgentCard } from './AgentCard.tsx';

export function ConsumerOverview({ consumer }: { consumer: ConsumerOverviewModel }) {
  const { agents, mcpServices } = consumer;
  return (
    <Section
      title="Agents"
      count={agents.length}
      hint="The agents configured in this app and their settings. Expand an agent for its named services, MCP connections, tools, and models."
    >
      {mcpServices.length > 0 && (
        <div className="ac-consumer__services">
          <span className="ac-kv">shared MCP services:</span>
          {mcpServices.map((s) => (
            <Badge key={s} tone="muted">{s}</Badge>
          ))}
        </div>
      )}

      {agents.length === 0 ? (
        <p className="ac-note ac-note--muted">This app defines no agents.</p>
      ) : (
        <div className="ac-agents">
          {agents.map((a) => (
            <AgentCard key={a.id} agent={a} />
          ))}
        </div>
      )}
    </Section>
  );
}
