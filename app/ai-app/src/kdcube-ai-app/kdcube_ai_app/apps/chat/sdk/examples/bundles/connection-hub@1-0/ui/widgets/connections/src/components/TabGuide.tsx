import { useState } from 'react';
import type { ConnectionsTab } from './AppShell';

// One short line per tab saying what the user can do there, with an
// expandable Learn more list linking the public docs/recipes on GitHub.

const DOCS = 'https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs';

interface GuideLink {
  label: string;
  href: string;
}

interface Guide {
  summary: string;
  links: GuideLink[];
}

const GUIDES: Record<ConnectionsTab, Guide> = {
  identity: {
    summary:
      'Link alternate identities — such as a Telegram account — to your KDCube user, '
      + 'so KDCube recognizes the same person across channels and routes their data accordingly.',
    links: [
      {
        label: 'Link KDCube from an external channel',
        href: `${DOCS}/recipes/connections/link-from-external-channel-README.md`,
      },
      {
        label: 'Use connected identities in a product feature',
        href: `${DOCS}/recipes/connections/use-connected-identities-in-product-feature-README.md`,
      },
      {
        label: 'Telegram integration',
        href: `${DOCS}/recipes/connections/integrations/telegram-README.md`,
      },
      {
        label: 'How identity links are stored (connection edges)',
        href: `${DOCS}/sdk/solutions/connections/connection-edges/connection-edges-README.md`,
      },
    ],
  },
  delegatedToKdcube: {
    summary:
      'Connect your external accounts — Gmail, Slack, iCloud Mail — so KDCube apps can act on them '
      + 'with exactly the access you approve. Reconnect, add access, or disconnect any account here.',
    links: [
      {
        label: 'Connect Gmail',
        href: `${DOCS}/recipes/connections/integrations/google-gmail-README.md`,
      },
      {
        label: 'Connect Slack',
        href: `${DOCS}/recipes/connections/integrations/slack-README.md`,
      },
      {
        label: 'Mail accounts as a named service',
        href: `${DOCS}/recipes/connections/integrations/mail-named-service-README.md`,
      },
      {
        label: 'How delegated provider accounts work',
        href: `${DOCS}/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md`,
      },
    ],
  },
  providerConnections: {
    summary:
      'Connect provider accounts — Slack, Gmail — through KDCube\'s connector apps. '
      + 'Each access tier states what it grants; a connect asks the provider for exactly the tiers you check, '
      + 'and reconnecting an account adds tiers on top of the ones it already holds.',
    links: [
      {
        label: 'Connect Gmail',
        href: `${DOCS}/recipes/connections/integrations/google-gmail-README.md`,
      },
      {
        label: 'Connect Slack',
        href: `${DOCS}/recipes/connections/integrations/slack-README.md`,
      },
      {
        label: 'Connection Hub solution map',
        href: `${DOCS}/sdk/solutions/connections/connection-hub-solution-README.md`,
      },
    ],
  },
  delegatedAccess: {
    summary:
      'Access you granted to automations and external clients. Create bounded tokens for your own '
      + 'scripts and jobs, review apps connected through OAuth, and revoke any grant — revocation is immediate.',
    links: [
      {
        label: 'Create delegated automation access',
        href: `${DOCS}/recipes/connections/create-delegated-automation-access-README.md`,
      },
      {
        label: 'Delegate a KDCube service to an external client',
        href: `${DOCS}/recipes/connections/delegate-kdcube-service-to-external-client-README.md`,
      },
      {
        label: 'How delegated connections and grants work',
        href: `${DOCS}/sdk/solutions/connections/delegated-connections/delegated-connections-README.md`,
      },
    ],
  },
  authenticators: {
    summary:
      'Operator surface: configure the authenticator modules that prove incoming request identities. '
      + 'Metadata only — secret values stay in the bundle-secret lifecycle and are referenced here by secret_ref.',
    links: [
      {
        label: 'Request authenticators',
        href: `${DOCS}/sdk/solutions/connections/request-authenticators/request-authenticators-README.md`,
      },
      {
        label: 'Connection Hub solution map',
        href: `${DOCS}/sdk/solutions/connections/connection-hub-solution-README.md`,
      },
    ],
  },
};

export function TabGuide({ tab }: { tab: ConnectionsTab }) {
  const [open, setOpen] = useState(false);
  const guide = GUIDES[tab];
  if (!guide) return null;
  return (
    <div className="tab-guide">
      <p className="tab-guide-summary">
        {guide.summary}{' '}
        <button className="tab-guide-toggle" type="button" onClick={() => setOpen((v) => !v)}>
          {open ? 'Less' : 'Learn more'}
        </button>
      </p>
      {open ? (
        <ul className="tab-guide-links">
          {guide.links.map((link) => (
            <li key={link.href}>
              <a href={link.href} target="_blank" rel="noopener noreferrer">
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
