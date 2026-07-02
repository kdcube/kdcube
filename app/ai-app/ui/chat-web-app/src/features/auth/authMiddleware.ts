import {Middleware, PayloadAction, UnknownAction} from "@reduxjs/toolkit";
import CognitoAuth from "./cognitoAuth.ts";
import {AppStore, RootState} from "../../app/store.ts";
import {
    loadChatSettings,
    selectChatPath,
    selectAuthConfig,
    selectAuthCookieName,
    selectAuthCookieOpts,
    selectIdCookieName,
    selectProject,
    selectTenant,
    selectUseAuthCookies
} from "../chat/chatSettingsSlice.ts";
import {AuthAction, finishLoading, setCredentials, setLoggedOut, startLoading} from "./authSlice.ts";
import {BundleSessionAuthConfig, SimpleAuthConfig} from "./authTypes.ts";
import {removeCookie, setCookie} from "../../utils/cookies.ts";
import {chatAPIBasePath} from "../../BuildConfig.ts";

export const LOG_IN = "auth/LogIn"

interface LogInPayload {
    navigateTo?: string | URL | null;
}

interface LogInAction extends UnknownAction {
    type: typeof LOG_IN;
    payload?: LogInPayload | null;
}

export const logIn = (navigateTo?: string | URL | null): LogInAction => {
    return {
        type: LOG_IN,
        payload: navigateTo ? {navigateTo} : null,
    }
}

export const LOG_OUT = "auth/LogOut"

interface LogOutPayload {
    navigateTo?: string | URL | null;
}

interface LogOutAction extends UnknownAction {
    type: typeof LOG_OUT;
    payload?: LogOutPayload | null;
}

export const logOut = (navigateTo?: string | URL | null): LogOutAction => {
    return {
        type: LOG_OUT,
        payload: navigateTo ? {navigateTo} : null,
    }
}

export const LOG_IN_CALLBACK = "auth/LogInCallback"

interface LogInCallbackPayload {
    navigateTo?: string | URL | null;

}

interface LogInCallbackAction extends UnknownAction {
    type: typeof LOG_IN_CALLBACK;
    payload?: LogInCallbackPayload | null;
}

export const logInCallback = (navigateTo?: string | URL | null): LogInCallbackAction => {
    return {
        type: LOG_IN_CALLBACK,
        payload: navigateTo ? {navigateTo} : null
    }
}

export type AuthActions = LogInAction | LogOutAction | LogInCallbackAction

export type HandleAction = (store: AppStore, action: AuthActions) => void

export interface WithActionHandler {
    handleAction: HandleAction
}

const DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0";

