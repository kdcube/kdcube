import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";

export interface ConfigAssistantState {
    mode: string | null;
    inspect: {
        activeTab: "graph" | "concept" | "footprint" | "source" | "config" | "my_bundle";
        selectedQualifiedName: string | null;
        selectedConceptId: string | null;
    };
    scope: {
        packageFilter: string;
    };
}

const initialState: ConfigAssistantState = {
    mode: null,
    inspect: {
        activeTab: "graph",
        selectedQualifiedName: null,
        selectedConceptId: null,
    },
    scope: {
        packageFilter: "",
    },
};

const configAssistantSlice = createSlice({
    name: "configAssistant",
    initialState,
    reducers: {
        setMode(state, action: PayloadAction<string | null>) {
            state.mode = action.payload;
        },
        setInspectTab(state, action: PayloadAction<ConfigAssistantState["inspect"]["activeTab"]>) {
            state.inspect.activeTab = action.payload;
        },
        setInspectSelection(
            state,
            action: PayloadAction<{
                qualifiedName?: string | null;
                conceptId?: string | null;
                tab?: ConfigAssistantState["inspect"]["activeTab"];
            }>
        ) {
            const {qualifiedName, conceptId, tab} = action.payload;
            if (qualifiedName !== undefined) state.inspect.selectedQualifiedName = qualifiedName;
            if (conceptId !== undefined) state.inspect.selectedConceptId = conceptId;
            if (tab) state.inspect.activeTab = tab;
        },
        setPackageFilter(state, action: PayloadAction<string>) {
            state.scope.packageFilter = action.payload;
        },
        resetConfigAssistant() {
            return initialState;
        },
    },
});

export const {
    setMode,
    setInspectTab,
    setInspectSelection,
    setPackageFilter,
    resetConfigAssistant,
} = configAssistantSlice.actions;

export const selectConfigAssistantMode = (state: RootState) => state.configAssistant.mode;
export const selectConfigAssistantInspect = (state: RootState) => state.configAssistant.inspect;
export const selectConfigAssistantScope = (state: RootState) => state.configAssistant.scope;

export default configAssistantSlice.reducer;
