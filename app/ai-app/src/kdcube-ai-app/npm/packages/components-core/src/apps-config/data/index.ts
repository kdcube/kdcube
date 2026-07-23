/** apps-config data layer barrel. */
export type { AppsConfigDataSource, AppsConfigTransport } from './datasource.ts';
export { createHttpDataSource } from './http-source.ts';
export * as normalize from './normalize.ts';
export { deepMerge, getPath } from './props.ts';
export { appsListUrl, appPropsUrl, agentCapabilitiesUrl } from './endpoints.ts';
export type {
  RawBundlesResponse,
  RawBundleEntry,
  RawSurface,
  RawBundleProps,
  RawAgentCapabilitiesResponse,
  RawAgentCapabilities,
} from './dto.ts';
