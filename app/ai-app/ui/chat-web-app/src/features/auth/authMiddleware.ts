import {Middleware, UnknownAction} from "@reduxjs/toolkit";
import CognitoAuth from "./cognitoAuth.ts";
import {AppStore, RootState} from "../../app/store.ts";
import {loadChatSettings, selectAuthConfig} from "../chat/chatSettingsSlice.ts";
import {setCredentials} from "./authSlice.ts";
import {HardcodedAuthConfig} from "./authTypes.ts";

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

export const authMiddleware = (): Middleware => {
    let handler: HandleAction | null = null;
    return ((store) => (next: (action: unknown) => unknown) => (action: unknown) => {
        next(action)
        const state = store.getState() as RootState;
        const authConfig = selectAuthConfig(state)
        let handlerParent: WithActionHandler | null = null
        switch ((action as UnknownAction).type) {
            case loadChatSettings.fulfilled.type:
                if (!handler) {
                    switch (authConfig.authType) {
                        case "none":
                            store.dispatch(setCredentials({
                                loggedIn: true,
                            }));
                            break;
                        case "hardcoded":
                            store.dispatch(setCredentials({
                                loggedIn: true,
                                authToken: (authConfig as HardcodedAuthConfig).token,
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
                    throw new Error("auth action handler is not initialized");
                }
                handler(store as AppStore, action as AuthActions);
                break;
        }
    })
}