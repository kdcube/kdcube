import {createAsyncThunk, createSlice} from "@reduxjs/toolkit";
import {ChatSettings, ChatSettingsState} from "./chatTypes.ts";
import {RootState} from "../../app/store.ts";
import {configPath} from "../../BuildConfig.ts";

export const loadChatSettings = createAsyncThunk("chatSettings/load", async () => {
    const response = await fetch(configPath)
    if (response.ok) {
        return await response.json() as ChatSettings;
    }
    throw new Error("Could not load chatSettings from server");
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
                routesPrefix: ""
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
export const selectAuthConfig = (state: RootState) => state.chatSettings.settings.auth
export const selectTenant = (state: RootState) => state.chatSettings.settings.tenant
export const selectProject = (state: RootState) => state.chatSettings.settings.project
export const selectRoutesPrefix = (state: RootState) => state.chatSettings.settings.routesPrefix
export const selectChatPath = (state: RootState) => state.chatSettings.settings.routesPrefix + "/chat"
export const selectIdTokenHeaderName = (state: RootState) => {
    return ((state.chatSettings.settings.auth as unknown) as Record<string, string | undefined>).idTokenHeaderName ?? null
}

export default chatSettingsSlice.reducer