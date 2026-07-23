/**
 * @kdcube/components-core/apps-config — the headless engine for the app-config
 * viewer: domain model, a pluggable data source, an RTK store/slice, and a
 * controller. No React. Hosts (the admin widget, the control-plane client) wrap
 * this with the React adapter (@kdcube/components-react/apps-config) and supply
 * an AppsConfigTransport.
 */
export * from './model/index.ts';
export * from './data/index.ts';
export * from './state/index.ts';
export { createAppsConfigController } from './controller.ts';
export type { AppsConfigController } from './controller.ts';
