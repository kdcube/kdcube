import {WidgetPanelProps} from "../chatSidePanel/ChatSidePanel.tsx";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {
    ALL_FORMATS,
    FORMAT_LABELS,
    AdvancedRagSettings,
    CodeCoreSettings,
    HybridSearchSettings,
    VectorSearchSettings,
    selectHybridSettings,
    selectVectorSettings,
    selectCodeCoreSettings,
    selectAdvancedRagSettings,
    selectIngestionStatus,
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
} from "./searchSettingsSlice.ts";
import {selectProject} from "../chat/chatSettingsSlice.ts";
import {deriveStage, dispatchProcessing, listResources, uploadFile} from "./kbIngestionService.ts";
import {ReactNode, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {
    selectConfigAssistantMode,
    setMode as setConfigAssistantMode,
} from "../configAssistant/configAssistantSlice.ts";

/* ------------------------------------------------------------------ */
/*  Reusable controls                                                  */
/* ------------------------------------------------------------------ */

interface SectionProps {
    title: string;
    enabled: boolean;
    onToggle: (v: boolean) => void;
    children: ReactNode;
}

const Section = ({title, enabled, onToggle, children}: SectionProps) => {
    return (
        <div className="border border-gray-200 rounded-md mb-3">
            <button
                type="button"
                className="w-full flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-gray-50 rounded-t-md"
                onClick={() => onToggle(!enabled)}
            >
                <span className="font-medium text-sm">{title}</span>
                <span
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${enabled ? "bg-blue-600" : "bg-gray-300"}`}
                >
                    <span
                        className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${enabled ? "translate-x-4.5" : "translate-x-1"}`}
                    />
                </span>
            </button>
            <div className={`px-3 pb-3 pt-1 border-t border-gray-100 flex flex-col gap-2 transition-opacity ${enabled ? "" : "opacity-40 pointer-events-none select-none"}`}>
                {children}
            </div>
        </div>
    );
};

interface TextFieldProps {
    label: string;
    value: string;
    placeholder?: string;
    onChange: (v: string) => void;
    type?: "text" | "password";
    hint?: string;
}

const TextField = ({label, value, placeholder, onChange, type = "text", hint}: TextFieldProps) => {
    return (
        <div className="flex flex-col gap-0.5">
            <span className="text-xs text-gray-600">{label}</span>
            <input
                type={type}
                value={value}
                placeholder={placeholder}
                onChange={e => onChange(e.target.value)}
                className="text-xs border border-gray-200 rounded px-2 py-1.5 bg-white focus:outline-none focus:border-gray-400"
            />
            {hint && <span className="text-[10px] text-gray-400">{hint}</span>}
        </div>
    );
};

interface FilePickerFieldProps {
    label: string;
    filename: string;
    charCount: number;
    onLoad: (content: string, filename: string) => void;
    onClear: () => void;
    accept?: string;
    hint?: string;
}

const FilePickerField = ({label, filename, charCount, onLoad, onClear, accept = ".md,text/markdown", hint}: FilePickerFieldProps) => {
    const inputRef = useRef<HTMLInputElement>(null);

    const handleFile = (file: File | undefined) => {
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
            const content = typeof reader.result === "string" ? reader.result : "";
            onLoad(content, file.name);
        };
        reader.readAsText(file);
    };

    return (
        <div className="flex flex-col gap-0.5">
            <span className="text-xs text-gray-600">{label}</span>
            <div className="flex items-center gap-2">
                <button
                    type="button"
                    onClick={() => inputRef.current?.click()}
                    className="text-xs px-2 py-1 rounded border border-gray-200 bg-white hover:border-gray-400 cursor-pointer"
                >
                    {filename ? "Replace file" : "Load .md file"}
                </button>
                {filename && (
                    <button
                        type="button"
                        onClick={onClear}
                        className="text-xs text-gray-400 hover:text-gray-600 cursor-pointer"
                    >
                        clear
                    </button>
                )}
            </div>
            {filename && (
                <span className="text-[11px] text-gray-500 mt-0.5 font-mono truncate">
                    {filename} — {charCount.toLocaleString()} chars
                </span>
            )}
            <input
                ref={inputRef}
                type="file"
                accept={accept}
                className="hidden"
                onChange={e => handleFile(e.target.files?.[0])}
            />
            {hint && <span className="text-[10px] text-gray-400">{hint}</span>}
        </div>
    );
};

