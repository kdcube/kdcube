import { useEffect, useState } from "react";
import { RNFile, resolveResourceRn } from "../../../../features/chatController/chatBase.ts";
import { getResourceByRN, downloadBlob } from "../../../../app/api/utils.ts";

const useResourceObjectUrl = (rn: string): string | null => {
    const [url, setUrl] = useState<string | null>(null);
    useEffect(() => {
        if (!rn) return;
        let cancelled = false;
        let obj: string | null = null;
        (async () => {
            try {
                const resource = await getResourceByRN(rn);
                const downloadUrl = resource?.metadata?.download_url;
                if (!downloadUrl) return;
                const blob = await downloadBlob(downloadUrl);
                if (cancelled) return;
                obj = URL.createObjectURL(blob);
                setUrl(obj);
            } catch { /* leave url null; caller shows nothing */ }
        })();
        return () => { cancelled = true; if (obj) URL.revokeObjectURL(obj); };
    }, [rn]);
    return url;
};

const InlineImage = ({ item }: { item: RNFile }) => {
    const rn = resolveResourceRn(item);
    const url = useResourceObjectUrl(rn);
    if (!url) return null;
    return (
        <figure className="my-2">
            <a href={url} target="_blank" rel="noreferrer">
                <img src={url} alt={item.description || item.filename}
                     title={item.filename} loading="lazy"
                     className="rounded max-w-full h-auto border border-gray-200" />
            </a>
            <figcaption className="text-xs text-gray-500 mt-1">{item.filename}</figcaption>
        </figure>
    );
};

export const InlineImageItemsPanel = ({ items }: { items: RNFile[] }) => {
    if (!items || !items.length) return null;
    return (
        <div className="flex flex-col justify-start mt-2">
            {items.map((item, i) => <InlineImage key={resolveResourceRn(item) || i} item={item} />)}
        </div>
    );
};
