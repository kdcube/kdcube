import {createSlice, isAnyOf, PayloadAction} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";
import {bundlesApiSlice} from "./bundlesAPI.ts";
import {BundlesState} from "./types.ts";
import {ConversationState} from "../chat/chatTypes.ts";

const bundlesSlice = createSlice({
    name: "bundles",
    initialState: (): BundlesState => {
        return {
            currentBundle: null
        }
    },
    reducers: {
        setCurrentBundle: (state, action: PayloadAction<string>): void => {
            state.currentBundle = action.payload;
        }
    },
    extraReducers: (builder) => {
        builder.addCase(setCurrentBundle.type, (state, action) => {
            state.currentBundle = (action as PayloadAction<ConversationState>).payload.conversationBundleId
        }).addMatcher(isAnyOf(bundlesApiSlice.endpoints.getBundlesList.matchFulfilled), (state, action) => {
            if (state.currentBundle === null) {
                const bundles = action.payload.bundles;
                const bundleIds = Object.keys(bundles)
                if (bundleIds.length === 0) {
                    console.error("no bundles configured")
                    return
                }

                let bundleKey = action.payload.defaultBundle;
                if (!bundleKey) {
                    bundleKey = bundleIds[0]
                } else if (!Object.keys(bundles).includes(bundleKey)) {
                    console.warn(`Bundle list has no default bundle with named ${bundleKey}. Using first available`)
                    bundleKey = bundleIds[0]
                }

                state.currentBundle = bundleKey
            }
        })
    }
})

export const {
    setCurrentBundle
} = bundlesSlice.actions

export const selectCurrentBundle = (state: RootState) => state.bundles.currentBundle

export default bundlesSlice.reducer