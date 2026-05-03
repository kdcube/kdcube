import {useMemo} from "react";

import AskAgentButton from "./AskAgentButton.tsx";
import {Section} from "./Section.tsx";
import {useDefineLookup} from "../useCodeCoreLookup.ts";

interface Props {
    conceptId: string;
}

interface SemanticMatch {
    id?: string;
    kind?: string;
    name?: string;
    aliases?: string[];
    summary?: string;
    definition?: string;
    pitfalls?: string[];
    related?: Array<{id?: string; name?: string; kind?: string}>;
    realized_by?: string[];
}

function ConceptDetails({conceptId}: Props) {
    const lookup = useDefineLookup(conceptId);

    const match = useMemo<SemanticMatch | null>(() => {
        const matches = lookup.data?.matches ?? [];
        return matches[0] ?? null;
    }, [lookup.data]);

    const askPrompt = useMemo(
        () =>
            `Tell me how to use the "${match?.name ?? conceptId}" concept in my own bundle. Reference the realized_by classes and any style policies that govern them.`,
        [match, conceptId],
    );

    if (!match) {
        if (lookup.loading) {
            return (
                <div className="text-sm text-slate-500 italic">
                    Loading <code className="text-xs">{conceptId}</code>…
                </div>
            );
        }
        if (lookup.error) {
            return (
                <div className="text-sm text-slate-700">
                    <p className="mb-1 font-medium">{conceptId}</p>
                    <p className="text-xs text-rose-600 mb-2">Lookup failed: {lookup.error}</p>
                    <AskAgentButton
                        label={`Define "${conceptId}"`}
                        prompt={`Use code_graph.define to define "${conceptId}" and show me where it is realized in code.`}
                    />
                </div>
            );
        }
        return (
            <div className="text-sm text-slate-700">
                <p className="mb-1 font-medium">{conceptId}</p>
                <p className="text-xs text-slate-500">No data for this concept yet.</p>
                <AskAgentButton
                    label={`Define "${conceptId}"`}
                    prompt={`Use code_graph.define to define "${conceptId}" and show me where it is realized in code.`}
                />
            </div>
        );
    }

    return (
        <div className="text-sm text-slate-800">
            <div className="flex flex-row items-baseline justify-between gap-2 mb-1">
                <h3 className="text-base font-semibold">{match.name ?? match.id}</h3>
                {match.aliases?.length ? (
                    <span className="text-[10px] text-slate-500">
                        aka {match.aliases.join(", ")}
                    </span>
                ) : null}
            </div>
            {match.summary && <p className="mb-3 leading-relaxed">{match.summary}</p>}

            {match.definition && match.definition !== match.summary && (
                <details className="mb-3">
                    <summary className="text-xs font-medium text-slate-500 cursor-pointer">
                        Full definition
                    </summary>
                    <pre className="mt-1 text-xs whitespace-pre-wrap text-slate-700 bg-slate-50 p-2 rounded border border-slate-200">
                        {match.definition}
                    </pre>
                </details>
            )}

            {!!match.pitfalls?.length && (
                <Section title="Pitfalls">
                    <ul className="list-disc pl-5 space-y-0.5">
                        {match.pitfalls.map((p, i) => (
                            <li key={i}>{p}</li>
                        ))}
                    </ul>
                </Section>
            )}

            {!!match.related?.length && (
                <Section title="Related">
                    <div className="flex flex-row flex-wrap gap-1">
                        {match.related.map((r) => (
                            <span
                                key={r.id}
                                className="text-[11px] px-1.5 py-0.5 rounded border border-amber-300 bg-amber-50 text-amber-800"
                            >
                                {r.name ?? r.id}
                            </span>
                        ))}
                    </div>
                </Section>
            )}

            {!!match.realized_by?.length && (
                <Section title="Realized by">
                    <ul className="space-y-0.5 break-all">
                        {match.realized_by.map((q) => (
                            <li key={q} className="font-mono text-[11px]">{q}</li>
                        ))}
                    </ul>
                </Section>
            )}

            <AskAgentButton label="How do I use this →" prompt={askPrompt}/>
            {!lookup.fromArtifact && (
                <div className="text-[10px] text-slate-400 mt-2 italic">
                    Loaded directly from the code-graph (no agent round-trip).
                </div>
            )}
        </div>
    );
}

export default ConceptDetails;
