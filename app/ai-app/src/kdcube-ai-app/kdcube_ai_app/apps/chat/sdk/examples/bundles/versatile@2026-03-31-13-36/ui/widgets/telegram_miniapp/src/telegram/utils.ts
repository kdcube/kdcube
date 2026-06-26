import './types';

export function telegramInitData(): string {
  return window.Telegram?.WebApp?.initData || '';
}

export function telegramStartParam(): string {
  return window.Telegram?.WebApp?.initDataUnsafe?.start_param || '';
}

function normalizeChallenge(value: string): string {
  const raw = String(value || '').trim();
  if (!raw) return '';
  return raw
    .replace(/^link_challenge[:=_-]/, '')
    .replace(/^link[:=_-]/, '')
    .trim();
}

export function telegramLinkChallenge(): string {
  const params = new URLSearchParams(window.location.search);
  const queryValue = params.get('link_challenge') || params.get('challenge_id') || params.get('startapp') || '';
  const startValue = telegramStartParam();
  return normalizeChallenge(queryValue || startValue);
}

export function isTelegramWebApp(): boolean {
  return telegramInitData().length > 0;
}

export function prepareTelegramWebApp(): void {
  if (!isTelegramWebApp()) return;
  window.Telegram?.WebApp?.ready?.();
  window.Telegram?.WebApp?.expand?.();
}
