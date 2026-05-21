export type TabId = 'memory' | 'conversations' | 'events' | 'telegram_admin';

export type TelegramWidgetCallOperation = <T>(
  operation: string,
  payload?: Record<string, unknown>,
) => Promise<T>;

export interface AppSettings {
  baseUrl: string;
  accessToken: string | null;
  idToken: string | null;
  idTokenHeader: string;
  defaultTenant: string;
  defaultProject: string;
  defaultAppBundleId: string;
}

export interface RouteContext {
  tenant: string;
  project: string;
  bundleId: string;
  widgetAlias: string;
  widgetPath: string;
}

export interface MemoryPayload {
  ok?: boolean;
  count?: number;
  memories?: unknown[];
  error?: string;
  message?: string;
}

export interface ConversationItem {
  conversation_id: string;
  title?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationsPayload {
  active_conversation_id?: string;
  items?: ConversationItem[];
  conversations?: ConversationItem[];
  count?: number;
  telegram_user_id?: string;
  kdcube_user_id?: string;
  error?: { code?: string; message?: string };
}

export interface TelegramUser {
  telegram_user_id: string;
  telegram_chat_id?: string;
  telegram_username?: string;
  kdcube_user_id?: string;
  role?: string;
  conversation_id?: string;
  notes?: string;
}

export interface TelegramAdminPayload {
  ok?: boolean;
  roles?: string[];
  users?: TelegramUser[];
  error?: string;
  current_kdcube_user_id?: string;
  current_user?: {
    user_id?: string;
    username?: string;
    roles?: string[];
  };
}

export interface TelegramProfile {
  ok?: boolean;
  telegram?: {
    user_id?: string;
    username?: string;
    role?: string;
    allowed?: boolean;
    is_admin?: boolean;
    conversation_id?: string;
  };
  permissions?: {
    can_use_chatbot?: boolean;
    can_use_widget?: boolean;
    show_admin_component?: boolean;
  };
}

export interface CopilotEventItem {
  event_id: string;
  timestamp?: number;
  timestamp_iso?: string;
  bundle_id?: string;
  source?: string;
  type?: string;
  socket_event?: string | null;
  route?: string | null;
  agent?: string | null;
  step?: string | null;
  status?: string | null;
  title?: string | null;
  data?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  context?: {
    tenant?: string | null;
    project?: string | null;
    user?: string | null;
    request_id?: string | null;
    session_id?: string | null;
    conversation_id?: string | null;
    turn_id?: string | null;
  };
  privacy?: Record<string, unknown>;
}

export interface CopilotEventsPayload {
  ok?: boolean;
  bundle_id?: string;
  events?: CopilotEventItem[];
  count?: number;
  limit?: number;
  by_type?: Record<string, number>;
  by_source?: Record<string, number>;
  storage_path?: string;
  error?: string;
}

export interface WebAppPayload {
  ok?: boolean;
  active_tab?: string;
  memory?: MemoryPayload;
  conversations?: ConversationsPayload;
  events?: CopilotEventsPayload;
  telegram_admin?: { roles?: string[] };
  permissions?: { show_admin_component?: boolean };
}
