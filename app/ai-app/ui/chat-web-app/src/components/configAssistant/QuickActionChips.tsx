/*
 * SPDX-License-Identifier: MIT
 * QuickActionChips — pre-canned prompt chips above the composer for the
 * Configuration Assistant. Tap fills the composer (focus + cursor at end);
 * Enter sends. Self-hides outside config-assistant mode.
 */
import {useCallback, useMemo} from "react";

import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {setUserMessage} from "../../features/chat/chatStateSlice.ts";
import {
    selectConfigAssistantMode,
    selectConfigAssistantSelection,
} from "../../features/configAssistant/configAssistantSlice.ts";

interface Chip {
    label: string;
    prompt: string;
}

const FRESH_CHIPS: ReadonlyArray<Chip> = [
    {
        label: "Scaffold a doc-RAG bundle",
        prompt:
            "Help me scaffold a kdcube bundle for my company that retrieves answers from internal docs and policies. Walk me through bundle anatomy, then give me concrete starter files (entrypoint, tools_descriptor, knowledge config).",
    },
    {
        label: "What's a Bundle?",
        prompt: "What is a Bundle in kdcube? Show me the canonical definition and the classes that realise it.",
    },
    {
        label: "Bundle anatomy",
        prompt: "Walk me through the anatomy of a kdcube bundle (entrypoint, tools, skills, knowledge space) using react.code as the example.",
    },
    {
        label: "Add a tool",
        prompt: "How do I add a custom tool to my bundle? Show me a minimal example of an SK kernel function with the right registration in tools_descriptor.py.",
    },
    {
        label: "Ingest documents",
        prompt: "How do I ingest company documents into the knowledge space so the bundle can retrieve them? Show me the KBClient/HybridSearchParams shape and an example bundle_props.knowledge block.",
    },
    {
        label: "Style policies",
        prompt: "Which style policies must I follow when writing my bundle? Show me the most important ones and how they apply to a typical entrypoint.",
    },
];

function QuickActionChips() {
    const dispatch = useAppDispatch();
    const mode = useAppSelector(selectConfigAssistantMode);
    const selection = useAppSelector(selectConfigAssistantSelection);

    const chips = useMemo<ReadonlyArray<Chip>>(() => {
        if (selection.kind !== "class" || !selection.qualifiedName) return FRESH_CHIPS;
        const qn = selection.qualifiedName;
        return [
            {label: "Show callers", prompt: `Show me the callers of ${qn}.`},
            {label: "Show tests", prompt: `Show me the tests covering ${qn}.`},
            {label: "Find docs", prompt: `Find the documentation for ${qn}.`},
            {label: "Generate similar", prompt: `Generate a tool similar to ${qn}, adapted for my doc-RAG bundle.`},
            {label: "What concepts?", prompt: `What concepts does ${qn} embody, and which style policies govern it?`},
        ];
    }, [selection.kind, selection.qualifiedName]);

    const onChipClick = useCallback(
        (prompt: string) => () => {
            dispatch(setUserMessage(prompt));
        },
        [dispatch],
    );

    if (!mode) return null;

    return (
        <div className="absolute left-1/2 -translate-x-1/2 bottom-[88px] z-10 max-w-[50vw] w-full px-8 pointer-events-none">
            <div className="flex flex-row flex-wrap gap-2 justify-center pointer-events-auto">
                {chips.map((chip) => (
                    <button
                        key={chip.label}
                        type="button"
                        onClick={onChipClick(chip.prompt)}
                        className="px-3 py-1.5 text-xs font-medium bg-white border border-slate-300 rounded-full text-slate-700 hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 transition-colors shadow-sm"
                        title={chip.prompt}
                    >
                        {chip.label}
                    </button>
                ))}
            </div>
        </div>
    );
}

export default QuickActionChips;
