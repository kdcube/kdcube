export type ClientTimezone = {
    tz?: string;
    utcOffsetMin: number;
};

export const getClientTimezone = (): ClientTimezone => {
    let tz: string | undefined;
    try {
        tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

    } catch {
        tz = undefined;
    }

    const utcOffsetMin = -new Date().getTimezoneOffset();

    return {tz, utcOffsetMin};
};

export const formatDateToLocalString = (date: Date, timeOnlyForToday: boolean = false, appendTime: boolean = true, maxDaysAgo: number = 7) => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    // Calculate the difference in days
    const diffTime = today.getTime() - targetDate.getTime();
    const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));

    // Format the time portion

    const timeString = date.toLocaleTimeString(Intl.DateTimeFormat().resolvedOptions().locale, {
        hour: 'numeric',
        minute: '2-digit',
        hour12: false
    });

    const appendedTimeString = appendTime ? '' : " " + timeString;

    if (diffDays === 0) {
        if (timeOnlyForToday) {
            return timeString;
        }
        return `Today${appendedTimeString}`;
    }

    if (diffDays === 1) {
        return `Yesterday${appendedTimeString}`;
    } else if (diffDays > 1 && diffDays <= maxDaysAgo) {
        return `${diffDays} days ago${appendedTimeString}`;
    } else {
        return `${date.toLocaleDateString()}${appendedTimeString}`;
    }
}
