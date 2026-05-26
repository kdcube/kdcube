import { useAppDispatch, useAppSelector } from '../app/hooks';
import { deleteSelected, exportSelected, loadList } from '../features/storage/storageSlice';

export function StorageActions() {
  const dispatch = useAppDispatch();
  const { selectedPaths, deleting, exporting, loading } = useAppSelector((s) => s.storage);
  const disabled = selectedPaths.length === 0 || deleting || exporting || loading;

  return (
    <div className="actions">
      <button type="button" onClick={() => void dispatch(loadList())} disabled={loading}>
        Refresh
      </button>
      <button type="button" onClick={() => void dispatch(exportSelected())} disabled={disabled}>
        {exporting ? 'Exporting...' : 'Export selected'}
      </button>
      <button
        className="danger"
        type="button"
        disabled={disabled}
        onClick={() => {
          const ok = window.confirm(`Delete ${selectedPaths.length} selected item(s)?`);
          if (!ok) return;
          void dispatch(deleteSelected()).then(() => dispatch(loadList()));
        }}
      >
        {deleting ? 'Deleting...' : 'Delete selected'}
      </button>
      <span className="selection-count">{selectedPaths.length} selected</span>
    </div>
  );
}
