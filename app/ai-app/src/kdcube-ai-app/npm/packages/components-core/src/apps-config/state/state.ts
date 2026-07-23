/** apps-config redux state shape + initial value. */
import type {
  AppScope,
  AppSummary,
  AppConfigView,
  AgentCapabilities,
  LoadStatus,
} from '../model/index.ts';

/** A load slot with the explicit loading/error/data triad. */
export interface LoadSlot<T> {
  status: LoadStatus;
  error: string | null;
  data: T;
}

/** Per-agent capabilities slot (keyed by agent id). */
export type AgentCapsSlot = LoadSlot<AgentCapabilities | null>;

export interface AppsConfigState {
  scope: AppScope | null;
  apps: LoadSlot<AppSummary[]>;
  selectedAppId: string | null;
  appConfig: LoadSlot<AppConfigView | null>;
  selectedAgentId: string | null;
  agentCaps: Record<string, AgentCapsSlot>;
}

export const initialState: AppsConfigState = {
  scope: null,
  apps: { status: 'idle', error: null, data: [] },
  selectedAppId: null,
  appConfig: { status: 'idle', error: null, data: null },
  selectedAgentId: null,
  agentCaps: {},
};
