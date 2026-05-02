/*
 * SPDX-License-Identifier: MIT
 * InspectDrawer — global, slide-in panel anchored to the right edge of any
 * chat. Visible only when:
 *   - configAssistant.mode === "config_assistant"  (user opted in via settings)
 *   - configAssistant.drawerOpen === true           (auto-opened on artifact
 *                                                    arrival OR toggled manually)
 *
 * Auto-opens when a new code_core.* artifact lands in the current turn,
 * unless the user has explicitly closed it (sticky userClosed bit, reset
 * on conversation change).
 */
import {useCallback, useEffect, useRef} from "react";
import {ChevronsLeft, ChevronsRight, X} from "lucide-react";

import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectConversationId, selectCurrentTurn} from "../../features/chat/chatStateSlice.ts";
import {
    closeDrawer,
    ensureDrawerOpen,
    resetDrawerStickiness,
    selectClass,
    selectConcept,
    selectConfigAssistantDrawerOpen,
    selectConfigAssistantMode,
    toggleDrawer,
} from "../../features/configAssistant/configAssistantSlice.ts";
import {CODE_CORE_ARTIFACT_TYPE, CodeCoreArtifact} from "../../features/logExtensions/codeCore/types.ts";
import GraphPane from "./panes/GraphPane.tsx";
import DetailsPane from "./panes/DetailsPane.tsx";

function InspectDrawer() {
    const dispatch = useAppDispatch();
    const mode = useAppSelector(selectConfigAssistantMode);
    const open = useAppSelector(selectConfigAssistantDrawerOpen);
    const conversationId = useAppSelector(selectConversationId);
    const currentTurn = useAppSelector(selectCurrentTurn);

    // Reset drawer stickiness when the conversation changes — a new
    // conversation starts fresh, last-turn close doesn't carry over.
    useEffect(() => {
        dispatch(resetDrawerStickiness());
    }, [conversationId, dispatch]);

    // Watch for new code_core.* artifacts in the current turn; auto-open
    // (respecting the userClosed bit) AND auto-select the subject of the
    // latest one so the DetailsPane populates without a manual graph click.
    const lastSeenCount = useRef(0);
    useEffect(() => {
        if (mode !== "config_assistant") {
            lastSeenCount.current = 0;
            return;
        }
        const artifacts = (currentTurn?.artifacts ?? []).filter(
            (a): a is CodeCoreArtifact => a.artifactType === CODE_CORE_ARTIFACT_TYPE,
        );
        const count = artifacts.length;
        if (count > lastSeenCount.current) {
            dispatch(ensureDrawerOpen());

            // Auto-select the subject of the most recent artifact.
            const latest = artifacts[artifacts.length - 1];
            if (latest) {
                const kind = latest.content.kind;
                const payload = latest.content.payload as Record<string, unknown> | null;
                if (kind === "define" && payload && Array.isArray(payload.matches)) {
                    const first = payload.matches[0] as
                        | {id?: string; kind?: string}
                        | undefined;
                    if (first?.id) {
                        dispatch(
                            selectConcept({
                                conceptId: first.id,
                                isPolicy: first.kind === "policy",
                            }),
                        );
                    }
                } else if (
                    kind === "class_footprint"
                    && payload
                    && Array.isArray(payload.footprint)
                ) {
                    const fp = payload.footprint[0] as
                        | {qualified_name?: string}
                        | undefined;
                    if (fp?.qualified_name) {
                        dispatch(selectClass(fp.qualified_name));
                    }
                }
            }
        }
        lastSeenCount.current = count;
    }, [currentTurn, mode, dispatch]);

    const onClose = useCallback(() => dispatch(closeDrawer()), [dispatch]);
    const onToggle = useCallback(() => dispatch(toggleDrawer()), [dispatch]);

    if (mode !== "config_assistant") return null;

    return (
        <>
            {/* Edge handle — visible whenever the drawer is closed so the
                user can re-open after dismissing. Hidden when open. */}
            {!open && (
                <button
                    type="button"
                    onClick={onToggle}
                    className="fixed right-0 top-1/2 -translate-y-1/2 z-30 flex items-center gap-1 px-2 py-3 rounded-l-md bg-white border border-r-0 border-slate-300 shadow-md hover:bg-slate-50 text-slate-700"
                    aria-label="Open Configuration Assistant"
                    title="Open inspect drawer"
                >
                    <ChevronsLeft size={14}/>
                    <span className="text-[10px] font-medium uppercase tracking-wider [writing-mode:vertical-rl] rotate-180">
                        Inspect
                    </span>
                </button>
            )}

            {/* Drawer surface */}
            <aside
                className={[
                    "fixed top-0 right-0 z-30 h-screen w-[480px] max-w-[92vw]",
                    "bg-white border-l border-slate-200 shadow-2xl",
                    "transition-transform duration-300 ease-out",
                    open ? "translate-x-0" : "translate-x-full",
                ].join(" ")}
                aria-hidden={!open}
            >
                <div className="flex flex-row items-center justify-between px-3 py-2 border-b border-slate-200 bg-slate-50">
                    <div className="flex items-center gap-2">
                        <span className="inline-block h-2 w-2 rounded-full bg-amber-500"/>
                        <span className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
                            Configuration Assistant
                        </span>
                    </div>
                    <div className="flex items-center gap-1">
                        <button
                            type="button"
                            onClick={onToggle}
                            className="p-1 rounded hover:bg-slate-200 text-slate-500"
                            title="Collapse"
                            aria-label="Collapse drawer"
                        >
                            <ChevronsRight size={14}/>
                        </button>
                        <button
                            type="button"
                            onClick={onClose}
                            className="p-1 rounded hover:bg-slate-200 text-slate-500"
                            title="Close (won't auto-reopen this turn)"
                            aria-label="Close drawer"
                        >
                            <X size={14}/>
                        </button>
                    </div>
                </div>
                <div className="flex flex-col h-[calc(100vh-40px)]">
                    <div className="flex-[3] min-h-[260px] border-b border-slate-200">
                        <GraphPane/>
                    </div>
                    <div className="flex-[2] min-h-[220px]">
                        <DetailsPane/>
                    </div>
                </div>
            </aside>
        </>
    );
}

export default InspectDrawer;
