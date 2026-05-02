/*
 * SPDX-License-Identifier: MIT
 * GraphPane — top pane of the Configuration Assistant inspect column.
 * Reactive: rebuilds nodes/edges whenever a new code_core.* artifact lands
 * in the current turn (define / class_footprint payloads). Falls back to a
 * small demo cluster when no artifacts have arrived yet so the pane has
 * visible content.
 */
import {useCallback, useMemo} from "react";
import {
    Background,
    BackgroundVariant,
    Controls,
    Edge,
    Node,
    ReactFlow,
    type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {selectCurrentTurn} from "../../../features/chat/chatStateSlice.ts";
import {
    ScopeFilter,
    selectClass,
    selectConcept,
    selectConfigAssistantScope,
    selectConfigAssistantSelection,
    setScopeFilter,
} from "../../../features/configAssistant/configAssistantSlice.ts";
import {
    CODE_CORE_ARTIFACT_TYPE,
    CodeCoreArtifact,
} from "../../../features/logExtensions/codeCore/types.ts";
import {
    BuiltGraph,
    NODE_STYLE,
    SemanticNodeData,
    buildGraphFromArtifacts,
} from "./buildGraph.ts";

const DEMO: BuiltGraph = {
    nodes: [
        {
            id: "concept:bundle",
            position: {x: 40, y: 40},
            data: {label: "Bundle", sub: "concept", kind: "concept", conceptId: "bundle", scope: "framework"},
            style: NODE_STYLE.concept,
        },
        {
            id: "concept:bundle_entrypoint",
            position: {x: 280, y: 40},
            data: {label: "Bundle Entrypoint", sub: "concept", kind: "concept", conceptId: "bundle_entrypoint", scope: "framework"},
            style: NODE_STYLE.concept,
        },
        {
            id: "concept:knowledge_space",
            position: {x: 40, y: 200},
            data: {label: "Knowledge Space", sub: "concept", kind: "concept", conceptId: "knowledge_space", scope: "framework"},
            style: NODE_STYLE.concept,
        },
        {
            id: "class:BaseEntrypoint",
            position: {x: 280, y: 200},
            data: {
                label: "BaseEntrypoint",
                sub: "class · demo",
                kind: "class",
                scope: "framework",
                qualifiedName: "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint.BaseEntrypoint",
            },
            style: NODE_STYLE.class,
        },
    ],
    edges: [
        {
            id: "demo:e1",
            source: "concept:bundle",
            target: "concept:bundle_entrypoint",
            label: "RELATED_TO",
            style: {stroke: "#d97706"},
            labelStyle: {fontSize: 10, fill: "#92400e"},
        },
        {
            id: "demo:e2",
            source: "class:BaseEntrypoint",
            target: "concept:bundle_entrypoint",
            label: "EMBODIES",
            animated: true,
            style: {stroke: "#d97706", strokeDasharray: "6 4"},
            labelStyle: {fontSize: 10, fill: "#92400e"},
        },
    ],
};

const FILTERS: ReadonlyArray<{id: ScopeFilter; label: string}> = [
    {id: "all", label: "All"},
    {id: "framework", label: "Framework"},
    {id: "my_bundle", label: "My bundle"},
];

function GraphPane() {
    const dispatch = useAppDispatch();
    const scope = useAppSelector(selectConfigAssistantScope);
    const selection = useAppSelector(selectConfigAssistantSelection);
    const currentTurn = useAppSelector(selectCurrentTurn);

    // Build the live graph from the current turn's code_core.* artifacts.
    // Empty -> show the small demo cluster as a friendly default.
    const live = useMemo<BuiltGraph>(() => {
        const artifacts = (currentTurn?.artifacts ?? []).filter(
            (a): a is CodeCoreArtifact => a.artifactType === CODE_CORE_ARTIFACT_TYPE,
        );
        if (!artifacts.length) return DEMO;
        return buildGraphFromArtifacts(artifacts);
    }, [currentTurn]);

    // Apply scope filter + highlight the current selection.
    const visible = useMemo(() => {
        const selectedId = selection.kind === "class"
            ? `class:${selection.qualifiedName}`
            : selection.kind === "concept" || selection.kind === "policy"
                ? `concept:${selection.conceptId}`
                : null;

        const nodes = live.nodes
            .filter((n) => scope.scopeFilter === "all" || n.data.scope === scope.scopeFilter)
            .map((n): Node<SemanticNodeData> => {
                if (n.id === selectedId) {
                    return {
                        ...n,
                        style: {
                            ...(n.style ?? {}),
                            boxShadow: "0 0 0 3px rgba(37, 99, 235, 0.45)",
                            borderColor: "#1e3a8a",
                        },
                    };
                }
                // Also try matching by conceptId or qualifiedName, in case
                // the selected node id format differs from how we encoded it.
                if (
                    selection.kind === "concept" || selection.kind === "policy"
                ) {
                    if (n.data.conceptId === selection.conceptId) {
                        return {
                            ...n,
                            style: {
                                ...(n.style ?? {}),
                                boxShadow: "0 0 0 3px rgba(37, 99, 235, 0.45)",
                            },
                        };
                    }
                }
                if (selection.kind === "class" && n.data.qualifiedName === selection.qualifiedName) {
                    return {
                        ...n,
                        style: {
                            ...(n.style ?? {}),
                            boxShadow: "0 0 0 3px rgba(37, 99, 235, 0.45)",
                        },
                    };
                }
                return n;
            });

        const visibleIds = new Set(nodes.map((n) => n.id));
        const edges: Edge[] = live.edges.filter(
            (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
        );
        return {nodes, edges};
    }, [live, scope.scopeFilter, selection]);

    const onNodeClick = useCallback<NodeMouseHandler<Node<SemanticNodeData>>>(
        (_evt, node) => {
            const data = node.data;
            if (data.kind === "class") {
                dispatch(selectClass(data.qualifiedName ?? null));
            } else {
                dispatch(
                    selectConcept({
                        conceptId: data.conceptId ?? null,
                        isPolicy: data.kind === "policy",
                    }),
                );
            }
        },
        [dispatch],
    );

    const onFilterClick = useCallback(
        (id: ScopeFilter) => () => {
            dispatch(setScopeFilter(id));
        },
        [dispatch],
    );

    const isLive = live !== DEMO;

    return (
        <div className="flex flex-col h-full">
            <div className="flex flex-row items-center justify-between gap-2 px-3 py-2 border-b border-slate-200 bg-slate-50">
                <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-700">Graph</span>
                    {isLive ? (
                        <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                            live
                        </span>
                    ) : (
                        <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-200 text-slate-600">
                            demo
                        </span>
                    )}
                </div>
                <div className="flex flex-row gap-1">
                    {FILTERS.map((f) => (
                        <button
                            key={f.id}
                            type="button"
                            onClick={onFilterClick(f.id)}
                            className={[
                                "text-[10px] px-2 py-0.5 rounded-full border transition-colors",
                                scope.scopeFilter === f.id
                                    ? "bg-blue-100 border-blue-400 text-blue-800"
                                    : "bg-white border-slate-300 text-slate-600 hover:border-blue-300 hover:text-blue-600",
                            ].join(" ")}
                        >
                            {f.label}
                        </button>
                    ))}
                </div>
            </div>
            <div className="flex-1 min-h-0">
                <ReactFlow
                    nodes={visible.nodes}
                    edges={visible.edges}
                    onNodeClick={onNodeClick}
                    fitView
                    fitViewOptions={{padding: 0.2}}
                    proOptions={{hideAttribution: true}}
                    panOnScroll
                    zoomOnScroll
                    nodesDraggable={false}
                    nodesConnectable={false}
                >
                    <Background variant={BackgroundVariant.Dots} gap={16} size={1}/>
                    <Controls showInteractive={false}/>
                </ReactFlow>
            </div>
        </div>
    );
}

export default GraphPane;
