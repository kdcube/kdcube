import {Indexed, Timestamped} from "../types/common.ts";
import sha256 from 'crypto-js/sha256';

export function makeSerializable<T extends object>(obj: T): Partial<T> {
    const serializable: Record<string, unknown> = {};

    for (const key in obj) {
        if (Object.hasOwn(obj, key)) {
            const value = obj[key];
            const type = typeof value;

            // Include primitives and null
            if (
                type === 'string' ||
                type === 'number' ||
                type === 'boolean' ||
                value === null
            ) {
                serializable[key] = value;
            }
            // Handle arrays
            else if (Array.isArray(value)) {
                serializable[key] = value.map(item =>
                    typeof item === 'object' && item !== null
                        ? makeSerializable(item)
                        : item
                );
            }
            // Handle nested objects
            else if (type === 'object') {
                serializable[key] = makeSerializable(value as object);
            }
        }
    }

    return serializable as Partial<T>;
}

export function sortIndexed<T extends Indexed>(arr: T[], copy: boolean = false) {
    const result = copy ? arr.concat() : arr;
    arr.sort((a, b) => a.index - b.index);
    return result;
}

export function timeSortPredicate(a: number | null | undefined, b: number | null | undefined) {
    if ((a === null || a === undefined) && (b === null || b === undefined)) {
        return 0;
    }
    if (a === null || a === undefined) {
        return 1;
    }
    if (b === null || b === undefined) {
        return -1;
    }
    return a - b;
}

export function dateSortPredicate(a: Date | null, b: Date | null) {
    return timeSortPredicate(a?.getTime(), b?.getTime());
}

export function sortTimestamped<T extends Timestamped>(arr: T[], copy: boolean = false) {
    const result = copy ? arr.concat() : arr;
    arr.sort((a, b) => timeSortPredicate(a.timestamp, b.timestamp));
    return result;
}

export function sha(message: string) {
    const hashArray = Array.from(new Uint8Array(sha256(message).words));
    return hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
}

export function clamp(val: number, min?: number, max?: number) {
    if (min !== undefined) {
        val = Math.max(min, val);
    }
    if (max !== undefined) {
        val = Math.min(max, val);
    }
    return val;
}