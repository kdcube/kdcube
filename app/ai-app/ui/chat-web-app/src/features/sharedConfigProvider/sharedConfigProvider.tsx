import {useEffect} from "react";
import {chatAPIBasePath} from "../../BuildConfig.ts";
import {store, useAppSelector} from "../../app/store.ts";
import {selectIdTokenHeaderName, selectProject, selectTenant, selectUseAuthCookies} from "../chat/chatSettingsSlice.ts";
import {selectAuthToken, selectIdToken} from "../auth/authSlice.ts";
import {selectStreamId} from "../chat/chatStateSlice.ts";
import {selectCurrentBundle} from "../bundles/bundlesSlice.ts";

const useSharedConfigProvider = () => {
    const tenant = useAppSelector(selectTenant);
    const project = useAppSelector(selectProject);
    const authToken = useAppSelector(selectAuthToken)
    const idToken = useAppSelector(selectIdToken)
    const idTokenHeaderName = useAppSelector(selectIdTokenHeaderName)
    const streamId = useAppSelector(selectStreamId);
    const currentBundle = useAppSelector(selectCurrentBundle);

    useEffect(() => {
        const onIFrameRequest = (event: MessageEvent) => {
            if (event.data?.type === 'CONFIG_REQUEST') {
                console.debug(`[onIFrameRequest] CONFIG_REQUEST received`, event);

                const requestedFields = event.data.data?.requestedFields;
                const identity = event.data.data?.identity;

                if (!requestedFields || !Array.isArray(requestedFields)) {
                    return;
                }
                // This code runs inside the KDCube frontend frame. Use this
                // frame's origin, not the top-level embedding page origin, so
                // nested widgets keep calling the hosted KDCube API.
                const baseUrl = window.location.origin + chatAPIBasePath
                const configMap: Record<string, () => unknown> = {
                    'baseUrl': () => baseUrl,
                    'defaultTenant': () => tenant,
                    'defaultProject': () => project,
                    'defaultAppBundleId': () => currentBundle,
                    'streamId': () => streamId
                };

                if (!selectUseAuthCookies(store.getState())) {
                    configMap['accessToken'] = () => authToken
                    configMap['idToken'] = () => idToken
                    configMap['idTokenHeader'] = () => idTokenHeaderName
                }

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
    }, [tenant, project, authToken, idToken, idTokenHeaderName, streamId, currentBundle]);

}

export default useSharedConfigProvider;
