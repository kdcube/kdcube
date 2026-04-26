/*
 * SPDX-License-Identifier: MIT
 * DetailsPane — bottom pane of the inspect column. Selection-driven.
 *
 * Renders one of four modes based on configAssistantSlice.selection:
 *   null    → EmptyDetails  (hint to click a graph node or ask the agent)
 *   class   → ClassDetails  (footprint + concepts + style policies)
 *   concept → ConceptDetails (definition + related + realized_by)
 *   policy  → PolicyDetails (rule + rationale + how_to_apply + applied_to)
 *
 * Each mode ends with an "Ask the agent →" button that fills the composer
 * with a context-aware prompt so the user can drive the next step.
 */
import {useAppSelector} from "../../../app/store.ts";
import {selectConfigAssistantSelection} from "../../../features/configAssistant/configAssistantSlice.ts";
import EmptyDetails from "../details/EmptyDetails.tsx";
import ClassDetails from "../details/ClassDetails.tsx";
import ConceptDetails from "../details/ConceptDetails.tsx";
import PolicyDetails from "../details/PolicyDetails.tsx";

function DetailsPane() {
    const selection = useAppSelector(selectConfigAssistantSelection);

    let body: React.ReactNode;
    switch (selection.kind) {
        case "class":
            body = <ClassDetails qualifiedName={selection.qualifiedName ?? ""}/>;
            break;
        case "concept":
            body = <ConceptDetails conceptId={selection.conceptId ?? ""}/>;
            break;
        case "policy":
            body = <PolicyDetails policyId={selection.conceptId ?? ""}/>;
            break;
        default:
            body = <EmptyDetails/>;
    }

    return (
        <div className="flex flex-col h-full bg-white">
            <div className="flex flex-row items-center justify-between px-3 py-2 border-b border-slate-200 bg-slate-50">
                <div className="text-xs font-semibold text-slate-700">Details</div>
                {selection.kind && (
                    <div className="text-[10px] text-slate-500 uppercase tracking-wide">
                        {selection.kind}
                    </div>
                )}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">{body}</div>
        </div>
    );
}

export default DetailsPane;
