import { useAppDispatch, useAppSelector } from '../app/hooks';
import { loadList, setPath } from '../features/storage/storageSlice';
import { parentPath, pathSegments } from '../utils/format';

export function Breadcrumbs() {
  const dispatch = useAppDispatch();
  const current = useAppSelector((s) => s.storage.current);
  const selectedPath = useAppSelector((s) => s.storage.path);
  const path = current?.path || selectedPath;
  const segments = pathSegments(path);

  const openPath = (next: string) => {
    dispatch(setPath(next));
    window.setTimeout(() => void dispatch(loadList()), 0);
  };

  return (
    <div className="breadcrumbs">
      <button className="root-crumb" type="button" onClick={() => openPath('')} title="Storage scope root">/</button>
      {segments.map((segment) => (
        <button key={segment.path} type="button" onClick={() => openPath(segment.path)}>
          {segment.label}
        </button>
      ))}
      {path ? <button className="secondary" type="button" onClick={() => openPath(parentPath(path))}>Up</button> : null}
    </div>
  );
}
