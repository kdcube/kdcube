/*
 * SPDX-License-Identifier: MIT
 * InspectPanel — right column of the Configuration Assistant.
 *
 * Vertical split:
 *   Top    — Graph (always visible, interactive, pedagogical)
 *   Bottom — Details (selection-driven: concept / policy / class / empty)
 *
 * The chat (left/center) is the configurator + code-writer. This panel is
 * the *understanding* surface.
 */
import GraphPane from "./panes/GraphPane.tsx";
import DetailsPane from "./panes/DetailsPane.tsx";

function InspectPanel() {
    return (
        <div className="flex flex-col h-full w-[440px] min-w-[380px] max-w-[560px] border-l border-slate-200 bg-white">
            <div className="flex-[3] min-h-[260px] border-b border-slate-200">
                <GraphPane/>
            </div>
            <div className="flex-[2] min-h-[220px]">
                <DetailsPane/>
            </div>
        </div>
    );
}

export default InspectPanel;