export const authMiddleware = (): Middleware => {
    let handler: HandleAction | null = null;
    return ((store) => (next: (action: unknown) => unknown) => (action: unknown) => {
        next(action)
        const state = store.getState() as RootState;
        const authConfig = selectAuthConfig(state)
        let handlerParent: WithActionHandler | null = null

        const removeAuthCookie = () => {
            removeCookie(selectAuthCookieName(store.getState()))
        }

        const removeIdCookie = () => {
            removeCookie(selectIdCookieName(store.getState()))
        }

        const removeCookies = () => {
            removeAuthCookie()
            removeIdCookie()
        }

        const setAuthToken = (token: string) => {
            setCookie(selectAuthCookieName(store.getState()), token, selectAuthCookieOpts(store.getState()))
        }

        const setIdToken = (idToken: string) => {
            setCookie(selectIdCookieName(store.getState()), idToken, selectAuthCookieOpts(store.getState()))
        }

        const currentLocation = () => {
            if (typeof window === "undefined") {
                return selectChatPath(store.getState());
            }
            return `${window.location.pathname}${window.location.search}${window.location.hash}`;
        }

        const resolveBundleLoginUrl = async (cfg: BundleSessionAuthConfig): Promise<string> => {
            const loginUrl = String(cfg.loginUrl || "").trim();
            if (loginUrl) {
                return loginUrl;
            }
            const ref = cfg.connectionHub || {};
            const tenant = encodeURIComponent(selectTenant(store.getState()));
            const project = encodeURIComponent(selectProject(store.getState()));
            const bundleId = encodeURIComponent(ref.bundleId || DEFAULT_CONNECTION_HUB_BUNDLE_ID);
            const response = await fetch(
                `/api/integrations/bundles/${tenant}/${project}/${bundleId}/public/authority_provider_entrypoint_resolve`,
                {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        data: {
                            authority_id: ref.authorityId || "",
                            provider_id: ref.providerId || "",
                            provider_type: ref.providerType || "",
                            entrypoint: ref.entrypoint || "login",
                        },
                    }),
                },
            );
            const text = await response.text();
            let parsed: unknown = {};
            try {
                parsed = text ? JSON.parse(text) : {};
            } catch {
                parsed = {raw: text};
            }
            const result = parsed && typeof parsed === "object" && "authority_provider_entrypoint_resolve" in parsed
                ? (parsed as Record<string, unknown>).authority_provider_entrypoint_resolve
                : parsed;
            const record = result && typeof result === "object" ? result as Record<string, unknown> : {};
            if (!response.ok || record.ok === false) {
                throw new Error(String(record.error || record.detail || text || "Bundle auth login endpoint resolution failed"));
            }
            const resolvedUrl = String(record.url || "").trim();
            if (!resolvedUrl) {
                throw new Error("Bundle auth login endpoint resolution returned no URL");
            }
            return resolvedUrl;
        }

        const redirectToBundleLogin = async (navigateTo?: string | URL | null) => {
            const cfg = selectAuthConfig(store.getState()) as BundleSessionAuthConfig;
            let loginUrl = "";
            try {
                loginUrl = await resolveBundleLoginUrl(cfg);
            } catch (error) {
                console.error("Bundle auth login URL could not be resolved", error);
                return;
            }
            const url = new URL(loginUrl, window.location.origin);
            if (url.protocol === "http:" && window.location.protocol === "https:") {
                url.protocol = "https:";
            }
            url.searchParams.set("next", String(navigateTo || currentLocation()));
            window.location.assign(url.toString());
        }

        const verifyBundleSession = async () => {
            store.dispatch(startLoading());
            try {
                const response = await fetch(`${chatAPIBasePath}/profile`, {
                    method: "GET",
                    headers: {"Content-Type": "application/json"},
                    credentials: "include",
                });
                if (!response.ok) {
                    throw new Error(`Profile request failed: ${response.status}`);
                }
                const profile = await response.json();
                if (!profile?.session_id) {
                    throw new Error("Profile response did not include a session id");
                }
                const userType = String(profile.user_type || "").toLowerCase();
                const userId = String(profile.user_id || "").trim();
                if (!userId || userType === "anonymous") {
                    throw new Error("Bundle session is not authenticated");
                }
                store.dispatch(setCredentials({
                    loggedIn: true,
                    user: {
                        username: profile.username,
                        email: profile.email,
                        roles: profile.roles || [],
                        permissions: profile.permissions || [],
                        raw: profile,
                    },
                }));
            } catch (error) {
                console.debug("Bundle session is not established", error);
                store.dispatch(setLoggedOut());
                void redirectToBundleLogin(currentLocation());
            } finally {
                store.dispatch(finishLoading(null));
            }
        }

        switch ((action as UnknownAction).type) {
            case loadChatSettings.fulfilled.type:
                if (!handler) {
                    switch (authConfig.authType) {
                        case "none":
                            store.dispatch(setCredentials({
                                loggedIn: true,
                            }));
                            break;
                        case "bundle":
                            void verifyBundleSession();
                            break;
                        case "simple":
                        case "hardcoded":
                            store.dispatch(setCredentials({
                                loggedIn: true,
                                authToken: (authConfig as SimpleAuthConfig).token,
                            }));
                            break;
                        case "cognito":
                            handlerParent = new CognitoAuth()
                            handler = handlerParent.handleAction.bind(handlerParent)
                    }
                }
                break
            case LOG_IN:
            case LOG_IN_CALLBACK:
            case LOG_OUT:
                if (!handler) {
                    if (authConfig.authType === "none") {
                        return;
                    }
                    if (authConfig.authType === "bundle") {
                        const payload = (action as LogInAction | LogOutAction | LogInCallbackAction).payload;
                        if ((action as UnknownAction).type === LOG_IN || (action as UnknownAction).type === LOG_IN_CALLBACK) {
                            void redirectToBundleLogin(payload?.navigateTo);
                        } else {
                            removeCookies();
                            store.dispatch(setLoggedOut());
                        }
                        return;
                    }
                    throw new Error("auth action handler is not initialized");
                }
                handler(store as AppStore, action as AuthActions);
                break;
            case setCredentials.type:
                if (selectUseAuthCookies(store.getState())) {
                    switch (authConfig.authType) {
                        case "simple":
                        case "hardcoded":
                        case "cognito": {
                            const payload = (action as PayloadAction<AuthAction>).payload;
                            removeCookies()
                            if (!payload.loggedIn) {
                                return
                            }

                            if (payload.authToken) {
                                setAuthToken(payload.authToken)
                            }

                            if (payload.idToken) {
                                setIdToken(payload.idToken)
                            }
                        }
                    }
                    break
                }
        }
    })
}
