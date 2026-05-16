import { isTelegramWebApp, telegramInitData } from '../telegram/utils';
import { settings } from './settings';

const TELEGRAM_OPERATION_ALIASES: Record<string, string> = {
  telegram_profile: 'telegram_profile',
  copilot_webapp_data: 'telegram_copilot_webapp_data',
  conversations_list: 'conversations_list',
  conversations_create: 'telegram_conversations_create',
  conversations_switch: 'telegram_conversations_switch',
  conversations_delete: 'telegram_conversations_delete',
  telegram_user_admin_data: 'telegram_webapp_user_admin_data',
  telegram_user_admin_upsert: 'telegram_webapp_user_admin_upsert',
  telegram_user_admin_delete: 'telegram_webapp_user_admin_delete',
};

const GET_OPERATIONS = new Set(['telegram_profile', 'conversations_list']);

function telegramOperationAlias(operation: string): string {
  if (TELEGRAM_OPERATION_ALIASES[operation]) return TELEGRAM_OPERATION_ALIASES[operation];
  return operation.startsWith('memories_widget_') ? `telegram_${operation}` : '';
}

function authHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base);
  const initData = telegramInitData();
  const accessToken = settings.getAccessToken();
  const idToken = settings.getIdToken();
  if (initData) headers.set('X-Telegram-Init-Data', initData);
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  if (idToken) headers.set(settings.getIdTokenHeader(), idToken);
  return headers;
}

function queryString(payload: Record<string, unknown>): string {
  const params = new URLSearchParams();
  Object.entries(payload).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    params.set(key, String(value));
  });
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

function operationUrl(operation: string, payload: Record<string, unknown> = {}): string {
  const tenant = encodeURIComponent(settings.getTenant());
  const project = encodeURIComponent(settings.getProject());
  const bundleId = encodeURIComponent(settings.getBundleId());
  const telegramAlias = telegramOperationAlias(operation);
  if (telegramAlias && isTelegramWebApp()) {
    const path = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/public/${telegramAlias}`;
    return GET_OPERATIONS.has(operation) ? `${path}${queryString(payload)}` : path;
  }
  const path = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
  return GET_OPERATIONS.has(operation) ? `${path}${queryString(payload)}` : path;
}

function responseAlias(operation: string): string {
  const telegramAlias = telegramOperationAlias(operation);
  return isTelegramWebApp() && telegramAlias
    ? telegramAlias
    : operation;
}

export async function callOperation<T>(operation: string, payload: Record<string, unknown> = {}): Promise<T> {
  const usePost = !GET_OPERATIONS.has(operation);
  const response = await fetch(operationUrl(operation, payload), {
    method: usePost ? 'POST' : 'GET',
    credentials: 'include',
    headers: authHeaders(usePost ? { 'Content-Type': 'application/json' } : undefined),
    body: usePost ? JSON.stringify({ data: payload }) : undefined,
  });
  const text = await response.text();
  let parsed: unknown = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }
  if (!response.ok) {
    const detail = typeof parsed === 'object' && parsed && 'detail' in parsed
      ? String((parsed as Record<string, unknown>).detail)
      : text || response.statusText;
    throw new Error(detail);
  }
  const alias = responseAlias(operation);
  if (parsed && typeof parsed === 'object' && alias in parsed) {
    return (parsed as Record<string, unknown>)[alias] as T;
  }
  if (parsed && typeof parsed === 'object' && operation in parsed) {
    return (parsed as Record<string, unknown>)[operation] as T;
  }
  return parsed as T;
}

export function assertOk(result: unknown, fallback: string): void {
  if (!result || typeof result !== 'object') return;
  const object = result as Record<string, unknown>;
  if (object.ok !== false) return;
  throw new Error(String(object.error || fallback));
}
