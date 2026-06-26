import { settings } from './settings';

export type MemoryWidgetCallOperation = <T>(
  operation: string,
  payload?: Record<string, unknown>,
) => Promise<T>;

let hostCallOperation: MemoryWidgetCallOperation | null = null;

export function setMemoryWidgetCallOperation(callOperation: MemoryWidgetCallOperation): () => void {
  hostCallOperation = callOperation;
  return () => {
    if (hostCallOperation === callOperation) hostCallOperation = null;
  };
}

// The Telegram proof, when present, arrives on the standard CONFIG_RESPONSE
// handshake (see settings.ts) — the same channel that carries Bearer/cookie
// tokens for browser/scene hosts. No separate message family.
function telegramInitData(): string {
  return settings.getTelegramInitData();
}

function operationUrl(operation: string): string {
  const tenant = encodeURIComponent(settings.getTenant());
  const project = encodeURIComponent(settings.getProject());
  const bundleId = encodeURIComponent(settings.getBundleId());
  return `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
}

export async function callOperation<T>(operation: string, payload: Record<string, unknown> = {}): Promise<T> {
  if (hostCallOperation) {
    return hostCallOperation<T>(operation, payload);
  }
  const headers = settings.authHeaders({ 'Content-Type': 'application/json' });
  const initData = telegramInitData();
  if (initData) headers.set('X-Telegram-Init-Data', initData);
  const authProvider = settings.getAuthProvider();
  const authConnectionId = settings.getAuthConnectionId();
  if (authProvider) headers.set('X-KDCube-Auth-Provider', authProvider);
  if (authConnectionId) headers.set('X-KDCube-Auth-Connection-ID', authConnectionId);
  const response = await fetch(operationUrl(operation), {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify({ data: payload }),
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
  if (parsed && typeof parsed === 'object' && operation in parsed) {
    return (parsed as Record<string, unknown>)[operation] as T;
  }
  return parsed as T;
}
