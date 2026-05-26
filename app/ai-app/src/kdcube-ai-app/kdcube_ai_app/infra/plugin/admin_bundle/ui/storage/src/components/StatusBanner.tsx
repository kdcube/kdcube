import { useAppDispatch, useAppSelector } from '../app/hooks';
import { setError, setMessage } from '../features/storage/storageSlice';

export function StatusBanner() {
  const dispatch = useAppDispatch();
  const error = useAppSelector((s) => s.storage.error);
  const message = useAppSelector((s) => s.storage.message);
  const text = error || message;
  if (!text) return null;
  return (
    <div className={`status-banner ${error ? 'error' : 'ok'}`}>
      <span>{text}</span>
      <button type="button" onClick={() => {
        dispatch(setError(null));
        dispatch(setMessage(null));
      }}>Dismiss</button>
    </div>
  );
}
