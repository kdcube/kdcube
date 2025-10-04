/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

import {useEffect, useState} from "react";

export function useWordStreamEffect(text: string, delay = 100, maxDelta = 3) {
    const [displayedText, setDisplayedText] = useState('');
    const [wordIndex, setWordIndex] = useState(0);

    useEffect(() => {
        const words = text.split(' ');
        if (wordIndex < words.length) {
            const delta = Math.min(words.length - wordIndex - 1, maxDelta);
            const timeout = setTimeout(() => {
                setDisplayedText(words.slice(0, wordIndex + 1).join(' '));
                setWordIndex(prev => prev + delta);
            }, delay);

            return () => clearTimeout(timeout);
        }
    }, [text, wordIndex, delay, maxDelta]);

    return displayedText;
}