interface SliderFieldProps {
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    onChange: (v: number) => void;
    hint?: string;
}

const SliderField = ({label, value, min, max, step, onChange, hint}: SliderFieldProps) => {
    return (
        <div className="flex flex-col gap-0.5">
            <div className="flex justify-between text-xs text-gray-600">
                <span>{label}</span>
                <span className="font-mono">{value}</span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={e => onChange(parseFloat(e.target.value))}
                className="w-full accent-blue-600"
            />
            {hint && <span className="text-[10px] text-gray-400">{hint}</span>}
        </div>
    );
};

interface SelectFieldProps {
    label: string;
    value: string;
    options: {value: string; label: string}[];
    onChange: (v: string) => void;
}

const SelectField = ({label, value, options, onChange}: SelectFieldProps) => {
    return (
        <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-gray-600">{label}</span>
            <select
                value={value}
                onChange={e => onChange(e.target.value)}
                className="text-xs border border-gray-200 rounded px-1.5 py-1 bg-white"
            >
                {options.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                ))}
            </select>
        </div>
    );
};

interface CheckboxFieldProps {
    label: string;
    checked: boolean;
    onChange: (v: boolean) => void;
    hint?: string;
}

const CheckboxField = ({label, checked, onChange, hint}: CheckboxFieldProps) => {
    return (
        <div className="flex flex-col gap-0.5">
            <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                <input
                    type="checkbox"
                    checked={checked}
                    onChange={e => onChange(e.target.checked)}
                    className="accent-blue-600"
                />
                {label}
            </label>
            {hint && <span className="text-[10px] text-gray-400 ml-5">{hint}</span>}
        </div>
    );
};

interface FormatPickerProps {
    selected: string[];
    onToggle: (fmt: string) => void;
}

const FormatPicker = ({selected, onToggle}: FormatPickerProps) => {
    return (
        <div className="flex flex-col gap-0.5">
            <span className="text-xs text-gray-600">Processing Formats</span>
            <div className="flex flex-wrap gap-1.5 mt-0.5">
                {ALL_FORMATS.map(fmt => {
                    const active = selected.includes(fmt);
                    return (
                        <button
                            key={fmt}
                            type="button"
                            onClick={() => onToggle(fmt)}
                            className={`text-xs px-2 py-0.5 rounded border cursor-pointer transition-colors ${
                                active
                                    ? "bg-blue-50 border-blue-300 text-blue-700"
                                    : "bg-white border-gray-200 text-gray-500 hover:border-gray-300"
                            }`}
                        >
                            {FORMAT_LABELS[fmt] ?? fmt}
                        </button>
                    );
                })}
            </div>
        </div>
    );
};

const SubHeader = ({text}: {text: string}) => (
    <div className="border-t border-gray-100 mt-1 pt-2">
        <span className="text-xs text-gray-400 uppercase tracking-wide">{text}</span>
    </div>
);

const DISTANCE_OPTIONS = [
    {value: "cosine", label: "Cosine"},
    {value: "l2", label: "L2 (Euclidean)"},
    {value: "ip", label: "Inner Product"},
];

const CODE_SEARCH_TYPE_OPTIONS = [
    {value: "hybrid", label: "Hybrid"},
    {value: "fulltext", label: "Fulltext"},
    {value: "vector", label: "Vector"},
];

/* ------------------------------------------------------------------ */
/*  Section contents                                                   */
/* ------------------------------------------------------------------ */

// MIME type guess from filename extension. Used to filter the picked folder
// against the user's chosen `formats`. Browsers populate File.type for some
// extensions but not all (e.g. .md is often empty), so we map the common ones.
const EXT_TO_MIME: Record<string, string> = {
    pdf: "application/pdf",
    md: "text/markdown",
    markdown: "text/markdown",
    txt: "text/plain",
    csv: "text/csv",
    html: "text/html",
    htm: "text/html",
    json: "application/json",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    xml: "application/xml",
    yml: "application/x-yaml",
    yaml: "application/x-yaml",
};

function guessMime(file: File): string {
    if (file.type) return file.type;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    return EXT_TO_MIME[ext] || "application/octet-stream";
}

interface IngestionControlsProps {
    formats: string[];
}

