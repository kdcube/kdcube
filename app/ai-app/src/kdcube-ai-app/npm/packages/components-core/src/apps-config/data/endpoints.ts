/**
 * The single place that knows admin/integration route shapes. Isolated so a
 * route correction after live verification is a one-file change.
 */
import type { AppScope } from '../model/index.ts';

const enc = encodeURIComponent;
const scopeQuery = (scope: AppScope) =>
  `tenant=${enc(scope.tenant)}&project=${enc(scope.project)}`;

/** App list + introspected as_provider surfaces. */
export function appsListUrl(base: string, scope: AppScope): string {
  return `${base}/admin/integrations/bundles?${scopeQuery(scope)}`;
}

/** Full parsed config (props + defaults) for one app. */
export function appPropsUrl(base: string, scope: AppScope, bundleId: string): string {
  return `${base}/admin/integrations/bundles/${enc(bundleId)}/props?${scopeQuery(scope)}`;
}

/** Per-app `agent_capabilities` operation (POST {data:{agent}}). */
export function agentCapabilitiesUrl(base: string, scope: AppScope, bundleId: string): string {
  return `${base}/api/integrations/bundles/${enc(scope.tenant)}/${enc(scope.project)}/${enc(bundleId)}/operations/agent_capabilities`;
}
