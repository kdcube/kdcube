/**
 * apps-config controller — the imperative orchestration layer (repo convention:
 * no thunks/RTK-Query; a controller calls the data source, then dispatches plain
 * loading/loaded/error actions). Keeps React thin and keeps async out of reducers.
 */
import type { AppScope } from './model/index.ts';
import type { AppsConfigDataSource } from './data/index.ts';
import type { AppsConfigStore } from './state/index.ts';
import { appsConfigActions as A } from './state/index.ts';

export interface AppsConfigController {
  /** Set/replace scope and load the app list. */
  loadApps(scope?: AppScope): Promise<void>;
  /** Select an app and load its config view (null clears the selection). */
  selectApp(bundleId: string | null): Promise<void>;
  /** Mark an agent selected (UI focus only). */
  selectAgent(agentId: string | null): void;
  /** Load one agent's capabilities into its slot. */
  loadAgent(agentId: string): Promise<void>;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function createAppsConfigController(opts: {
  store: AppsConfigStore;
  dataSource: AppsConfigDataSource;
  scope?: AppScope;
}): AppsConfigController {
  const { store, dataSource } = opts;
  if (opts.scope) store.dispatch(A.setScope(opts.scope));

  const requireScope = (): AppScope => {
    const s = store.getState().appsConfig.scope;
    if (!s) throw new Error('apps-config: scope not set');
    return s;
  };

  return {
    async loadApps(next) {
      if (next) store.dispatch(A.setScope(next));
      store.dispatch(A.appsLoading());
      try {
        store.dispatch(A.appsLoaded(await dataSource.listApps(requireScope())));
      } catch (e) {
        store.dispatch(A.appsError(errMsg(e)));
      }
    },

    async selectApp(bundleId) {
      store.dispatch(A.selectApp(bundleId));
      if (!bundleId) return;
      store.dispatch(A.appConfigLoading());
      try {
        store.dispatch(A.appConfigLoaded(await dataSource.loadAppConfig(requireScope(), bundleId)));
      } catch (e) {
        store.dispatch(A.appConfigError(errMsg(e)));
      }
    },

    selectAgent(agentId) {
      store.dispatch(A.selectAgent(agentId));
    },

    async loadAgent(agentId) {
      const bundleId = store.getState().appsConfig.selectedAppId;
      if (!bundleId) return;
      store.dispatch(A.agentCapsLoading(agentId));
      try {
        const caps = await dataSource.loadAgentCapabilities(requireScope(), bundleId, agentId);
        store.dispatch(A.agentCapsLoaded({ agentId, caps }));
      } catch (e) {
        store.dispatch(A.agentCapsError({ agentId, error: errMsg(e) }));
      }
    },
  };
}