const IngestionControls = ({formats}: IngestionControlsProps) => {
    const dispatch = useAppDispatch();
    const project = useAppSelector(selectProject);
    const status = useAppSelector(selectIngestionStatus);
    const folderInputRef = useRef<HTMLInputElement>(null);
    const [pickedFiles, setPickedFiles] = useState<File[]>([]);
    const [pickedFolderName, setPickedFolderName] = useState<string | null>(null);

    const eligibleFiles = useMemo(() => {
        if (!pickedFiles.length || !formats.length) return pickedFiles;
        const allowed = new Set(formats);
        return pickedFiles.filter(f => allowed.has(guessMime(f)));
    }, [pickedFiles, formats]);

    const onPickFolder = useCallback(() => folderInputRef.current?.click(), []);

    const onFolderChosen = useCallback((files: FileList | null) => {
        if (!files || !files.length) return;
        const arr = Array.from(files);
        // The first relativePath segment is the folder name.
        const rel = (arr[0] as File & {webkitRelativePath?: string}).webkitRelativePath || "";
        const folder = rel.split("/")[0] || "(selection)";
        setPickedFiles(arr);
        setPickedFolderName(folder);
    }, []);

    const onClear = useCallback(() => {
        setPickedFiles([]);
        setPickedFolderName(null);
        if (folderInputRef.current) folderInputRef.current.value = "";
        dispatch(clearIngestionStatus());
    }, [dispatch]);

    const isRunning = status.phase !== "idle" && status.phase !== "done";

    const onApply = useCallback(async () => {
        if (!project) {
            dispatch(ingestionFileFailedUpload({error: "No project context — cannot ingest."}));
            return;
        }
        if (!eligibleFiles.length) return;

        dispatch(startIngestion({
            folderName: pickedFolderName || "(selection)",
            selectedCount: eligibleFiles.length,
            skippedCount: pickedFiles.length - eligibleFiles.length,
        }));

        const dispatchedResources: {id: string; version: string | number}[] = [];

        for (const file of eligibleFiles) {
            try {
                const up = await uploadFile(project, file);
                dispatch(ingestionFileUploaded({resourceId: up.resource_id}));
                try {
                    await dispatchProcessing(project, up.resource_metadata, "");
                    dispatch(ingestionFileDispatched());
                    dispatchedResources.push({
                        id: up.resource_metadata.id,
                        version: up.resource_metadata.version,
                    });
                } catch (e: unknown) {
                    const msg = e instanceof Error ? e.message : String(e);
                    dispatch(ingestionFileFailedDispatch({error: `${file.name}: ${msg}`}));
                }
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                dispatch(ingestionFileFailedUpload({error: `${file.name}: ${msg}`}));
            }
        }

        if (dispatchedResources.length === 0) {
            dispatch(ingestionFinish());
            return;
        }
        dispatch(ingestionEnterProcessingPhase());
    }, [dispatch, project, eligibleFiles, pickedFolderName, pickedFiles.length]);

    // Poll resource processing status while there are active jobs.
    useEffect(() => {
        if (!project) return;
        if (status.phase !== "processing" && status.phase !== "dispatching") return;
        if (status.activeResourceIds.length === 0) return;

        let cancelled = false;
        const seenDone = new Set<string>();

        const tick = async () => {
            try {
                const items = await listResources(project);
                if (cancelled) return;
                const byId = new Map(items.map(r => [String(r.id), r]));
                for (const rid of status.activeResourceIds) {
                    const item = byId.get(rid);
                    if (!item) continue;
                    if (deriveStage(item) === "done" && !seenDone.has(rid)) {
                        seenDone.add(rid);
                        dispatch(ingestionResourceIndexed({resourceId: rid}));
                    }
                }
            } catch {
                // Polling errors are non-fatal — try again next tick.
            }
        };

        const id = window.setInterval(tick, 3000);
        // Tick once immediately so the status updates without waiting 3s.
        tick();
        return () => { cancelled = true; window.clearInterval(id); };
    }, [project, status.phase, status.activeResourceIds, dispatch]);

    const summary = (() => {
        if (status.phase === "idle") return null;
        const parts: string[] = [];
        parts.push(`${status.uploadedCount}/${status.selectedCount} uploaded`);
        if (status.failedUploadCount) parts.push(`${status.failedUploadCount} failed upload`);
        if (status.dispatchedCount) parts.push(`${status.dispatchedCount} dispatched`);
        if (status.indexedCount) parts.push(`${status.indexedCount} indexed`);
        if (status.failedDispatchCount) parts.push(`${status.failedDispatchCount} failed dispatch`);
        if (status.skippedCount) parts.push(`${status.skippedCount} skipped (format)`);
        return parts.join(" · ");
    })();

    return (
        <div className="flex flex-col gap-1 border-b border-gray-100 pb-2 mb-1">
            <span className="text-xs text-gray-600">Ingest folder into the KB</span>
            <div className="flex items-center gap-2">
                <button
                    type="button"
                    onClick={onPickFolder}
                    disabled={isRunning}
                    className="text-xs px-2 py-1 rounded border border-gray-200 bg-white hover:border-gray-400 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                >
                    {pickedFolderName ? "Change folder" : "Choose folder..."}
                </button>
                {pickedFolderName && (
                    <button
                        type="button"
                        onClick={onClear}
                        disabled={isRunning}
                        className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50 cursor-pointer"
                    >
                        clear
                    </button>
                )}
                {pickedFolderName && (
                    <span className="text-[11px] text-gray-500 font-mono truncate">
                        {pickedFolderName} — {eligibleFiles.length}/{pickedFiles.length} files
                    </span>
                )}
            </div>
            <input
                ref={folderInputRef}
                type="file"
                /* @ts-expect-error webkitdirectory is non-standard but supported in Chromium/Edge/Firefox */
                webkitdirectory=""
                directory=""
                multiple
                className="hidden"
                onChange={e => onFolderChosen(e.target.files)}
            />
            <button
                type="button"
                onClick={onApply}
                disabled={isRunning || !pickedFolderName || eligibleFiles.length === 0 || !project}
                className="text-xs px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
                {isRunning ? "Ingesting..." : "Apply settings & ingest"}
            </button>
            {summary && (
                <div className="mt-1 text-[11px] text-gray-600 font-mono whitespace-pre-wrap">
                    {summary}
                    {status.lastError && (
                        <div className="text-red-600 mt-0.5 break-all">{status.lastError}</div>
                    )}
                </div>
            )}
            {!project && (
                <span className="text-[10px] text-amber-600">No project context — open a chat first so tenant/project resolves.</span>
            )}
        </div>
    );
};

