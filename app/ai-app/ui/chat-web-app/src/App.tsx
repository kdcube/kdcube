import {useEffect, useMemo} from "react";
import AppRouter from "./AppRouter.tsx";
import PlatformVersionBadge from "./components/PlatformVersionBadge.tsx";
import './App.css'
import {useAppDispatch, useAppSelector, useAppStore} from "./app/store.ts";
import {
    loadChatSettings, selectChatEventReportingEnabled,
    selectChatSettingsLoaded,
    selectChatSettingsLoading, selectChatSettingsLoadingError
} from "./features/chat/chatSettingsSlice.ts";
import {initializeEventLogger} from "./services/eventLogger";

const App = () => {
    const dispatch = useAppDispatch();
    const store = useAppStore();
    const settingsLoaded = useAppSelector(selectChatSettingsLoaded)
    const settingsLoading = useAppSelector(selectChatSettingsLoading)
    const settingsLoadingError = useAppSelector(selectChatSettingsLoadingError)

    useEffect(() => {
        // Initialize event logger for error and log tracking
        if (settingsLoaded && selectChatEventReportingEnabled(store.getState()))
            initializeEventLogger(store);
    }, [store, settingsLoaded]);

    useEffect(() => {
        if (!settingsLoaded && !settingsLoading && !settingsLoadingError) {
            dispatch(loadChatSettings())
        }
    }, [dispatch, settingsLoaded, settingsLoading, settingsLoadingError]);

    return useMemo(() => {
        if (settingsLoadingError) {
            return <main className="app-startup" role="alert">
                <div className="app-startup__content">
                    <h1>KDCube could not start</h1>
                    <p>The runtime configuration is unavailable.</p>
                    <button type="button" onClick={() => dispatch(loadChatSettings())}>Try again</button>
                </div>
            </main>
        }

        if (!settingsLoaded) {
            return <main className="app-startup" role="status" aria-live="polite">
                <div className="app-startup__content">
                    <p>Starting KDCube...</p>
                </div>
            </main>;
        }
        return <>
            <AppRouter/>
            <PlatformVersionBadge/>
        </>
    }, [dispatch, settingsLoaded, settingsLoadingError])
}

export default App
