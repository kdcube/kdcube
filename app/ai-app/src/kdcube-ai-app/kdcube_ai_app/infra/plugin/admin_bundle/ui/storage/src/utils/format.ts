export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unit]}`;
}

export function formatDate(epoch: number | null | undefined): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleString();
}

export function parentPath(path: string): string {
  const cleaned = path.replace(/^\/+|\/+$/g, '');
  if (!cleaned) return '';
  const parts = cleaned.split('/');
  parts.pop();
  return parts.join('/');
}

export function pathSegments(path: string): { label: string; path: string }[] {
  const cleaned = path.replace(/^\/+|\/+$/g, '');
  if (!cleaned) return [];
  const segments = cleaned.split('/');
  return segments.map((label, index) => ({
    label,
    path: segments.slice(0, index + 1).join('/'),
  }));
}

export function topSegment(path: string): string {
  return path.replace(/^\/+/, '').split('/')[0] || '';
}
