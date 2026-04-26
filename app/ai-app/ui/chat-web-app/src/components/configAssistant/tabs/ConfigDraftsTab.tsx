/*
 * ConfigDraftsTab — collected `code_core.config_draft` artifacts produced
 * during the conversation. Each draft has a target_path + content with a
 * Copy button (Apply deferred to v1.1).
 */
import {TabEmpty, TabFrame} from "./TabFrame.tsx";

function ConfigDraftsTab() {
    return (
        <TabFrame title="Config drafts" subtitle="Generated bundle scaffolding">
            <TabEmpty>
                When the assistant produces YAML, JSON, or markdown that should
                land in your bundle (e.g.{" "}
                <code className="mx-1 px-1 py-0.5 bg-slate-100 rounded">tools_descriptor.py</code>{" "}
                snippets,{" "}
                <code className="mx-1 px-1 py-0.5 bg-slate-100 rounded">concepts/*.md</code>{" "}
                files), each draft will appear here with a Copy button.
            </TabEmpty>
        </TabFrame>
    );
}

export default ConfigDraftsTab;
