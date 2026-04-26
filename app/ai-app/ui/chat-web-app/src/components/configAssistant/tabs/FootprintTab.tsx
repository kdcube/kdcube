/*
 * FootprintTab — structural class card with concepts + style policies pulled
 * from the augmented class_footprint MCP/CodeGraphClient response.
 */
import {useAppSelector} from "../../../app/store.ts";
import {selectConfigAssistantInspect} from "../../../features/configAssistant/configAssistantSlice.ts";
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function FootprintTab() {
    const inspect = useAppSelector(selectConfigAssistantInspect);
    const qn = inspect.selectedQualifiedName;

    if (!qn) {
        return (
            <TabFrame title="Footprint" subtitle="Structural class card">
                <TabEmpty>
                    Ask the assistant about a class — e.g.{" "}
                    <em>“class_footprint of KBClient”</em> — and the methods,
                    callers, embodied concepts, and governing style policies
                    will appear here.
                </TabEmpty>
            </TabFrame>
        );
    }

    return (
        <TabFrame title="Footprint" subtitle={qn}>
            <div className="text-sm text-slate-700">
                <div className="text-slate-500 italic">
                    Footprint payload renderer not wired yet — backend emission
                    of <code className="mx-1 px-1 py-0.5 bg-slate-100 rounded">code_core.footprint</code>
                    arrives in the polish pass.
                </div>
            </div>
        </TabFrame>
    );
}

export default FootprintTab;
