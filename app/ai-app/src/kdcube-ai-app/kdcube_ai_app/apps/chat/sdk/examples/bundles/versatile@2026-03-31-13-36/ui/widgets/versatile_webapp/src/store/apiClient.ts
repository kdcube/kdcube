import { telegramInitData, isTelegramWebApp } from '../telegram/utils';
import { settings } from './settings';
import type {
  ConversationItem,
  ConversationsPayload,
  ExportPayload,
  MemoryEntry,
  MemoryPayload,
} from './types';

const TELEGRAM_OPERATION_ALIASES: Record<string, string> = {
  telegram_profile: 'telegram_profile',
  versatile_webapp_data: 'telegram_versatile_webapp_data',
  conversations_list: 'conversations_list',
  conversations_create: 'telegram_conversations_create',
  conversations_switch: 'telegram_conversations_switch',
  conversations_delete: 'telegram_conversations_delete',
  preferences_canvas_data: 'telegram_memory_canvas_data',
  preferences_canvas_save: 'telegram_memory_canvas_save',
  preferences_canvas_export_excel: 'telegram_memory_canvas_export_excel',
  preferences_canvas_import_excel: 'telegram_memory_canvas_import_excel',
  telegram_user_admin_data: 'telegram_webapp_user_admin_data',
  telegram_user_admin_upsert: 'telegram_webapp_user_admin_upsert',
  telegram_user_admin_delete: 'telegram_webapp_user_admin_delete',
};

const GET_OPERATIONS = new Set(['telegram_profile', 'conversations_list']);

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
  if (isTelegramWebApp() && TELEGRAM_OPERATION_ALIASES[operation]) {
    const publicAlias = TELEGRAM_OPERATION_ALIASES[operation];
    const path = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/public/${publicAlias}`;
    return GET_OPERATIONS.has(operation) ? `${path}${queryString(payload)}` : path;
  }
  const path = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
  return GET_OPERATIONS.has(operation) ? `${path}${queryString(payload)}` : path;
}

function responseAlias(operation: string): string {
  return isTelegramWebApp() && TELEGRAM_OPERATION_ALIASES[operation] ? TELEGRAM_OPERATION_ALIASES[operation] : operation;
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

export function memoryEntries(memory?: MemoryPayload): MemoryEntry[] {
  return memory?.entries || memory?.items || memory?.memories || [];
}

export function conversationItems(conversations?: ConversationsPayload): ConversationItem[] {
  return conversations?.items || conversations?.conversations || [];
}

export function downloadBase64(payload: ExportPayload): void {
  if (!payload.content_b64) return;
  const binary = atob(payload.content_b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: payload.mime || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = payload.filename || 'preferences.xlsx';
  a.click();
  URL.revokeObjectURL(url);
}
