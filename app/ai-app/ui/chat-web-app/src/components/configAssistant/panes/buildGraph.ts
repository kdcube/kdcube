/*
 * Build xyflow nodes + edges from the current turn's code_core.* artifacts.
 *
 * Artifacts contribute as follows:
 *   code_core.define          -> central :Semantic node + related concept
 *                                neighbours + realized_by Class neighbours
 *   code_core.class_footprint -> central :Class node + embodied concept
 *                                neighbours + governing policy neighbours
 *                                + ancestor Class neighbours
 *
 * Layout: each artifact gets its own "row"; the focal node sits in the
 * middle of the row, neighbours fan out in a half-circle below it. Simple
 * but readable; works for the typical 1–3 artifacts per turn.
 */
import {Edge, Node} from "@xyflow/react";
import dagre from "dagre";

import {CodeCoreArtifact} from "../../../features/logExtensions/codeCore/types.ts";

export type SemanticNodeKind = "class" | "concept" | "policy";

export type SemanticNodeData = {
    label: string;
    sub?: string;
    kind: SemanticNodeKind;
    qualifiedName?: string;
    conceptId?: string;
    scope: "framework" | "my_bundle";
};

const STYLE: Record<SemanticNodeKind, React.CSSProperties> = {
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

const FRAMEWORK_BUNDLE_HINTS = ["framework"];

// Approximate render dimensions per kind; dagre uses these to space nodes.
// They don't have to be exact — just close enough to avoid overlap.
const NODE_WIDTH: Record<SemanticNodeKind, number> = {
    class: 200,
    concept: 180,
    policy: 200,
};
const NODE_HEIGHT: Record<SemanticNodeKind, number> = {
    class: 56,
    concept: 44,
    policy: 44,
};

interface MutableGraph {
    nodes: Map<string, Node<SemanticNodeData>>;
    edges: Map<string, Edge>;
}

const ensureNode = (
    g: MutableGraph,
    id: string,
    data: SemanticNodeData,
): void => {
    if (g.nodes.has(id)) return;
    g.nodes.set(id, {
        id,
        // Position is filled in by the dagre layout pass below.
        position: {x: 0, y: 0},
        data,
        style: STYLE[data.kind],
    });
};

const ensureEdge = (
    g: MutableGraph,
    id: string,
    source: string,
    target: string,
    label: string,
    style: "embodies" | "governed_by" | "related" | "inherits" | "realized_by",
): void => {
    if (g.edges.has(id)) return;
    const stylePresets: Record<typeof style, {edge: React.CSSProperties; animated: boolean}> = {
        embodies: {
            edge: {stroke: "#d97706", strokeDasharray: "6 4"},
            animated: true,
        },
        governed_by: {
            edge: {stroke: "#7c3aed", strokeDasharray: "6 4"},
            animated: false,
        },
        related: {
            edge: {stroke: "#d97706"},
            animated: false,
        },
        inherits: {
            edge: {stroke: "#2563eb"},
            animated: false,
        },
        realized_by: {
            edge: {stroke: "#d97706", strokeDasharray: "6 4"},
            animated: true,
        },
    };
    const preset = stylePresets[style];
    g.edges.set(id, {
        id,
        source,
        target,
        label,
        animated: preset.animated,
        style: preset.edge,
        labelStyle: {fontSize: 10, fill: "#475569"},
    });
};

const shortName = (qn: string): string => {
    if (!qn) return qn;
    const parts = qn.split(".");
    return parts[parts.length - 1] || qn;
};

const ingestDefine = (
    g: MutableGraph,
    payload: Record<string, unknown> | null | undefined,
): void => {
    if (!payload || !Array.isArray(payload.matches)) return;
    const match = (payload.matches as Array<Record<string, unknown>>)[0];
    if (!match) return;

    const id = `concept:${match.id}`;
    const isPolicy = match.kind === "policy";
    const focalKind: SemanticNodeKind = isPolicy ? "policy" : "concept";

    ensureNode(g, id, {
        label: String(match.name ?? match.id ?? "Concept"),
        sub: isPolicy ? "policy" : "concept",
        kind: focalKind,
        conceptId: String(match.id ?? ""),
        scope: FRAMEWORK_BUNDLE_HINTS.includes(String(match.scope ?? "framework"))
            ? "framework"
            : "my_bundle",
    });

    const related = Array.isArray(match.related) ? (match.related as Array<Record<string, unknown>>) : [];
    const realized = Array.isArray(match.realized_by) ? (match.realized_by as string[]) : [];
    const applied = Array.isArray(match.applied_to) ? (match.applied_to as string[]) : [];

    for (const rel of related) {
        if (!rel.id) continue;
        const relId = `concept:${rel.id}`;
        ensureNode(g, relId, {
            label: String(rel.name ?? rel.id ?? "Concept"),
            sub: rel.kind === "policy" ? "policy" : "concept",
            kind: rel.kind === "policy" ? "policy" : "concept",
            conceptId: String(rel.id ?? ""),
            scope: "framework",
        });
        ensureEdge(g, `${id}->${relId}:related`, id, relId, "RELATED_TO", "related");
    }

    for (const qn of realized) {
        if (!qn) continue;
        const cid = `class:${qn}`;
        ensureNode(g, cid, {
            label: shortName(qn),
            sub: "class",
            kind: "class",
            qualifiedName: qn,
            scope: "framework",
        });
        ensureEdge(g, `${id}->${cid}:realized_by`, id, cid, "REALIZED_BY", "realized_by");
    }

    for (const qn of applied) {
        if (!qn) continue;
        const cid = `class:${qn}`;
        ensureNode(g, cid, {
            label: shortName(qn),
            sub: "class",
            kind: "class",
            qualifiedName: qn,
            scope: "framework",
        });
        // For policies, the inverse edge is "governed_by" from class -> policy.
        ensureEdge(g, `${cid}->${id}:governed_by`, cid, id, "GOVERNED_BY", "governed_by");
    }
};

const ingestClassFootprint = (
    g: MutableGraph,
    payload: Record<string, unknown> | null | undefined,
): void => {
    if (!payload || !Array.isArray(payload.footprint)) return;
    const fp = (payload.footprint as Array<Record<string, unknown>>)[0];
    if (!fp || !fp.qualified_name) return;
    const qn = String(fp.qualified_name);
    const id = `class:${qn}`;

    ensureNode(g, id, {
        label: String(fp.name ?? shortName(qn)),
        sub: "class",
        kind: "class",
        qualifiedName: qn,
        scope: "framework",
    });

    const concepts = Array.isArray(payload.concepts) ? (payload.concepts as Array<Record<string, unknown>>) : [];
    const policies = Array.isArray(payload.style_policies)
        ? (payload.style_policies as Array<Record<string, unknown>>)
        : [];
    const ancestors = Array.isArray(fp.ancestors)
        ? ((fp.ancestors as string[]).filter((s) => !!s))
        : [];

    for (const c of concepts) {
        if (!c.id) continue;
        const cid = `concept:${c.id}`;
        ensureNode(g, cid, {
            label: String(c.name ?? c.id ?? "Concept"),
            sub: "concept",
            kind: "concept",
            conceptId: String(c.id ?? ""),
            scope: "framework",
        });
        ensureEdge(g, `${id}->${cid}:embodies`, id, cid, "EMBODIES", "embodies");
    }

    for (const p of policies) {
        if (!p.id) continue;
        const pid = `policy:${p.id}`;
        ensureNode(g, pid, {
            label: String(p.name ?? p.id ?? "Policy"),
            sub: "policy",
            kind: "policy",
            conceptId: String(p.id ?? ""),
            scope: "framework",
        });
        ensureEdge(g, `${id}->${pid}:governed_by`, id, pid, "GOVERNED_BY", "governed_by");
    }

    for (const ancestorQn of ancestors) {
        const aid = `class:${ancestorQn}`;
        ensureNode(g, aid, {
            label: shortName(ancestorQn),
            sub: "class",
            kind: "class",
            qualifiedName: ancestorQn,
            scope: "framework",
        });
        ensureEdge(g, `${id}->${aid}:inherits`, id, aid, "INHERITS", "inherits");
    }
};

export interface BuiltGraph {
    nodes: Node<SemanticNodeData>[];
    edges: Edge[];
}

/**
 * Run a dagre top-down layout over the collected nodes/edges so the graph
 * looks like a real network instead of hand-positioned blobs.
 */
const layoutWithDagre = (
    nodes: Node<SemanticNodeData>[],
    edges: Edge[],
): Node<SemanticNodeData>[] => {
    if (!nodes.length) return nodes;
    const dg = new dagre.graphlib.Graph();
    dg.setGraph({rankdir: "TB", nodesep: 38, ranksep: 70, marginx: 20, marginy: 20});
    dg.setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
        dg.setNode(n.id, {
            width: NODE_WIDTH[n.data.kind],
            height: NODE_HEIGHT[n.data.kind],
        });
    }
    for (const e of edges) {
        dg.setEdge(e.source, e.target);
    }

    dagre.layout(dg);

    return nodes.map((n) => {
        const meta = dg.node(n.id);
        if (!meta) return n;
        return {
            ...n,
            // dagre returns the centre — xyflow expects the top-left of the node.
            position: {
                x: meta.x - NODE_WIDTH[n.data.kind] / 2,
                y: meta.y - NODE_HEIGHT[n.data.kind] / 2,
            },
        };
    });
};

export function buildGraphFromArtifacts(
    artifacts: ReadonlyArray<CodeCoreArtifact>,
): BuiltGraph {
    const g: MutableGraph = {
        nodes: new Map(),
        edges: new Map(),
    };

    for (const a of artifacts) {
        const payload = a.content.payload as Record<string, unknown> | null;
        switch (a.content.kind) {
            case "define":
                ingestDefine(g, payload);
                break;
            case "class_footprint":
                ingestClassFootprint(g, payload);
                break;
            // Other kinds (code_search, find_references, …) — TODO.
            default:
                break;
        }
    }

    const rawNodes = Array.from(g.nodes.values());
    const edges = Array.from(g.edges.values());
    return {
        nodes: layoutWithDagre(rawNodes, edges),
        edges,
    };
}

export const NODE_STYLE = STYLE;
