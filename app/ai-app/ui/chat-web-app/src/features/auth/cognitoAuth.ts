import {UnknownAction} from "@reduxjs/toolkit";
import {User, UserManager, UserManagerSettings} from "oidc-client-ts";
import {AuthActions, LOG_IN, LOG_IN_CALLBACK, LOG_OUT} from "./authMiddleware.ts";
import {AppStore, RootState} from "../../app/store.ts";
import {finishLoading, selectAuthIsLoading, setCredentials, setLoggedOut, startLoading} from "./authSlice.ts";
import {oAuthUserToAppUser} from "./helpers.ts";
import {createDefaultUserManager} from "./oAuth2.ts";
import {getChatPagePath, getDefaultRoutePrefix} from "../chat/configHelper.ts";
import {CognitoAuthConfig} from "./authTypes.ts";
import {selectAuthConfig, selectChatPath} from "../chat/chatSettingsSlice.ts";

class CognitoAuth {
    private userManager: UserManager | null = null;

    handleAction(store: AppStore, action: AuthActions) {
        const getOAuthConfig = (store: AppStore): UserManagerSettings => {
            const state = store.getState() as RootState;
            const config = selectAuthConfig(state) as CognitoAuthConfig;

            const scope = config.oidcConfig.scope ?? "openid email phone profile"

            const base = getDefaultRoutePrefix();
            const origin = window.location.origin;

            const redirect_uri = config.oidcConfig.redirect_uri ?? `${origin}${base}/callback`;
            const post_logout_redirect_uri = config.oidcConfig.post_logout_redirect_uri ?? getChatPagePath();

            return {
                ...config.oidcConfig,
                response_type: "code",
                automaticSilentRenew: false,
                monitorSession: false,
                loadUserInfo: true,
                redirect_uri,
                post_logout_redirect_uri,
                scope,
            };
        }

        const setUser = (user: User) => {
            store.dispatch(setCredentials({
                user: oAuthUserToAppUser(user),
                authToken: user!.access_token,
                idToken: user!.id_token,
                loggedIn: true,
            }))
        }

        const getUserManager = ((store: AppStore): UserManager => {
            if (!this.userManager) {
                const settings = getOAuthConfig(store);

                this.userManager = createDefaultUserManager(settings, {
                    onUserSignedIn: async () => {
                        console.debug("onUserSignedIn")
                    },
                    onUserLoaded: (user) => {
                        console.debug("onUserLoaded")
                        if (user) {
                            setUser(user)
                        }
                    },
                    onUserSessionChanged: async () => {
                        console.debug("onUserSessionChanged")
                    },
                    onAccessTokenExpiring: async () => {
                        try {
                            await this.userManager!.signinSilent()
                        } catch (e) {
                            console.error(e)
                            await this.userManager!.removeUser()
                            store.dispatch(setLoggedOut())
                        }
                    }
                })
            }
            return this.userManager
        })

        const actionHandlers = async (store: AppStore, action: UnknownAction) => {
            const userManager = getUserManager(store)

            const checkLogin = async () => {
                const user = await userManager!.getUser()
                if (user) {
                    if (user.expired) {
                        try {
                            await userManager!.signinSilent()
                        } catch (e) {
                            console.error(e)
                            await userManager!.removeUser()
                            await userManager!.clearStaleState()
                            await userManager!.signinRedirect({
                                state: action.payload ? JSON.stringify(action.payload) : undefined,
                            })
                        }
                    } else {
                        setUser(user)
                    }
                } else {
                    await userManager!.clearStaleState()
                    await userManager!.signinRedirect({
                        state: action.payload ? JSON.stringify(action.payload) : undefined,
                    })
                }
                return user
            }

            switch (action.type) {
                case LOG_IN: {
                    await checkLogin()
                    break
                }
                case LOG_OUT: {
                    await userManager!.signoutRedirect()
                    break
                }
                case LOG_IN_CALLBACK: {
                    const state = store.getState() as RootState;
                    if (selectAuthIsLoading(state)) {
                        break;
                    }
                    store.dispatch(startLoading())
                    try {
                        const user = await userManager!.getUser() ?? await userManager.signinCallback()
                        if (user) {
                            const userState = user.state ? JSON.parse(user.state as string) : null
                            const navigateTo = userState && userState.navigateTo ? userState.navigateTo : "/"
                            store.dispatch(finishLoading(navigateTo))
                        } else {
                            console.error("wtf") //todo: handle this
                        }
                    } catch (e) {
                        console.error(e)
                        await userManager.removeUser()
                        await userManager.clearStaleState()
                        window.location.replace(selectChatPath(state))
                    }

                    break;
                }
            }
        }

        actionHandlers(store as AppStore, action as AuthActions).catch(console.error);
    }
}

export default CognitoAuth