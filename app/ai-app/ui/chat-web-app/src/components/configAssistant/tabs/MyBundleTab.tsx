/*
 * MyBundleTab — bundle-scoped Semantic nodes (concepts/policies authored
 * in the developer's own `<bundle>/concepts/` folder). Editable view
 * lands in v1.1; v1 is read-only.
 */
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function MyBundleTab() {
    return (
        <TabFrame title="My Bundle" subtitle="Bundle-scoped vocabulary">
            <TabEmpty>
                Concepts authored under your bundle's{" "}
                <code className="mx-1 px-1 py-0.5 bg-slate-100 rounded">/concepts/</code>{" "}
                folder will appear here. Author concepts there to teach your
                bundle's vocabulary to the LLM and the assistant.
            </TabEmpty>
        </TabFrame>
    );
}

export default MyBundleTab;
