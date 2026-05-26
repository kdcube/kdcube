import { useEffect } from 'react';
import { useAppDispatch, useAppSelector } from './app/hooks';
import { settings } from './api/settings';
import { StorageDashboard } from './features/storage/StorageDashboard';
import { setRuntimeDefaults } from './features/storage/storageSlice';

export function App() {
  const dispatch = useAppDispatch();
  const ready = useAppSelector((s) => s.storage.ready);

  useEffect(() => {
    let mounted = true;
    void settings.setupParentListener().then(() => {
      if (mounted) dispatch(setRuntimeDefaults());
    });
    return () => {
      mounted = false;
    };
  }, [dispatch]);

  if (!ready) {
    return <div className="boot-state">Loading storage admin...</div>;
  }

  return <StorageDashboard />;
}
