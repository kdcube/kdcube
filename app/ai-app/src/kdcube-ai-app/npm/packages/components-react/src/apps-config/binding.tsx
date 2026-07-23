/**
 * @kdcube/components-react/apps-config — React bindings over the headless
 * apps-config engine (@kdcube/components-core/apps-config).
 *
 * Intentionally thin: the store, slice, data source, and controller live in the
 * core. This provider owns one store + controller instance, exposes the store to
 * react-redux, and puts the controller on context. A host supplies a `scope` and
 * either a ready `dataSource` or an `AppsConfigTransport` (baseUrl + authHeaders)
 * — so the same component works in the admin widget and the control-plane client.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { Provider, useDispatch, useSelector, type TypedUseSelectorHook } from 'react-redux';
import {
  createAppsConfigStore,
  createAppsConfigController,
  createHttpDataSource,
  type AppScope,
  type AppsConfigStore,
  type AppsConfigRootState,
  type AppsConfigDispatch,
  type AppsConfigController,
  type AppsConfigDataSource,
  type AppsConfigTransport,
} from '@kdcube/components-core/apps-config';

const ControllerContext = createContext<AppsConfigController | null>(null);

export interface AppsConfigProviderProps {
  scope: AppScope;
  /** Supply a ready data source, or a transport (an HTTP source is built from it). */
  dataSource?: AppsConfigDataSource;
  transport?: AppsConfigTransport;
  /** Load the app list on mount (default true). */
  autoload?: boolean;
  children: ReactNode;
}

interface Held {
  store: AppsConfigStore;
  controller: AppsConfigController;
}

export function AppsConfigProvider({
  scope,
  dataSource,
  transport,
  autoload = true,
  children,
}: AppsConfigProviderProps) {
  const [{ store, controller }] = useState<Held>(() => {
    const source = dataSource ?? createHttpDataSource(transport ?? throwingTransport());
    const st = createAppsConfigStore();
    const ctl = createAppsConfigController({ store: st, dataSource: source, scope });
    return { store: st, controller: ctl };
  });

  useEffect(() => {
    if (autoload) void controller.loadApps();
  }, [controller, autoload]);

  return (
    <ControllerContext.Provider value={controller}>
      <Provider store={store}>{children}</Provider>
    </ControllerContext.Provider>
  );
}

function throwingTransport(): AppsConfigTransport {
  return {
    baseUrl() {
      throw new Error('AppsConfigProvider: provide a `transport` or a `dataSource`.');
    },
    authHeaders() {
      return {};
    },
  };
}

export function useAppsConfigController(): AppsConfigController {
  const controller = useContext(ControllerContext);
  if (!controller) {
    throw new Error('useAppsConfigController must be used within <AppsConfigProvider>.');
  }
  return controller;
}

export const useAppsConfigSelector: TypedUseSelectorHook<AppsConfigRootState> = useSelector;
export const useAppsConfigDispatch = () => useDispatch<AppsConfigDispatch>();
