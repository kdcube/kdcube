import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {AppUser, AuthType} from "./authTypes.ts";
import {RootState} from "../../app/store.ts";

interface AuthState {
    authType: AuthType;
    loggedIn: boolean;
    loading: boolean;
    navigateTo: string | URL | null;
    user?: AppUser | null;
    authToken?: string | null;
    idToken?: string | null;
}

interface AuthAction {
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
            }
        },
        setLoggedOut(state) {
            state.user = null;
            state.authToken = null;
            state.idToken = null;
            state.loggedIn = false;
        },
        startLoading(state) {
            state.loading = true;
        },
        finishLoading(state, action: PayloadAction<string | null | undefined>) {
            if (action.payload !== undefined) {
                state.navigateTo = action.payload;
            }
        }
    }
})

export const {setCredentials, setLoggedOut, startLoading, finishLoading} = authSlice.actions
export const selectIsLoggedIn = (state: RootState) => state.auth.loggedIn
export const selectAuthIsLoading = (state: RootState) => state.auth.loading
export const selectNavigateTo = (state: RootState) => state.auth.navigateTo
export const selectAppUser = (state: RootState) => state.auth.user
export const selectRoles = (state: RootState) => state.auth.user?.roles
export const selectAuthToken = (state: RootState) => state.auth.authToken
export const selectIdToken = (state: RootState) => state.auth.idToken

export default authSlice.reducer