const HybridSection = () => {
    const dispatch = useAppDispatch();
    const settings = useAppSelector(selectHybridSettings);

    const onToggle = useCallback((v: boolean) => dispatch(setHybridEnabled(v)), [dispatch]);
    const onChange = useCallback((patch: Partial<HybridSearchSettings>) => dispatch(updateHybrid(patch)), [dispatch]);
    const onFormatToggle = useCallback((fmt: string) => dispatch(toggleHybridFormat(fmt)), [dispatch]);

    return (
        <Section title="Hybrid Search (KB)" enabled={settings.enabled} onToggle={onToggle}>
            <IngestionControls formats={settings.formats}/>
            <TextField label="Source Folder (path-only, advisory)" value={settings.source_folder}
                       placeholder="(used by deferred indexing-config phase; not consumed today)"
                       onChange={v => onChange({source_folder: v})}/>
            <FormatPicker selected={settings.formats} onToggle={onFormatToggle}/>

            <SubHeader text="Graph (Neo4j)"/>
            <TextField label="URI" value={settings.neo4j_uri}
                       placeholder="bolt://neo4j:7687"
                       onChange={v => onChange({neo4j_uri: v})}/>
            <div className="flex gap-2">
                <div className="flex-1">
                    <TextField label="User" value={settings.neo4j_user}
                               placeholder="neo4j"
                               onChange={v => onChange({neo4j_user: v})}/>
                </div>
                <div className="flex-1">
                    <TextField label="Password" value={settings.neo4j_password}
                               placeholder="password" type="password"
                               onChange={v => onChange({neo4j_password: v})}/>
                </div>
            </div>
            <TextField label="Database" value={settings.neo4j_database}
                       placeholder="neo4j"
                       onChange={v => onChange({neo4j_database: v})}/>

            <SubHeader text="Conventions"/>
            <FilePickerField label="Parsing Prompt (.md)"
                             filename={settings.conventions_filename}
                             charCount={settings.conventions.length}
                             onLoad={(content, filename) => onChange({conventions: content, conventions_filename: filename})}
                             onClear={() => onChange({conventions: "", conventions_filename: ""})}
                             hint="Controls how data is extracted and structured in the knowledge graph."/>

            <SubHeader text="Retrieval"/>
            <SliderField label="Top K (Vector)" value={settings.top_k_vector} min={1} max={30} step={1}
                         onChange={v => onChange({top_k_vector: v})}
                         hint="Chunks from vector similarity. 8-15 is the sweet spot."/>
            <SliderField label="Top K (Graph)" value={settings.top_k_graph} min={1} max={30} step={1}
                         onChange={v => onChange({top_k_graph: v})}
                         hint="Chunks from graph entity text-match. Complements vector search."/>
            <SliderField label="Min Score" value={settings.min_score_threshold} min={0} max={1} step={0.05}
                         onChange={v => onChange({min_score_threshold: parseFloat(v.toFixed(2))})}
                         hint="Discard chunks below this similarity. Lower = broader, higher = stricter."/>
            <SliderField label="Context Window" value={settings.context_window} min={0} max={5} step={1}
                         onChange={v => onChange({context_window: v})}
                         hint="Fetch +/- N neighboring chunks from same document."/>
            <CheckboxField label="Rerank results" checked={settings.use_reranking}
                           onChange={v => onChange({use_reranking: v})}
                           hint="CrossEncoder re-scores chunks. More accurate, adds ~1-2s."/>
            <SelectField label="Distance" value={settings.distance_type} options={DISTANCE_OPTIONS}
                         onChange={v => onChange({distance_type: v as HybridSearchSettings["distance_type"]})}/>
        </Section>
    );
};

