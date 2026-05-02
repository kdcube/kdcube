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

const ROW_HEIGHT = 220;
const NEIGHBOUR_RADIUS = 200;
const FOCAL_X = 360;
const FRAMEWORK_BUNDLE_HINTS = ["framework"];

interface MutableGraph {
    nodes: Map<string, Node<SemanticNodeData>>;
    edges: Map<string, Edge>;
    rowIdx: number;
}

const ensureNode = (
    g: MutableGraph,
    id: string,
    data: SemanticNodeData,
    pos: {x: number; y: number},
): void => {
    if (g.nodes.has(id)) return;
    g.nodes.set(id, {
        id,
        position: pos,
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

const fanPosition = (rowIdx: number, slot: number, total: number): {x: number; y: number} => {
    if (total <= 0) return {x: FOCAL_X, y: rowIdx * ROW_HEIGHT};
    // Half-circle fan from -60° to +60° (relative to vertical down) below the focal node.
    const start = -Math.PI / 3;
    const end = Math.PI / 3;
    const t = total === 1 ? 0.5 : slot / (total - 1);
    const angle = start + (end - start) * t;
    const x = FOCAL_X + Math.sin(angle) * NEIGHBOUR_RADIUS;
    const y = rowIdx * ROW_HEIGHT + 130 + Math.cos(angle) * NEIGHBOUR_RADIUS * 0.6;
    return {x, y};
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
    const rowIdx = g.rowIdx++;

    ensureNode(
        g,
        id,
        {
            label: String(match.name ?? match.id ?? "Concept"),
            sub: isPolicy ? "policy" : "concept",
            kind: focalKind,
            conceptId: String(match.id ?? ""),
            scope: FRAMEWORK_BUNDLE_HINTS.includes(String(match.scope ?? "framework"))
                ? "framework"
                : "my_bundle",
        },
        {x: FOCAL_X, y: rowIdx * ROW_HEIGHT},
    );

    const related = Array.isArray(match.related) ? (match.related as Array<Record<string, unknown>>) : [];
    const realized = Array.isArray(match.realized_by) ? (match.realized_by as string[]) : [];
    const applied = Array.isArray(match.applied_to) ? (match.applied_to as string[]) : [];
    const neighbours = related.length + realized.length + applied.length;
    let slot = 0;

    for (const rel of related) {
        if (!rel.id) continue;
        const relId = `concept:${rel.id}`;
        ensureNode(
            g,
            relId,
            {
                label: String(rel.name ?? rel.id ?? "Concept"),
                sub: rel.kind === "policy" ? "policy" : "concept",
                kind: rel.kind === "policy" ? "policy" : "concept",
                conceptId: String(rel.id ?? ""),
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
        ensureEdge(g, `${id}->${relId}:related`, id, relId, "RELATED_TO", "related");
    }

    for (const qn of realized) {
        if (!qn) continue;
        const cid = `class:${qn}`;
        ensureNode(
            g,
            cid,
            {
                label: shortName(qn),
                sub: "class",
                kind: "class",
                qualifiedName: qn,
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
        ensureEdge(g, `${id}->${cid}:realized_by`, id, cid, "REALIZED_BY", "realized_by");
    }

    for (const qn of applied) {
        if (!qn) continue;
        const cid = `class:${qn}`;
        ensureNode(
            g,
            cid,
            {
                label: shortName(qn),
                sub: "class",
                kind: "class",
                qualifiedName: qn,
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
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
    const rowIdx = g.rowIdx++;

    ensureNode(
        g,
        id,
        {
            label: String(fp.name ?? shortName(qn)),
            sub: "class",
            kind: "class",
            qualifiedName: qn,
            scope: "framework",
        },
        {x: FOCAL_X, y: rowIdx * ROW_HEIGHT},
    );

    const concepts = Array.isArray(payload.concepts) ? (payload.concepts as Array<Record<string, unknown>>) : [];
    const policies = Array.isArray(payload.style_policies)
        ? (payload.style_policies as Array<Record<string, unknown>>)
        : [];
    const ancestors = Array.isArray(fp.ancestors)
        ? ((fp.ancestors as string[]).filter((s) => !!s))
        : [];
    const neighbours = concepts.length + policies.length + ancestors.length;
    let slot = 0;

    for (const c of concepts) {
        if (!c.id) continue;
        const cid = `concept:${c.id}`;
        ensureNode(
            g,
            cid,
            {
                label: String(c.name ?? c.id ?? "Concept"),
                sub: "concept",
                kind: "concept",
                conceptId: String(c.id ?? ""),
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
        ensureEdge(g, `${id}->${cid}:embodies`, id, cid, "EMBODIES", "embodies");
    }

    for (const p of policies) {
        if (!p.id) continue;
        const pid = `policy:${p.id}`;
        ensureNode(
            g,
            pid,
            {
                label: String(p.name ?? p.id ?? "Policy"),
                sub: "policy",
                kind: "policy",
                conceptId: String(p.id ?? ""),
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
        ensureEdge(g, `${id}->${pid}:governed_by`, id, pid, "GOVERNED_BY", "governed_by");
    }

    for (const ancestorQn of ancestors) {
        const aid = `class:${ancestorQn}`;
        ensureNode(
            g,
            aid,
            {
                label: shortName(ancestorQn),
                sub: "class",
                kind: "class",
                qualifiedName: ancestorQn,
                scope: "framework",
            },
            fanPosition(rowIdx, slot++, neighbours),
        );
        ensureEdge(g, `${id}->${aid}:inherits`, id, aid, "INHERITS", "inherits");
    }
};

export interface BuiltGraph {
    nodes: Node<SemanticNodeData>[];
    edges: Edge[];
}

export function buildGraphFromArtifacts(
    artifacts: ReadonlyArray<CodeCoreArtifact>,
): BuiltGraph {
    const g: MutableGraph = {
        nodes: new Map(),
        edges: new Map(),
        rowIdx: 0,
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

    return {
        nodes: Array.from(g.nodes.values()),
        edges: Array.from(g.edges.values()),
    };
}

export const NODE_STYLE = STYLE;
