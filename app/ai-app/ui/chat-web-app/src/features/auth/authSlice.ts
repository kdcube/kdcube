import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {AppUser, AuthType} from "./authTypes.ts";
import {RootState} from "../../app/store.ts";

interface AuthState {
    authType: AuthType;
    loggedIn: boolean;
    // The person signed out on purpose: show the signed-out page and wait for
    // them to press Sign in, instead of starting a login by itself.
    signedOut: boolean;
    loading: boolean;
    navigateTo: string | URL | null;
    user?: AppUser | null;
    authToken?: string | null;
    idToken?: string | null;
}

export interface AuthAction {
    loggedIn?: boolean | null;
    user?: AppUser | null;
    authToken?: string | null;
    idToken?: string | null;
}

const authSlice = createSlice({
    name: 'auth',
    initialState: () => {
        return {
            loggedIn: false,
            signedOut: false,
            loading: false,
            navigateTo: null,
            authToken: null,
            user: null
        } as AuthState
    },
    reducers: {
        setCredentials(state, action: PayloadAction<AuthAction>) {
            state.user = action.payload.user;
            state.authToken = action.payload.authToken;
            state.idToken = action.payload.idToken;
            if (action.payload.loggedIn !== undefined && action.payload.loggedIn !== null) {
                state.loggedIn = action.payload.loggedIn;
                if (action.payload.loggedIn) {
                    state.signedOut = false;
                }
            }
        },
        setLoggedOut(state) {
            state.user = null;
            state.authToken = null;
            state.idToken = null;
            state.loggedIn = false;
        },
        setSignedOut(state) {
            state.user = null;
            state.authToken = null;
            state.idToken = null;
            state.loggedIn = false;
            state.signedOut = true;
        },
        startLoading(state) {
            state.loading = true;
        },
        finishLoading(state, action: PayloadAction<string | null | undefined>) {
            state.loading = false;
            if (action.payload !== undefined) {
                state.navigateTo = action.payload;
            }
        }
    }
})

export const {setCredentials, setLoggedOut, setSignedOut, startLoading, finishLoading} = authSlice.actions
export const selectIsLoggedIn = (state: RootState) => state.auth.loggedIn
export const selectAuthIsLoading = (state: RootState) => state.auth.loading
export const selectIsSignedOut = (state: RootState) => state.auth.signedOut
export const selectNavigateTo = (state: RootState) => state.auth.navigateTo
export const selectAppUser = (state: RootState) => state.auth.user
export const selectRoles = (state: RootState) => state.auth.user?.roles
export const selectAuthToken = (state: RootState) => state.auth.authToken
export const selectIdToken = (state: RootState) => state.auth.idToken

export default authSlice.reducer
