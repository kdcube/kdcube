import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";

export type ScopeFilter = "all" | "framework" | "my_bundle";

export interface ConfigAssistantSelection {
    /**
     * "class" — a Class node was clicked; qualifiedName is set.
     * "concept" — a Semantic node (kind=concept|term) was clicked; conceptId is set.
     * "policy" — a Semantic node (kind=policy) was clicked; conceptId is set.
     * null — nothing selected, DetailsPane shows the empty hint.
     */
    kind: "class" | "concept" | "policy" | null;
    qualifiedName: string | null;
    conceptId: string | null;
}

export interface ConfigAssistantState {
    mode: string | null;
    selection: ConfigAssistantSelection;
    scope: {
        packageFilter: string;
        scopeFilter: ScopeFilter;
    };
}

const initialState: ConfigAssistantState = {
    mode: null,
    selection: {kind: null, qualifiedName: null, conceptId: null},
    scope: {packageFilter: "", scopeFilter: "all"},
};

const configAssistantSlice = createSlice({
    name: "configAssistant",
    initialState,
    reducers: {
        setMode(state, action: PayloadAction<string | null>) {
            state.mode = action.payload;
        },
        selectClass(state, action: PayloadAction<string | null>) {
            const qn = action.payload;
            state.selection = {
                kind: qn ? "class" : null,
                qualifiedName: qn,
                conceptId: null,
            };
        },
        selectConcept(
            state,
            action: PayloadAction<{conceptId: string | null; isPolicy?: boolean}>,
        ) {
            const {conceptId, isPolicy} = action.payload;
            state.selection = {
                kind: conceptId ? (isPolicy ? "policy" : "concept") : null,
                qualifiedName: null,
                conceptId,
            };
        },
        clearSelection(state) {
            state.selection = {kind: null, qualifiedName: null, conceptId: null};
        },
        setPackageFilter(state, action: PayloadAction<string>) {
            state.scope.packageFilter = action.payload;
        },
        setScopeFilter(state, action: PayloadAction<ScopeFilter>) {
            state.scope.scopeFilter = action.payload;
        },
        resetConfigAssistant() {
            return initialState;
        },
    },
});

export const {
    setMode,
    selectClass,
    selectConcept,
    clearSelection,
    setPackageFilter,
    setScopeFilter,
    resetConfigAssistant,
} = configAssistantSlice.actions;

export const selectConfigAssistantMode = (state: RootState) => state.configAssistant.mode;
export const selectConfigAssistantSelection = (state: RootState) => state.configAssistant.selection;
export const selectConfigAssistantScope = (state: RootState) => state.configAssistant.scope;

export default configAssistantSlice.reducer;
