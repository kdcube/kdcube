import {useMemo} from "react";

import {useCodeCoreArtifact} from "../useCodeCoreArtifact.ts";
import AskAgentButton from "./AskAgentButton.tsx";
import {Section} from "./Section.tsx";

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

const KINDS = ["define"] as const;

function PolicyDetails({policyId}: Props) {
    const artifact = useCodeCoreArtifact(KINDS);

    const match = useMemo<PolicyMatch | null>(() => {
        if (!artifact) return null;
        const payload = artifact.content.payload as {matches?: PolicyMatch[]} | null;
        const matches = payload?.matches ?? [];
        const exact = matches.find(
            (m) => m.id?.toLowerCase() === policyId.toLowerCase() && m.kind === "policy",
        );
        return exact ?? matches.find((m) => m.kind === "policy") ?? null;
    }, [artifact, policyId]);

    const askPrompt = useMemo(
        () =>
            `Show me how to apply the "${match?.name ?? policyId}" style policy in my own code. Use the applied_to classes as worked examples.`,
        [match, policyId],
    );

    if (!match) {
        return (
            <div className="text-sm text-slate-700">
                <p className="mb-1 font-medium">{policyId}</p>
                <p className="text-xs text-slate-500">
                    No policy payload yet for this selection. Ask the agent to load it.
                </p>
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
        </div>
    );
}

export default PolicyDetails;
