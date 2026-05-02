import {useMemo} from "react";

import {useAppSelector} from "../../app/store.ts";
import {selectLatestTurn} from "../../features/chat/chatStateSlice.ts";
import {CODE_CORE_ARTIFACT_TYPE, CodeCoreArtifact} from "../../features/logExtensions/codeCore/types.ts";

/**
 * Returns the most recent CodeCoreArtifact for the given kinds in the
 * latest turn (in-progress or just-completed), or null if none yet.
 *
 * Reads from selectLatestTurn rather than selectCurrentTurn so that the
 * artifact stays visible after the turn completes (chat moves the turn
 * out of "inProgress" state at chat_complete, which otherwise would clear
 * the inspect drawer the moment the agent finishes answering).
 */
export function useCodeCoreArtifact(kinds: ReadonlyArray<string>): CodeCoreArtifact | null {
    const latestTurn = useAppSelector(selectLatestTurn);
    return useMemo(() => {
        if (!latestTurn) return null;
        const filtered = latestTurn.artifacts.filter(
            (a) => a.artifactType === CODE_CORE_ARTIFACT_TYPE,
        ) as CodeCoreArtifact[];
        const matching = filtered.filter((a) => kinds.includes(a.content.kind));
        if (!matching.length) return null;
        // Latest by timestamp.
        return matching.reduce((latest, cur) =>
            (cur.content.timestamp > latest.content.timestamp ? cur : latest),
        );
    }, [latestTurn, kinds]);
}
