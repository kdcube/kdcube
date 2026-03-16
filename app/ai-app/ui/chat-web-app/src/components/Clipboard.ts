function fallbackCopy(text: string) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();

    const success = document.execCommand('copy');
    document.body.removeChild(textarea);
    return success;
}

async function copyMarkdownToClipboard(text?: string, html?: string) {
    // Try modern API first
    if (navigator.clipboard && window.isSecureContext) {
        try {
            const clipboardItemData: Record<string, string> = {};
            if (text) {
                clipboardItemData["text/plain"] = text;
            }
            if (html) {
                clipboardItemData["text/html"] = html;
            }
            const clipboardItem = [new ClipboardItem(clipboardItemData)]
            await navigator.clipboard.write(clipboardItem)
            return true;
        } catch (error) {
            console.error('Modern clipboard failed:', error);
        }
    }

    if (text) {
        return fallbackCopy(text.toString());
    } else {
        return false
    }
}

async function saveStringAsFile(content: string, filename: string) {
    const blob = new Blob([content], {type: "text/plain"});
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();

    URL.revokeObjectURL(url);
}

export {copyMarkdownToClipboard, saveStringAsFile}