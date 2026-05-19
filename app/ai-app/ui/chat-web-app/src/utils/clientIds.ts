const pad = (value: number, width = 2): string => String(value).padStart(width, "0");

export const createClientTurnId = (now: Date = new Date()): string => {
    return [
        `turn_${now.getUTCFullYear()}`,
        pad(now.getUTCMonth() + 1),
        pad(now.getUTCDate()),
        pad(now.getUTCHours()),
        pad(now.getUTCMinutes()),
        pad(now.getUTCSeconds()),
    ].join("-") + `-${pad(now.getUTCMilliseconds(), 3)}`;
};
