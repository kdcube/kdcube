/*
 * SourceTab — read-only source snippet for the selected method/class.
 */
import {useAppSelector} from "../../../app/store.ts";
import {selectConfigAssistantInspect} from "../../../features/configAssistant/configAssistantSlice.ts";
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function SourceTab() {
    const inspect = useAppSelector(selectConfigAssistantInspect);
    const qn = inspect.selectedQualifiedName;

    return (
        <TabFrame title="Source" subtitle={qn ?? "No selection"}>
            {qn ? (
                <pre className="text-xs leading-snug font-mono text-slate-700 bg-slate-50 p-3 rounded border border-slate-200 overflow-x-auto">
                    {`# Source for ${qn}\n# (renderer pending — code_core.source artifact wiring)`}
                </pre>
            ) : (
                <TabEmpty>
                    Pick a method or class in the Footprint tab to see its
                    source here.
                </TabEmpty>
            )}
        </TabFrame>
    );
}

export default SourceTab;
