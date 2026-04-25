import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {RootState} from "../../app/store.ts";

export interface HybridSearchSettings {
    enabled: boolean;
    source_folder: string;
    formats: string[];
    // Neo4j — powers the graph side of hybrid search
    neo4j_uri: string;
    neo4j_user: string;
    neo4j_password: string;
    neo4j_database: string;
    // Conventions prompt — loaded from an .md file; controls how documents are parsed into the graph
    conventions: string;
    conventions_filename: string;
    // Retrieval
    top_k_vector: number;
    top_k_graph: number;
    min_score_threshold: number;
    context_window: number;
    use_reranking: boolean;
    distance_type: "cosine" | "l2" | "ip";
}

export interface VectorSearchSettings {
    enabled: boolean;
    source_folder: string;
    formats: string[];
    top_k_vector: number;
    min_score_threshold: number;
    context_window: number;
    use_reranking: boolean;
    distance_type: "cosine" | "l2" | "ip";
}

export interface CodeCoreSettings {
    enabled: boolean;
    search_type: "fulltext" | "vector" | "hybrid";
    limit: number;
}

export interface SearchSettingsState {
    hybrid: HybridSearchSettings;
    vector: VectorSearchSettings;
    codeCore: CodeCoreSettings;
}

export const ALL_FORMATS = [
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/csv",
    "text/html",
    "application/json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/xml",
    "application/x-yaml",
];

export const FORMAT_LABELS: Record<string, string> = {
    "application/pdf": "PDF",
    "text/markdown": "Markdown",
    "text/plain": "Text",
    "text/csv": "CSV",
    "text/html": "HTML",
    "application/json": "JSON",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "application/xml": "XML",
    "application/x-yaml": "YAML",
};

const DEFAULT_FORMATS = [
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/csv",
];

const initialState: SearchSettingsState = {
    hybrid: {
        enabled: true,
        source_folder: "",
        formats: [...DEFAULT_FORMATS],
        neo4j_uri: "bolt://neo4j:7687",
        neo4j_user: "neo4j",
        neo4j_password: "",
        neo4j_database: "neo4j",
        conventions: "",
        conventions_filename: "",
        top_k_vector: 10,
        top_k_graph: 10,
        min_score_threshold: 0.6,
        context_window: 2,
        use_reranking: true,
        distance_type: "cosine",
    },
    vector: {
        enabled: false,
        source_folder: "",
        formats: [...DEFAULT_FORMATS],
        top_k_vector: 10,
        min_score_threshold: 0.6,
        context_window: 2,
        use_reranking: true,
        distance_type: "cosine",
    },
    codeCore: {
        enabled: false,
        search_type: "hybrid",
        limit: 10,
    },
};

const searchSettingsSlice = createSlice({
    name: "searchSettings",
    initialState,
    reducers: {
        setHybridEnabled(state, action: PayloadAction<boolean>) {
            state.hybrid.enabled = action.payload;
        },
        updateHybrid(state, action: PayloadAction<Partial<HybridSearchSettings>>) {
            Object.assign(state.hybrid, action.payload);
        },
        setVectorEnabled(state, action: PayloadAction<boolean>) {
            state.vector.enabled = action.payload;
        },
        updateVector(state, action: PayloadAction<Partial<VectorSearchSettings>>) {
            Object.assign(state.vector, action.payload);
        },
        setCodeCoreEnabled(state, action: PayloadAction<boolean>) {
            state.codeCore.enabled = action.payload;
        },
        updateCodeCore(state, action: PayloadAction<Partial<CodeCoreSettings>>) {
            Object.assign(state.codeCore, action.payload);
        },
        toggleHybridFormat(state, action: PayloadAction<string>) {
            const fmt = action.payload;
            const idx = state.hybrid.formats.indexOf(fmt);
            if (idx >= 0) state.hybrid.formats.splice(idx, 1);
            else state.hybrid.formats.push(fmt);
        },
        toggleVectorFormat(state, action: PayloadAction<string>) {
            const fmt = action.payload;
            const idx = state.vector.formats.indexOf(fmt);
            if (idx >= 0) state.vector.formats.splice(idx, 1);
            else state.vector.formats.push(fmt);
        },
    },
});

export const {
    setHybridEnabled,
    updateHybrid,
    setVectorEnabled,
    updateVector,
    setCodeCoreEnabled,
    updateCodeCore,
    toggleHybridFormat,
    toggleVectorFormat,
} = searchSettingsSlice.actions;

export const selectSearchSettings = (state: RootState) => state.searchSettings;
export const selectHybridSettings = (state: RootState) => state.searchSettings.hybrid;
export const selectVectorSettings = (state: RootState) => state.searchSettings.vector;
export const selectCodeCoreSettings = (state: RootState) => state.searchSettings.codeCore;

export default searchSettingsSlice.reducer;
