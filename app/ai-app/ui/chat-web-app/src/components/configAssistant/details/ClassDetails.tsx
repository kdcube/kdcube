import {useMemo} from "react";

import AskAgentButton from "./AskAgentButton.tsx";
import {Section} from "./Section.tsx";
import {useFootprintLookup} from "../useCodeCoreLookup.ts";

interface Props {
    qualifiedName: string;
}

interface SemanticBadge {
    id?: string;
    name?: string;
    summary?: string;
}

function ClassDetails({qualifiedName}: Props) {
    const lookup = useFootprintLookup(qualifiedName);

    const data = useMemo(() => {
        if (!lookup.data) return null;
        const fp = lookup.data.footprint?.[0];
        if (!fp) return null;
        return {
            footprint: fp,
            concepts: (lookup.data.concepts ?? []) as SemanticBadge[],
            style_policies: (lookup.data.style_policies ?? []) as SemanticBadge[],
        };
    }, [lookup.data]);

    const askPrompt = useMemo(
        () =>
            `Walk me through how to extend ${qualifiedName} for my own bundle. List which methods I'd typically override, which concepts it embodies, and which style policies I must follow.`,
        [qualifiedName],
    );

    if (!data) {
        const shortName = qualifiedName.split(".").slice(-1)[0] || qualifiedName;
        if (lookup.loading) {
            return (
                <div className="text-sm text-slate-700">
                    <p className="mb-1 font-medium">{shortName}</p>
                    <p className="text-[10px] font-mono text-slate-500 mb-2 break-all">{qualifiedName}</p>
                    <p className="text-xs text-slate-500 italic">Loading footprint…</p>
                </div>
            );
        }
        if (lookup.error) {
            return (
                <div className="text-sm text-slate-700">
                    <p className="mb-1 font-medium">{shortName}</p>
                    <p className="text-[10px] font-mono text-slate-500 mb-2 break-all">{qualifiedName}</p>
                    <p className="text-xs text-rose-600 mb-2">Lookup failed: {lookup.error}</p>
                    <AskAgentButton
                        label="Load class_footprint"
                        prompt={`Use code_graph.class_footprint on ${qualifiedName} and tell me what concepts it embodies and which style policies govern it.`}
                    />
                </div>
            );
        }
        return (
            <div className="text-sm text-slate-700">
                <p className="mb-1 font-medium">{shortName}</p>
                <p className="text-[10px] font-mono text-slate-500 mb-2 break-all">{qualifiedName}</p>
                <p className="text-xs text-slate-500">No footprint loaded yet.</p>
                <AskAgentButton
                    label="Load class_footprint"
                    prompt={`Use code_graph.class_footprint on ${qualifiedName} and tell me what concepts it embodies and which style policies govern it.`}
                />
            </div>
        );
    }

    const {footprint, concepts, style_policies} = data;
    const methodList = (footprint.methods ?? []).filter((m) => m && m.name);

    return (
        <div className="text-sm text-slate-800">
            <div className="mb-2">
                <h3 className="text-base font-semibold">{footprint.name}</h3>
                <p className="text-[10px] font-mono text-slate-500 break-all">
                    {footprint.qualified_name}
                </p>
            </div>

            {footprint.docstring && (
                <p className="text-xs italic text-slate-600 mb-3 leading-relaxed">
                    {footprint.docstring}
                </p>
            )}

            {!!concepts.length && (
                <Section title="Concepts">
                    <div className="flex flex-row flex-wrap gap-1">
                        {concepts.map((c) => (
                            <span
                                key={c.id}
                                className="text-[11px] px-1.5 py-0.5 rounded border border-amber-300 bg-amber-50 text-amber-800"
                                title={c.summary}
                            >
                                {c.name ?? c.id}
                            </span>
                        ))}
                    </div>
                </Section>
            )}

            {!!style_policies.length && (
                <Section title="Style policies">
                    <div className="flex flex-row flex-wrap gap-1">
                        {style_policies.map((p) => (
                            <span
                                key={p.id}
                                className="text-[11px] px-1.5 py-0.5 rounded border border-violet-300 bg-violet-50 text-violet-800"
                                title={p.summary}
                            >
                                {p.name ?? p.id}
                            </span>
                        ))}
                    </div>
                </Section>
            )}

            {!!footprint.ancestors?.filter(Boolean).length && (
                <Section title="Inherits">
                    <ul className="space-y-0.5 break-all">
                        {footprint.ancestors.filter(Boolean).map((q) => (
                            <li key={q} className="font-mono text-[11px]">{q}</li>
                        ))}
                    </ul>
                </Section>
            )}

            {!!methodList.length && (
                <Section title={`Methods (${methodList.length})`}>
                    <ul className="space-y-0.5">
                        {methodList.slice(0, 12).map((m, i) => (
                            <li key={`${m.name}-${i}`} className="font-mono text-[11px]">
                                {m.is_abstract && <span className="text-rose-600 mr-1">abstract</span>}
                                <span className="text-slate-900">{m.name}</span>
                                {m.signature && <span className="text-slate-500">{m.signature}</span>}
                            </li>
                        ))}
                        {methodList.length > 12 && (
                            <li className="text-[11px] text-slate-500 italic">
                                … {methodList.length - 12} more
                            </li>
                        )}
                    </ul>
                </Section>
            )}

            {!!footprint.callers?.filter(Boolean).length && (
                <Section title={`Used by (${footprint.callers.filter(Boolean).length})`}>
                    <ul className="space-y-0.5 break-all">
                        {footprint.callers.filter(Boolean).slice(0, 6).map((q) => (
                            <li key={q} className="font-mono text-[11px]">{q}</li>
                        ))}
                    </ul>
                </Section>
            )}

            <AskAgentButton label="How do I extend this →" prompt={askPrompt}/>
            {!lookup.fromArtifact && (
                <div className="text-[10px] text-slate-400 mt-2 italic">
                    Loaded directly from the code-graph.
                </div>
            )}
        </div>
    );
}

export default ClassDetails;
