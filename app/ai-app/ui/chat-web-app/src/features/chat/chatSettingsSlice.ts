import {createAsyncThunk, createSlice} from "@reduxjs/toolkit";
import {ChatSettingsState} from "./chatTypes.ts";
import {RootState} from "../../app/store.ts";
import {configEndpoint, configPath} from "../../BuildConfig.ts";
import {loadRuntimeChatSettings} from "./chatSettingsLoader.ts";

export const loadChatSettings = createAsyncThunk("chatSettings/load", async () => {
    return loadRuntimeChatSettings({configEndpoint, configPath});
}, {
    condition(_unused, api) {
        return !selectChatSettingsLoading(api.getState() as RootState)
    }
})

const chatSettingsSlice = createSlice({
    name: "chatSettings",
    initialState: (): ChatSettingsState => {
        return {
            isLoaded: false,
            isLoading: false,
            isLoadingError: false,
            settings: {
                auth: {authType: "none"},
                tenant: "default",
                project: "default",
                routesPrefix: "",
                streamIdHeaderName: "KDC-Stream-ID",
                useAuthCookies: true,
                authCookieName: "__Secure-LATC",
                idCookieName: "__Secure-LITC",
                authCookieOpts: {
                    secure: true,
                    sameSite: "Strict"
                },
                eventReportingEnabled: true
            }
        }
    },
    reducers: {},
    extraReducers: (builder) => {
        builder.addCase(loadChatSettings.pending, (state) => {
            state.isLoading = true
            state.isLoadingError = false
        })
        builder.addCase(loadChatSettings.fulfilled, (state, action) => {
            state.isLoaded = true
            state.isLoading = false
            state.isLoadingError = false
            Object.entries(action.payload).forEach(([key, val]) => {
                (state.settings as Record<string, unknown>)[key] = val;
            })
        })
        builder.addCase(loadChatSettings.rejected, (state, action) => {
            console.error(action.payload, action.error)
            state.isLoading = false
            state.isLoadingError = true
        })
    }
})

export const selectChatSettingsLoaded = (state: RootState) => state.chatSettings.isLoaded
export const selectChatSettingsLoading = (state: RootState) => state.chatSettings.isLoading
export const selectChatSettingsLoadingError = (state: RootState) => state.chatSettings.isLoadingError
export const selectChatEventReportingEnabled = (state: RootState) => state.chatSettings.settings.eventReportingEnabled
export const selectAuthConfig = (state: RootState) => state.chatSettings.settings.auth
export const selectTenant = (state: RootState) => state.chatSettings.settings.tenant
export const selectProject = (state: RootState) => state.chatSettings.settings.project
export const selectRoutesPrefix = (state: RootState) => state.chatSettings.settings.routesPrefix
export const selectChatPath = (state: RootState) => state.chatSettings.settings.routesPrefix + "/chat"
export const selectUseAuthCookies = (state: RootState) => state.chatSettings.settings.useAuthCookies
export const selectAuthCookieName = (state: RootState) => state.chatSettings.settings.authCookieName
export const selectIdCookieName = (state: RootState) => state.chatSettings.settings.idCookieName
export const selectAuthCookieOpts = (state: RootState) => state.chatSettings.settings.authCookieOpts
export const selectStreamIdHeaderName = (state: RootState) => state.chatSettings.settings.streamIdHeaderName
export const selectPlatformVersion = (state: RootState) => state.chatSettings.settings.versions?.platform ?? null
export const selectIdTokenHeaderName = (state: RootState) => {
    return ((state.chatSettings.settings.auth as unknown) as Record<string, string | undefined>).idTokenHeaderName ?? null
}

export default chatSettingsSlice.reducer
