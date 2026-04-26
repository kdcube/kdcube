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

// Advanced RAG knobs not already covered by hybrid.* (the pipeline reuses
// hybrid.top_k_vector / use_reranking / min_score_threshold / context_window
// when present).
export interface AdvancedRagSettings {
    enable_query_rewrite: boolean;
    enable_entity_pass: boolean;
    entity_top_k: number;
    min_priority_slots: number;
}

// Transient ingestion state for the "Apply settings & ingest" button in the
// search settings panel. Lives in Redux so progress survives panel toggles.
export type IngestionPhase = "idle" | "uploading" | "dispatching" | "processing" | "done";

export interface IngestionState {
    phase: IngestionPhase;
    folderName: string | null;
    selectedCount: number;
    skippedCount: number;
    uploadedCount: number;
    failedUploadCount: number;
    dispatchedCount: number;
    failedDispatchCount: number;
    indexedCount: number;
    activeResourceIds: string[];
    lastError: string | null;
    startedAt: number | null;
    finishedAt: number | null;
}

export interface SearchSettingsState {
    hybrid: HybridSearchSettings;
    vector: VectorSearchSettings;
    codeCore: CodeCoreSettings;
    advancedRag: AdvancedRagSettings;
    ingestion: IngestionState;
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
    advancedRag: {
        enable_query_rewrite: true,
        enable_entity_pass: true,
        entity_top_k: 6,
        min_priority_slots: 0,
    },
    ingestion: {
        phase: "idle",
        folderName: null,
        selectedCount: 0,
        skippedCount: 0,
        uploadedCount: 0,
        failedUploadCount: 0,
        dispatchedCount: 0,
        failedDispatchCount: 0,
        indexedCount: 0,
        activeResourceIds: [],
        lastError: null,
        startedAt: null,
        finishedAt: null,
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
        updateAdvancedRag(state, action: PayloadAction<Partial<AdvancedRagSettings>>) {
            Object.assign(state.advancedRag, action.payload);
        },
        startIngestion(state, action: PayloadAction<{folderName: string; selectedCount: number; skippedCount: number}>) {
            state.ingestion = {
                ...initialState.ingestion,
                phase: "uploading",
                folderName: action.payload.folderName,
                selectedCount: action.payload.selectedCount,
                skippedCount: action.payload.skippedCount,
                startedAt: Date.now(),
            };
        },
        ingestionFileUploaded(state, action: PayloadAction<{resourceId: string}>) {
            state.ingestion.uploadedCount += 1;
            state.ingestion.activeResourceIds.push(action.payload.resourceId);
        },
        ingestionFileFailedUpload(state, action: PayloadAction<{error: string}>) {
            state.ingestion.failedUploadCount += 1;
            state.ingestion.lastError = action.payload.error;
        },
        ingestionFileDispatched(state) {
            state.ingestion.dispatchedCount += 1;
            if (state.ingestion.phase === "uploading") {
                state.ingestion.phase = "dispatching";
            }
        },
        ingestionFileFailedDispatch(state, action: PayloadAction<{error: string}>) {
            state.ingestion.failedDispatchCount += 1;
            state.ingestion.lastError = action.payload.error;
        },
        ingestionEnterProcessingPhase(state) {
            if (state.ingestion.phase !== "done") {
                state.ingestion.phase = "processing";
            }
        },
        ingestionResourceIndexed(state, action: PayloadAction<{resourceId: string}>) {
            state.ingestion.indexedCount += 1;
            state.ingestion.activeResourceIds = state.ingestion.activeResourceIds.filter(
                id => id !== action.payload.resourceId,
            );
            const totalDone = state.ingestion.indexedCount + state.ingestion.failedUploadCount + state.ingestion.failedDispatchCount;
            if (totalDone >= state.ingestion.selectedCount && state.ingestion.activeResourceIds.length === 0) {
                state.ingestion.phase = "done";
                state.ingestion.finishedAt = Date.now();
            }
        },
        ingestionFinish(state) {
            state.ingestion.phase = "done";
            state.ingestion.finishedAt = Date.now();
        },
        clearIngestionStatus(state) {
            state.ingestion = {...initialState.ingestion};
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
    updateAdvancedRag,
    startIngestion,
    ingestionFileUploaded,
    ingestionFileFailedUpload,
    ingestionFileDispatched,
    ingestionFileFailedDispatch,
    ingestionEnterProcessingPhase,
    ingestionResourceIndexed,
    ingestionFinish,
    clearIngestionStatus,
} = searchSettingsSlice.actions;

export const selectSearchSettings = (state: RootState) => state.searchSettings;
export const selectHybridSettings = (state: RootState) => state.searchSettings.hybrid;
export const selectVectorSettings = (state: RootState) => state.searchSettings.vector;
export const selectCodeCoreSettings = (state: RootState) => state.searchSettings.codeCore;
export const selectAdvancedRagSettings = (state: RootState) => state.searchSettings.advancedRag;
export const selectIngestionStatus = (state: RootState) => state.searchSettings.ingestion;

export default searchSettingsSlice.reducer;
