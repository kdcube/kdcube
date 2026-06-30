export type TabId = 'memory' | 'conversations' | 'connections';

export type TelegramWidgetCallOperation = <T>(
  operation: string,
  payload?: Record<string, unknown>,
) => Promise<T>;

export interface AppSettings {
  baseUrl: string;
  accessToken: string | null;
  idToken: string | null;
  idTokenHeader: string;
  authContextHeaders: Record<string, string>;
  defaultTenant: string;
  defaultProject: string;
  defaultAppBundleId: string;
  connectionHubBundleId: string;
}

export interface RouteContext {
  tenant: string;
  project: string;
  bundleId: string;
  widgetAlias: string;
  widgetPath: string;
  publicRoute: boolean;
}

export interface MemoryEntry {
  id?: string;
  memory?: string;
  context?: string;
  kind?: string;
  status?: string;
  tier?: number;
  pinned?: boolean;
  labels?: string[];
  keywords?: string[];
  confidence_score?: number;
  importance_score?: number;
  salience_score?: number;
  evidence_count?: number;
  updated_at?: string;
  last_event_at?: string;
  score?: number;
}

export interface MemoryPayload {
  ok?: boolean;
  user_id?: string;
  memories?: MemoryEntry[];
  count?: number;
  scope?: {
    user_id?: string;
    bundle_id?: string;
    filter?: string;
  };
  filters?: {
    query?: string;
    scope_filter?: string;
    status?: string;
  };
  capabilities?: {
    can_write?: boolean;
    allow_all_user_memories?: boolean;
  };
  has_more?: boolean;
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
  };
  connection?: {
    linked?: boolean;
    required?: boolean;
    provider?: string;
    provider_subject?: string;
    platform_user_id?: string;
    error?: string;
    message?: string;
  };
  authContext?: {
    headers?: Record<string, unknown>;
  };
  connections?: {
    connection_hub?: {
      bundle_id?: string;
    };
  };
}

export interface WebAppPayload {
  ok?: boolean;
  active_tab?: string;
  memory?: MemoryPayload;
  conversations?: ConversationsPayload;
  authContext?: {
    headers?: Record<string, unknown>;
  };
  connections?: {
    connection_hub?: {
      bundle_id?: string;
    };
  };
  connection?: TelegramProfile['connection'];
  auth?: {
    provider?: string;
    authority_id?: string;
    authorityId?: string;
    authenticator_id?: string;
    authenticatorId?: string;
  };
}

export interface ExportPayload {
  ok?: boolean;
  filename?: string;
  mime?: string;
  content_b64?: string;
  error?: string;
}
