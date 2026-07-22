/** apps-config selectors. */
import type { AppsConfigRootState } from './store.ts';
import type { AgentCapsSlot } from './state.ts';

export const selectScope = (s: AppsConfigRootState) => s.appsConfig.scope;
export const selectApps = (s: AppsConfigRootState) => s.appsConfig.apps;
export const selectSelectedAppId = (s: AppsConfigRootState) => s.appsConfig.selectedAppId;
export const selectAppConfig = (s: AppsConfigRootState) => s.appsConfig.appConfig;
export const selectSelectedAgentId = (s: AppsConfigRootState) => s.appsConfig.selectedAgentId;

export const selectAgentCaps =
  (agentId: string) =>
  (s: AppsConfigRootState): AgentCapsSlot | undefined =>
    s.appsConfig.agentCaps[agentId];