const VectorSection = () => {
    const dispatch = useAppDispatch();
    const settings = useAppSelector(selectVectorSettings);

    const onToggle = useCallback((v: boolean) => dispatch(setVectorEnabled(v)), [dispatch]);
    const onChange = useCallback((patch: Partial<VectorSearchSettings>) => dispatch(updateVector(patch)), [dispatch]);
    const onFormatToggle = useCallback((fmt: string) => dispatch(toggleVectorFormat(fmt)), [dispatch]);

    return (
        <Section title="Vector Search (KB)" enabled={settings.enabled} onToggle={onToggle}>
            <TextField label="Source Folder" value={settings.source_folder}
                       placeholder="/path/to/documents"
                       onChange={v => onChange({source_folder: v})}/>
            <FormatPicker selected={settings.formats} onToggle={onFormatToggle}/>
            <SubHeader text="Retrieval"/>
            <SliderField label="Top K" value={settings.top_k_vector} min={1} max={30} step={1}
                         onChange={v => onChange({top_k_vector: v})}
                         hint="Chunks from vector similarity. 8-15 is the sweet spot."/>
            <SliderField label="Min Score" value={settings.min_score_threshold} min={0} max={1} step={0.05}
                         onChange={v => onChange({min_score_threshold: parseFloat(v.toFixed(2))})}
                         hint="Discard chunks below this similarity."/>
            <SliderField label="Context Window" value={settings.context_window} min={0} max={5} step={1}
                         onChange={v => onChange({context_window: v})}
                         hint="Fetch +/- N neighboring chunks from same document."/>
            <CheckboxField label="Rerank results" checked={settings.use_reranking}
                           onChange={v => onChange({use_reranking: v})}
                           hint="CrossEncoder re-scores chunks. More accurate, adds ~1-2s."/>
            <SelectField label="Distance" value={settings.distance_type} options={DISTANCE_OPTIONS}
                         onChange={v => onChange({distance_type: v as VectorSearchSettings["distance_type"]})}/>
        </Section>
    );
};

const CodeCoreSection = () => {
    const dispatch = useAppDispatch();
    const settings = useAppSelector(selectCodeCoreSettings);

    const onToggle = useCallback((v: boolean) => dispatch(setCodeCoreEnabled(v)), [dispatch]);
    const onChange = useCallback((patch: Partial<CodeCoreSettings>) => dispatch(updateCodeCore(patch)), [dispatch]);

    return (
        <Section title="Code Core" enabled={settings.enabled} onToggle={onToggle}>
            <SelectField label="Search Type" value={settings.search_type} options={CODE_SEARCH_TYPE_OPTIONS}
                         onChange={v => onChange({search_type: v as CodeCoreSettings["search_type"]})}/>
            <SliderField label="Result Limit" value={settings.limit} min={1} max={30} step={1}
                         onChange={v => onChange({limit: v})}/>
        </Section>
    );
};

