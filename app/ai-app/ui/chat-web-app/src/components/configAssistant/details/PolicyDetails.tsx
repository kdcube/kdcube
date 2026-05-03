import {useMemo} from "react";

import AskAgentButton from "./AskAgentButton.tsx";
import {Section} from "./Section.tsx";
import {useDefineLookup} from "../useCodeCoreLookup.ts";

interface Props {
    policyId: string;
}

interface PolicyMatch {
    id?: string;
    kind?: string;
    name?: string;
    summary?: string;
    rationale?: string;
    how_to_apply?: string;
    pitfalls?: string[];
    applied_to?: string[];
}

function PolicyDetails({policyId}: Props) {
    const lookup = useDefineLookup(policyId);

    const match = useMemo<PolicyMatch | null>(() => {
        const matches = lookup.data?.matches ?? [];
        const policy = matches.find((m) => m.kind === "policy");
        return policy ?? matches[0] ?? null;
    }, [lookup.data]);

    const askPrompt = useMemo(
        () =>
            `Show me how to apply the "${match?.name ?? policyId}" style policy in my own code. Use the applied_to classes as worked examples.`,
        [match, policyId],
    );

    if (!match) {
        if (lookup.loading) {
            return <div className="text-sm text-slate-500 italic">Loading <code>{policyId}</code>…</div>;
        }
        if (lookup.error) {
            return (
                <div className="text-sm text-slate-700">
                    <p className="mb-1 font-medium">{policyId}</p>
                    <p className="text-xs text-rose-600 mb-2">Lookup failed: {lookup.error}</p>
                    <AskAgentButton
                        label={`Define "${policyId}"`}
                        prompt={`Use code_graph.define to load the "${policyId}" style policy and show me which classes it governs.`}
                    />
                </div>
            );
        }
        return (
            <div className="text-sm text-slate-700">
                <p className="mb-1 font-medium">{policyId}</p>
                <p className="text-xs text-slate-500">No data for this policy yet.</p>
                <AskAgentButton
                    label={`Define "${policyId}"`}
                    prompt={`Use code_graph.define to load the "${policyId}" style policy and show me which classes it governs.`}
                />
            </div>
        );
    }

    return (
        <div className="text-sm text-slate-800">
            <h3 className="text-base font-semibold mb-1">{match.name ?? match.id}</h3>
            {match.summary && <p className="mb-3 leading-relaxed">{match.summary}</p>}

            {match.rationale && <Section title="Why">{match.rationale}</Section>}
            {match.how_to_apply && (
                <Section title="How to apply">
                    <pre className="whitespace-pre-wrap text-[11px] text-slate-700 bg-slate-50 p-2 rounded border border-slate-200">
                        {match.how_to_apply}
                    </pre>
                </Section>
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

            {!!match.applied_to?.length && (
                <Section title="Applied to (worked examples)">
                    <ul className="space-y-0.5 break-all">
                        {match.applied_to.map((q) => (
                            <li key={q} className="font-mono text-[11px]">{q}</li>
                        ))}
                    </ul>
                </Section>
            )}

            <AskAgentButton label="Apply this in my code →" prompt={askPrompt}/>
            {!lookup.fromArtifact && (
                <div className="text-[10px] text-slate-400 mt-2 italic">
                    Loaded directly from the code-graph.
                </div>
            )}
        </div>
    );
}

export default PolicyDetails;
