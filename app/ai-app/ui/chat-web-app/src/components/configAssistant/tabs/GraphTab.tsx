/*
 * SPDX-License-Identifier: MIT
 * GraphTab — interactive class+concept dependency graph powered by xyflow.
 *
 * v1: bootstraps with a static demo cluster (Bundle ecosystem) so the page
 * has visible content before the bundle starts emitting `code_core.graph`
 * artifacts. Once that wiring lands, this component will read nodes/edges
 * from configAssistantSlice instead of the demo constants.
 */
import {useCallback} from "react";
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

import {useAppDispatch} from "../../../app/store.ts";
import {setInspectSelection} from "../../../features/configAssistant/configAssistantSlice.ts";
import {TabFrame} from "./TabFrame.tsx";

type SemanticNodeData = {
    label: string;
    sub?: string;
    kind: "class" | "concept" | "policy";
    qualifiedName?: string;
    conceptId?: string;
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
        data: {label: "Bundle", sub: "concept", kind: "concept", conceptId: "bundle"},
        style: NODE_STYLE.concept,
    },
    {
        id: "concept:bundle_entrypoint",
        position: {x: 280, y: 40},
        data: {label: "Bundle Entrypoint", sub: "concept", kind: "concept", conceptId: "bundle_entrypoint"},
        style: NODE_STYLE.concept,
    },
    {
        id: "concept:knowledge_space",
        position: {x: 40, y: 200},
        data: {label: "Knowledge Space", sub: "concept", kind: "concept", conceptId: "knowledge_space"},
        style: NODE_STYLE.concept,
    },
    {
        id: "class:BaseEntrypoint",
        position: {x: 280, y: 200},
        data: {
            label: "BaseEntrypoint",
            sub: "class",
            kind: "class",
            qualifiedName: "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint.BaseEntrypoint",
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
            qualifiedName: "kdcube_ai_app.apps.chat.sdk.examples.bundles.react.code@2026_03_29.entrypoint.ReactCodeWorkflow",
        },
        style: NODE_STYLE.class,
    },
    {
        id: "policy:client_lifecycle",
        position: {x: 40, y: 360},
        data: {label: "Async Client Lifecycle", sub: "policy", kind: "policy", conceptId: "client_lifecycle"},
        style: NODE_STYLE.policy,
    },
    {
        id: "class:KBClient",
        position: {x: 280, y: 360},
        data: {
            label: "KBClient",
            sub: "class",
            kind: "class",
            qualifiedName: "kdcube_ai_app.apps.chat.sdk.retrieval.kb_client.KBClient",
        },
        style: NODE_STYLE.class,
    },
];

const DEMO_EDGES: Edge[] = [
    {
        id: "e:bundle-be",
        source: "concept:bundle",
        target: "concept:bundle_entrypoint",
        label: "RELATED_TO",
        style: {stroke: "#d97706"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:bundle-ks",
        source: "concept:bundle",
        target: "concept:knowledge_space",
        label: "RELATED_TO",
        style: {stroke: "#d97706"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:base-be",
        source: "class:BaseEntrypoint",
        target: "concept:bundle_entrypoint",
        label: "EMBODIES",
        animated: true,
        style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:reactcode-base",
        source: "class:ReactCodeWorkflow",
        target: "class:BaseEntrypoint",
        label: "INHERITS",
        style: {stroke: "#2563eb"},
        labelStyle: {fontSize: 10, fill: "#1e3a8a"},
    },
    {
        id: "e:reactcode-bundle",
        source: "class:ReactCodeWorkflow",
        target: "concept:bundle",
        label: "EMBODIES",
        animated: true,
        style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:kb-ks",
        source: "class:KBClient",
        target: "concept:knowledge_space",
        label: "EMBODIES",
        animated: true,
        style: {stroke: "#d97706", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#92400e"},
    },
    {
        id: "e:kb-policy",
        source: "class:KBClient",
        target: "policy:client_lifecycle",
        label: "GOVERNED_BY",
        style: {stroke: "#7c3aed", strokeDasharray: "6 4"},
        labelStyle: {fontSize: 10, fill: "#4c1d95"},
    },
];

function GraphTab() {
    const dispatch = useAppDispatch();
    const [nodes, , onNodesChange] = useNodesState<Node<SemanticNodeData>>(DEMO_NODES);
    const [edges, , onEdgesChange] = useEdgesState(DEMO_EDGES);

    const onNodeClick = useCallback<NodeMouseHandler<Node<SemanticNodeData>>>(
        (_evt, node) => {
            const data = node.data;
            if (data.kind === "class") {
                dispatch(setInspectSelection({qualifiedName: data.qualifiedName ?? null, tab: "footprint"}));
            } else {
                dispatch(setInspectSelection({conceptId: data.conceptId ?? null, tab: "concept"}));
            }
        },
        [dispatch],
    );

    return (
        <TabFrame title="Graph" subtitle="Class + concept dependency map (demo)">
            <div className="-mx-4 -my-3 h-[calc(100%+1.5rem)] w-[calc(100%+2rem)]">
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
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
        </TabFrame>
    );
}

export default GraphTab;
