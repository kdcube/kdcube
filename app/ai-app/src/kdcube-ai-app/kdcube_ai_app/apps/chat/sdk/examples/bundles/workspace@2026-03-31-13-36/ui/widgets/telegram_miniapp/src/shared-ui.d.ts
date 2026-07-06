declare module '@kdcube/telegram-widget' {
  import type {
    ConversationsPayload,
    TelegramUser,
    TelegramWidgetCallOperation,
  } from './store/types';

  export type {
    ConversationsPayload,
    TelegramUser,
    TelegramWidgetCallOperation,
  };

  export function TelegramConversationsPanel(props: {
    conversations?: ConversationsPayload;
    reload: () => Promise<void>;
    callOperation: TelegramWidgetCallOperation;
    createOperation?: string;
    switchOperation?: string;
    deleteOperation?: string;
    title?: string;
  }): JSX.Element;

  export function TelegramPendingApproval(props: {
    title?: string;
    message?: string;
    detail?: string;
  }): JSX.Element;
}