// Advanced RAG knobs that aren't covered by the Hybrid section.
// The pipeline reuses hybrid.{top_k_vector, use_reranking, min_score_threshold,
// context_window, distance_type} when they're set; this section only exposes
// the fields that don't already exist there.
const AdvancedRagSection = () => {
    const dispatch = useAppDispatch();
    const settings = useAppSelector(selectAdvancedRagSettings);
    const hybrid = useAppSelector(selectHybridSettings);

    const onChange = useCallback(
        (patch: Partial<AdvancedRagSettings>) => dispatch(updateAdvancedRag(patch)),
        [dispatch],
    );
    // The advanced-RAG tool is gated by hybrid.enabled (it runs over the KB)
    // so we expose the toggle here as a read-only mirror.
    const enabled = hybrid.enabled;

    return (
        <Section title="Advanced RAG (multi-step)" enabled={enabled} onToggle={() => { /* mirror of hybrid */ }}>
            <span className="text-[10px] text-gray-400">
                Multi-step KB retrieval — query rewrite, entity extraction, dual-pass hybrid, compound rerank.
                Reuses Hybrid Search settings (top_k, rerank, min_score, context window, distance) when enabled.
            </span>
            <SubHeader text="Pipeline steps"/>
            <CheckboxField label="Rewrite follow-up questions"
                           checked={settings.enable_query_rewrite}
                           onChange={v => onChange({enable_query_rewrite: v})}
                           hint="Resolve pronouns/ellipsis using conversation history before searching."/>
            <CheckboxField label="Entity-driven second pass"
                           checked={settings.enable_entity_pass}
                           onChange={v => onChange({enable_entity_pass: v})}
                           hint="Extract named entities/IDs from the question and run a second hybrid pass on them."/>
            <SliderField label="Entity pass top K"
                         value={settings.entity_top_k} min={1} max={20} step={1}
                         onChange={v => onChange({entity_top_k: v})}
                         hint="Chunks fetched in the entity pass. 4-8 is typical."/>
            <SliderField label="Min priority slots"
                         value={settings.min_priority_slots} min={0} max={5} step={1}
                         onChange={v => onChange({min_priority_slots: v})}
                         hint="Guarantee N rows in the top window contain a priority/entity match (0 = no guarantee)."/>
        </Section>
    );
};

// Toggles the bundle-developer Configuration Assistant mode. When on, the
// agent is primed (via persona prompt) to use code_graph.* tools, and the
// inspect drawer slides in from the right whenever a code_graph.* call lands.
const ConfigAssistantSection = () => {
    const dispatch = useAppDispatch();
    const mode = useAppSelector(selectConfigAssistantMode);
    const enabled = mode === "config_assistant";

    const onToggle = useCallback(
        (v: boolean) => dispatch(setConfigAssistantMode(v ? "config_assistant" : null)),
        [dispatch],
    );

    return (
        <Section title="Configuration Assistant" enabled={enabled} onToggle={onToggle}>
            <span className="text-[11px] text-gray-500 leading-snug">
                Bundle-developer helper. Activates a slide-in graph + details drawer
                on the right of the chat when the agent calls code-graph tools, and
                primes the agent to lean on those tools when answering.
            </span>
            <span className="text-[10px] text-gray-400 leading-snug">
                Best with bundles that already register the <code>code_graph</code> plugin
                (e.g. <code>react.code</code>). Other bundles can opt in by adding it to
                their <code>tools_descriptor.py</code>.
            </span>
        </Section>
    );
};

/* ------------------------------------------------------------------ */
/*  Panel                                                              */
/* ------------------------------------------------------------------ */

const SearchSettingsPanel = ({visible, className}: WidgetPanelProps) => {
    return useMemo(() => {
        return (
            <div className={`${className ?? ""} ${visible ? "" : "pointer-events-none hidden"}`}>
                <div className="flex flex-col w-full h-full overflow-y-auto p-3">
                    <h2 className="text-lg font-semibold mb-3">Search Settings</h2>
                    <ConfigAssistantSection/>
                    <HybridSection/>
                    <AdvancedRagSection/>
                    <VectorSection/>
                    <CodeCoreSection/>
                </div>
            </div>
        );
    }, [className, visible]);
};

export default SearchSettingsPanel;
