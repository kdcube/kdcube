export type TabId = 'memory' | 'telegram_admin';

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
}

export interface TelegramProfile {
  ok?: boolean;
  telegram?: { role?: string; is_admin?: boolean };
  permissions?: { show_admin_component?: boolean };
}

export interface WebAppPayload {
  ok?: boolean;
  memory?: MemoryPayload;
  telegram_admin?: { roles?: string[] };
}
