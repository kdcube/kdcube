/** apps-config state layer barrel. */
export { createAppsConfigStore } from './store.ts';
export type { AppsConfigStore, AppsConfigRootState, AppsConfigDispatch } from './store.ts';
export { appsConfigActions, appsConfigReducer } from './slice.ts';
export { initialState } from './state.ts';
export type { AppsConfigState, LoadSlot, AgentCapsSlot } from './state.ts';
export * from './selectors.ts';
