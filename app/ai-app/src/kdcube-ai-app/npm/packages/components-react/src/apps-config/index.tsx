/**
 * @kdcube/components-react/apps-config — the React app-config viewer.
 *
 *   <AppsConfigProvider scope={{ tenant, project }} transport={transport}>
 *     <AppConfigPanel />
 *   </AppsConfigProvider>
 *
 * The headless engine (store, slice, data source, controller) lives in
 * @kdcube/components-core/apps-config. A host supplies scope + transport
 * (baseUrl + authHeaders) or a ready data source.
 */
export * from './binding.tsx';
export { AppConfigPanel, type AppConfigPanelProps } from './ui/AppConfigPanel.tsx';
export { AppList } from './ui/AppList.tsx';
export { AppDetail } from './ui/AppDetail.tsx';
