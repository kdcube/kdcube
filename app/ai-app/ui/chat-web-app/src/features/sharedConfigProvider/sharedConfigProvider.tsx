import {useEffect} from "react";
import {chatAPIBasePath} from "../../BuildConfig.ts";
import {useAppSelector} from "../../app/store.ts";
import {selectIdTokenHeaderName, selectProject, selectTenant} from "../chat/chatSettingsSlice.ts";
import {selectAuthToken, selectIdToken} from "../auth/authSlice.ts";

const useSharedConfigProvider = ()=> {
    const tenant = useAppSelector(selectTenant);
    const project = useAppSelector(selectProject);
    const authToken = useAppSelector(selectAuthToken)
    const idToken = useAppSelector(selectIdToken)
    const idTokenHeaderName = useAppSelector(selectIdTokenHeaderName)

    useEffect(() => {
        const onIFrameRequest = (event: MessageEvent) => {
            if (event.data?.type === 'CONFIG_REQUEST') {
                console.debug(`[onIFrameRequest] CONFIG_REQUEST received`, event);

                const requestedFields = event.data.data?.requestedFields;
                const identity = event.data.data?.identity;

                if (!requestedFields || !Array.isArray(requestedFields)) {
                    return;
                }
                const baseUrl = window.location.origin + chatAPIBasePath
                const configMap: Record<string, () => unknown> = {
                    'baseUrl': () => baseUrl,
                    'accessToken': () => authToken,
                    'idToken': () => idToken,
                    'idTokenHeader': () => idTokenHeaderName,
                    'defaultTenant': () => tenant,
                    'defaultProject': () => project,
                    'defaultAppBundleId': () => null
                };

                const config = requestedFields.reduce((result, field) => {
                    if (field in configMap) {
                        result[field] = configMap[field]();
                    }
                    return result;
                }, {} as Record<string, unknown>);

                console.debug(`[onIFrameRequest] Sending config`, config);

                event.source?.postMessage({
                    type: 'CONN_RESPONSE',
                    identity: identity,
                    config: config
                }); //, event.origin);
            }
        };

        window.addEventListener("message", onIFrameRequest);
        return () => {
            window.removeEventListener("message", onIFrameRequest);
        };
    }, [tenant, project, authToken, idToken, idTokenHeaderName]);

}

export default useSharedConfigProvider;