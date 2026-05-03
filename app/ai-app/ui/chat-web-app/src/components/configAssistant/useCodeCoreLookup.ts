/*
 * useCodeCoreLookup — fetches code-core data directly from the chat-proc
 * HTTP API on selection change, with a fallback to the latest matching
 * artifact in the current/just-completed turn (so artifacts emitted by the
 * agent still take priority when they're available).
 *
 * Implementation note: the cache key lives in a ref (not a useState
 * dependency) because including it in the effect deps would tear down
 * the in-flight fetch the moment we set the new key — its cleanup would
 * run with abort=true before the .then() resolved.
 */
import {useEffect, useRef, useState} from "react";

import {useCodeCoreArtifact} from "./useCodeCoreArtifact.ts";
import {ClassFootprintResponse, DefineResponse, fetchClassFootprint, fetchDefine} from "./codeCoreService.ts";

interface DefineState {
    loading: boolean;
    error: string | null;
    data: DefineResponse | null;
    /** True when the data came from a tool artifact (preferred) rather than a fresh fetch. */
    fromArtifact: boolean;
}

interface FootprintState {
    loading: boolean;
    error: string | null;
    data: ClassFootprintResponse | null;
    fromArtifact: boolean;
}

export function useDefineLookup(conceptId: string | null): DefineState {
    const artifact = useCodeCoreArtifact(["define"]);
    const cacheKey = useRef<string | null>(null);
    const [remote, setRemote] = useState<{
        loading: boolean;
        error: string | null;
        data: DefineResponse | null;
    }>({loading: false, error: null, data: null});

    // Best-match from the artifact (latest define call this turn).
    const artifactMatch = (() => {
        if (!artifact || !conceptId) return null;
        const payload = artifact.content.payload as DefineResponse | null;
        const matches = payload?.matches ?? [];
        const exact = matches.find((m) => m.id?.toLowerCase() === conceptId.toLowerCase());
        if (exact) return {matches: [exact]} as DefineResponse;
        return null;
    })();

    useEffect(() => {
        if (!conceptId) {
            cacheKey.current = null;
            setRemote({loading: false, error: null, data: null});
            return;
        }
        if (artifactMatch) {
            cacheKey.current = `artifact:${conceptId}`;
            setRemote({loading: false, error: null, data: artifactMatch});
            return;
        }
        const key = `define:${conceptId}`;
        if (cacheKey.current === key) return;
        cacheKey.current = key;
        setRemote({loading: true, error: null, data: null});
        fetchDefine(conceptId)
            .then((data) => {
                // Only commit if the user hasn't moved on. We deliberately do
                // NOT use an abort flag here: React 19 StrictMode double-mounts
                // effects, the first cleanup would otherwise drop the result.
                if (cacheKey.current !== key) return;
                setRemote({loading: false, error: null, data});
            })
            .catch((err: Error) => {
                if (cacheKey.current !== key) return;
                setRemote({loading: false, error: err.message, data: null});
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [conceptId, !!artifactMatch]);

    return {
        loading: remote.loading,
        error: remote.error,
        data: remote.data,
        fromArtifact: !!artifactMatch,
    };
}

export function useFootprintLookup(qualifiedName: string | null): FootprintState {
    const artifact = useCodeCoreArtifact(["class_footprint"]);
    const cacheKey = useRef<string | null>(null);
    const [remote, setRemote] = useState<{
        loading: boolean;
        error: string | null;
        data: ClassFootprintResponse | null;
    }>({loading: false, error: null, data: null});

    const artifactMatch = (() => {
        if (!artifact || !qualifiedName) return null;
        const payload = artifact.content.payload as ClassFootprintResponse | null;
        const fp = payload?.footprint?.[0];
        if (!fp) return null;
        if (fp.qualified_name !== qualifiedName) return null;
        return payload;
    })();

    useEffect(() => {
        if (!qualifiedName) {
            cacheKey.current = null;
            setRemote({loading: false, error: null, data: null});
            return;
        }
        if (artifactMatch) {
            cacheKey.current = `artifact:${qualifiedName}`;
            setRemote({loading: false, error: null, data: artifactMatch});
            return;
        }
        const key = `footprint:${qualifiedName}`;
        if (cacheKey.current === key) return;
        cacheKey.current = key;
        setRemote({loading: true, error: null, data: null});
        fetchClassFootprint(qualifiedName)
            .then((data) => {
                if (cacheKey.current !== key) return;
                setRemote({loading: false, error: null, data});
            })
            .catch((err: Error) => {
                if (cacheKey.current !== key) return;
                setRemote({loading: false, error: err.message, data: null});
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [qualifiedName, !!artifactMatch]);

    return {
        loading: remote.loading,
        error: remote.error,
        data: remote.data,
        fromArtifact: !!artifactMatch,
    };
}
