/**
 * Thin fetch wrapper for the economics /me/ routes the usage card reads.
 *
 * Only the GET requests this widget actually issues are wrapped here;
 * the pay-flow POSTs (checkout / subscription / billing portal) are not
 * exposed because the corresponding UI surface is hidden in this card.
 */

import { settings } from './settings';
import type { BudgetBreakdownResponse, ProfileResponse } from './types';

function meUrl(path: string): string {
  return `${settings.getBaseUrl()}/api/economics/me${path}`;
}

function platformUrl(path: string): string {
  return `${settings.getBaseUrl()}${path}`;
}

export class UsageCardApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'UsageCardApiError';
    this.status = status;
  }
}

async function fetchJson<T>(url: string): Promise<T> {
  const headers = settings.authHeaders({ Accept: 'application/json' });
  let response: Response;
  try {
    response = await fetch(url, { credentials: 'include', headers });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new UsageCardApiError(`network error: ${message}`, 0);
  }
  if (!response.ok) {
    let detail = '';
    try {
      detail = (await response.text()).slice(0, 240);
    } catch {
      detail = '';
    }
    throw new UsageCardApiError(detail || `HTTP ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export function getBudgetBreakdown(): Promise<BudgetBreakdownResponse> {
  return fetchJson<BudgetBreakdownResponse>(meUrl('/budget-breakdown'));
}

export function getProfile(): Promise<ProfileResponse> {
  return fetchJson<ProfileResponse>(platformUrl('/profile'));
}
