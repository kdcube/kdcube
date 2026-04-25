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
    setHybridEnabled,
    updateHybrid,
    setVectorEnabled,
    updateVector,
    setCodeCoreEnabled,
    updateCodeCore,
    toggleHybridFormat,
    toggleVectorFormat,
    updateAdvancedRag,
} from "./searchSettingsSlice.ts";
import {ReactNode, useCallback, useMemo, useRef} from "react";

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

const HybridSection = () => {
    const dispatch = useAppDispatch();
    const settings = useAppSelector(selectHybridSettings);

    const onToggle = useCallback((v: boolean) => dispatch(setHybridEnabled(v)), [dispatch]);
    const onChange = useCallback((patch: Partial<HybridSearchSettings>) => dispatch(updateHybrid(patch)), [dispatch]);
    const onFormatToggle = useCallback((fmt: string) => dispatch(toggleHybridFormat(fmt)), [dispatch]);

    return (
        <Section title="Hybrid Search (KB)" enabled={settings.enabled} onToggle={onToggle}>
            <TextField label="Source Folder" value={settings.source_folder}
                       placeholder="/path/to/documents"
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

/* ------------------------------------------------------------------ */
/*  Panel                                                              */
/* ------------------------------------------------------------------ */

const SearchSettingsPanel = ({visible, className}: WidgetPanelProps) => {
    return useMemo(() => {
        return (
            <div className={`${className ?? ""} ${visible ? "" : "pointer-events-none hidden"}`}>
                <div className="flex flex-col w-full h-full overflow-y-auto p-3">
                    <h2 className="text-lg font-semibold mb-3">Search Settings</h2>
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
