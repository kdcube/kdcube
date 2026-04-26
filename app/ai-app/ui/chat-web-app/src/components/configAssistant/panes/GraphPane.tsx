/*
 * SPDX-License-Identifier: MIT
 * GraphPane — top pane of the Configuration Assistant inspect column.
 * Always visible. Click a node to drive the DetailsPane below.
 *
 * v1 ships with a static demo cluster (Bundle ecosystem) so the page has
 * visible content before the bundle starts emitting code_core.graph
 * artifacts. Future iterations will read nodes/edges from the latest
 * code_core.* artifacts in the current turn.
 */
import {useCallback, useMemo} from "react";
import {
    Background,
    BackgroundVariant,
    Controls,
    Edge,
    Node,
    ReactFlow,
    useEdgesState,
    useNodesState,
    type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {useAppDispatch, useAppSelector} from "../../../app/store.ts";
import {
    ScopeFilter,
    selectClass,
    selectConcept,
    selectConfigAssistantScope,
    setScopeFilter,
} from "../../../features/configAssistant/configAssistantSlice.ts";

type GraphScope = "framework" | "my_bundle";

type SemanticNodeData = {
    label: string;
    sub?: string;
    kind: "class" | "concept" | "policy";
    qualifiedName?: string;
    conceptId?: string;
    scope: GraphScope;
};

const NODE_STYLE: Record<SemanticNodeData["kind"], React.CSSProperties> = {
    class: {
        background: "#dbeafe",
        border: "1px solid #2563eb",
        color: "#1e3a8a",
        borderRadius: 8,
        padding: 10,
        fontSize: 12,
        minWidth: 160,
    },
    concept: {
        background: "#fef3c7",
        border: "1px dashed #d97706",
        color: "#78350f",
        borderRadius: 999,
        padding: "8px 14px",
        fontSize: 12,
        fontStyle: "italic",
        minWidth: 140,
    },
    policy: {
        background: "#ede9fe",
        border: "1px dashed #7c3aed",
        color: "#4c1d95",
        borderRadius: 999,
        padding: "8px 14px",
        fontSize: 12,
        fontStyle: "italic",
        minWidth: 140,
    },
};

const DEMO_NODES: Node<SemanticNodeData>[] = [
    {
        id: "concept:bundle",
        position: {x: 40, y: 40},
        data: {label: "Bundle", sub: "concept", kind: "concept", conceptId: "bundle", scope: "framework"},
        style: NODE_STYLE.concept,
    },
    {
        id: "concept:bundle_entrypoint",
        position: {x: 280, y: 40},
        data: {
            label: "Bundle Entrypoint", sub: "concept", kind: "concept",
            conceptId: "bundle_entrypoint", scope: "framework",
        },
        style: NODE_STYLE.concept,
    },
    {
        id: "concept:knowledge_space",
        position: {x: 40, y: 200},
        data: {
            label: "Knowledge Space", sub: "concept", kind: "concept",
            conceptId: "knowledge_space", scope: "framework",
        },
        style: NODE_STYLE.concept,
    },
    {
        id: "class:BaseEntrypoint",
        position: {x: 280, y: 200},
        data: {
            label: "BaseEntrypoint",
            sub: "class",
            kind: "class",
            scope: "framework",
            qualifiedName:
                "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint.BaseEntrypoint",
        },
        style: NODE_STYLE.class,
    },
    {
        id: "class:ReactCodeWorkflow",
        position: {x: 540, y: 200},
        data: {
            label: "ReactCodeWorkflow",
            sub: "class · react.code",
            kind: "class",
            scope: "my_bundle",
            qualifiedName:
                "kdcube_ai_app.apps.chat.sdk.examples.bundles.react.code@2026_03_29.entrypoint.ReactCodeWorkflow",
        },
        style: NODE_STYLE.class,
    },
    {
        id: "policy:client_lifecycle",
        position: {x: 40, y: 360},
        data: {
            label: "Async Client Lifecycle",
            sub: "policy",
            kind: "policy",
            conceptId: "client_lifecycle",
            scope: "framework",
        },
        style: NODE_STYLE.policy,
    },
    {
        id: "class:KBClient",
        position: {x: 280, y: 360},
        data: {
            label: "KBClient",
            sub: "class",
            kind: "class",
            scope: "framework",
            qualifiedName: "kdcube_ai_app.apps.chat.sdk.retrieval.kb_client.KBClient",
        },
        style: NODE_STYLE.class,
    },
];

const DEMO_EDGES: Edge[] = [
    {
        id: "e:bundle-be", source: "concept:bundle", target: "concept:bundle_entrypoint",
        label: "RELATED_TO", style: {stroke: "#d97706"}, labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:bundle-ks", source: "concept:bundle", target: "concept:knowledge_space",
        label: "RELATED_TO", style: {stroke: "#d97706"}, labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:base-be", source: "class:BaseEntrypoint", target: "concept:bundle_entrypoint",
        label: "EMBODIES", animated: true, style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:reactcode-base", source: "class:ReactCodeWorkflow", target: "class:BaseEntrypoint",
        label: "INHERITS", style: {stroke: "#2563eb"}, labelStyle: {fontSize: 10, fill: "#1e3a8a"},
    },
    {
        id: "e:reactcode-bundle", source: "class:ReactCodeWorkflow", target: "concept:bundle",
        label: "EMBODIES", animated: true, style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:kb-ks", source: "class:KBClient", target: "concept:knowledge_space",
        label: "EMBODIES", animated: true, style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:kb-policy", source: "class:KBClient", target: "policy:client_lifecycle",
        label: "GOVERNED_BY", style: {stroke: "#7c3aed", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#4c1d95"},
    },
];

const FILTERS: ReadonlyArray<{id: ScopeFilter; label: string}> = [
    {id: "all", label: "All"},
    {id: "framework", label: "Framework"},
    {id: "my_bundle", label: "My bundle"},
];

function GraphPane() {
    const dispatch = useAppDispatch();
    const scope = useAppSelector(selectConfigAssistantScope);

    const [allNodes, , onNodesChange] = useNodesState<Node<SemanticNodeData>>(DEMO_NODES);
    const [allEdges, , onEdgesChange] = useEdgesState(DEMO_EDGES);

    const visibleNodes = useMemo(() => {
        if (scope.scopeFilter === "all") return allNodes;
        return allNodes.filter((n) => n.data.scope === scope.scopeFilter);
    }, [allNodes, scope.scopeFilter]);

    const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

    const visibleEdges = useMemo(() => {
        return allEdges.filter(
            (e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target),
        );
    }, [allEdges, visibleNodeIds]);

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

    return (
        <div className="flex flex-col h-full">
            <div className="flex flex-row items-center justify-between gap-2 px-3 py-2 border-b border-slate-200 bg-slate-50">
                <div className="text-xs font-semibold text-slate-700">Graph</div>
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
                    nodes={visibleNodes}
                    edges={visibleEdges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onNodeClick={onNodeClick}
                    fitView
                    fitViewOptions={{padding: 0.2}}
                    proOptions={{hideAttribution: true}}
                    panOnScroll
                    zoomOnScroll
                >
                    <Background variant={BackgroundVariant.Dots} gap={16} size={1}/>
                    <Controls showInteractive={false}/>
                </ReactFlow>
            </div>
        </div>
    );
}

export default GraphPane;
