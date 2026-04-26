/*
 * ConceptTab — renders the canonical definition of a framework concept or
 * style policy when the LLM emits a `code_core.concept` artifact (or the
 * user clicks a Concept node in the graph).
 */
import {useAppSelector} from "../../../app/store.ts";
import {selectConfigAssistantInspect} from "../../../features/configAssistant/configAssistantSlice.ts";
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function ConceptTab() {
    const inspect = useAppSelector(selectConfigAssistantInspect);
    const conceptId = inspect.selectedConceptId;

    if (!conceptId) {
        return (
            <TabFrame title="Concept" subtitle="Framework definitions">
                <TabEmpty>
                    Ask <em>“what is a Bundle?”</em> or click a concept node in
                    the Graph tab. The canonical definition, related concepts,
                    and code symbols that realize it will appear here.
                </TabEmpty>
            </TabFrame>
        );
    }

    return (
        <TabFrame title="Concept" subtitle={conceptId}>
            <div className="text-sm text-slate-700">
                <div className="text-slate-500 italic">
                    Concept payload renderer not wired yet — backend emission of
                    <code className="mx-1 px-1 py-0.5 bg-slate-100 rounded">code_core.concept</code>
                    arrives in the polish pass.
                </div>
            </div>
        </TabFrame>
    );
}

export default ConceptTab;
