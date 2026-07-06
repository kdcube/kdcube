import { TelegramConversationsPanel } from '@kdcube/telegram-widget';
import { callOperation } from '../store/apiClient';
import type { ConversationsPayload } from '../store/types';

interface ConversationsPageProps {
  conversations?: ConversationsPayload;
  reload: () => Promise<void>;
}

export function ConversationsPage({ conversations, reload }: ConversationsPageProps) {
  return (
    <TelegramConversationsPanel
      conversations={conversations}
      reload={reload}
      callOperation={callOperation}
    />
  );
}
