/*
 * GraphTab — interactive class+concept dependency graph (v1.1).
 * Empty state for v1; xyflow integration arrives in Phase 5.
 */
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function GraphTab() {
    return (
        <TabFrame title="Graph" subtitle="Class + concept dependency map">
            <TabEmpty>
                Ask the assistant about a class — e.g.{" "}
                <em>“show me BaseEntrypoint and what extends it”</em> — and the
                graph will be drawn here.
            </TabEmpty>
        </TabFrame>
    );
}

export default GraphTab;
