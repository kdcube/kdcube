import { FormEvent, useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  clearMemoryFocus,
  deleteMemoriesBySearch,
  exportMemories,
  focusMemories,
  loadMemories,
  loadMemoryEvents,
  normalizeMemoryRefs,
  setKeywordsFilter,
  setLabelsFilter,
  setQuery,
  setStatus,
} from './memoriesSlice';

export function MemoryFilters() {
  const dispatch = useAppDispatch();
  const {
    focusedMemoryIds,
    keywordsFilter,
    labelsFilter,
    memories,
    query,
    status,
    viewMode,
    loading,
  } = useAppSelector((state) => state.memories);
  const compact = viewMode === 'compact';
  const hasFilters = Boolean(query.trim() || labelsFilter.trim() || keywordsFilter.trim() || status !== 'active');
  const focusedMemory = focusedMemoryIds.length === 1 ? memories.find((memory) => memory.id === focusedMemoryIds[0]) : undefined;
  const [idsText, setIdsText] = useState('');

  useEffect(() => {
    setIdsText(focusedMemoryIds.map((id) => `mem:${id}`).join(', '));
  }, [focusedMemoryIds.join('|')]);

  function submit(event: FormEvent) {
    event.preventDefault();
    void dispatch(loadMemories());
  }

  function downloadText(filename: string, content: string, mime = 'text/plain') {
    const blob = new Blob([content], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function runExport(format: 'json' | 'markdown' | 'csv', all = false) {
    const payload = await dispatch(exportMemories({ format, all })).unwrap();
    if (!payload.ok || !payload.content) return;
    downloadText(payload.filename || `memories.${format}`, payload.content, payload.mime || 'text/plain');
  }

  async function runDelete(all = false) {
    const label = all ? 'all visible memory notes' : (hasFilters ? 'matching the current filters' : 'all active visible memory notes');
    if (!window.confirm(`Delete ${label}? This permanently deletes the notes and their memory events.`)) return;
    await dispatch(deleteMemoriesBySearch({ all })).unwrap();
    void dispatch(loadMemories());
  }

  function showMemoryIds() {
    const memoryIds = normalizeMemoryRefs(idsText);
    if (!memoryIds.length) return;
    dispatch(focusMemories(memoryIds));
    void dispatch(loadMemories()).then(() => {
      void dispatch(loadMemoryEvents(memoryIds[0]));
    });
  }

  return (
    <form className={`filters ${compact ? 'compact-filters' : ''}`} onSubmit={submit}>
      {focusedMemoryIds.length ? (
        <div className="focused-memory-filter">
          <div>
            <span>{focusedMemoryIds.length === 1 ? 'Filtered by memory id' : 'Filtered by memory ids'}</span>
            <strong>{focusedMemoryIds.length === 1 ? focusedMemory?.memory || focusedMemoryIds[0] : `${focusedMemoryIds.length} memories by id`}</strong>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              dispatch(clearMemoryFocus());
              void dispatch(loadMemories());
            }}
          >
            Back to list
          </button>
        </div>
      ) : null}
      {compact ? null : <div className="filter-row">
        <label>
          <span>Status</span>
          <select value={status} onChange={(event) => dispatch(setStatus(event.target.value))}>
            <option value="active">Active</option>
            <option value="weakened">Weakened</option>
            <option value="unsupported">Unsupported</option>
            <option value="retired">Retired</option>
            <option value="any">Any</option>
          </select>
        </label>
      </div>}
      {compact ? null : <div className="id-filter-row">
        <input
          value={idsText}
          onChange={(event) => setIdsText(event.target.value)}
          placeholder="Memory ids, for example mem:mem_a, mem:mem_b"
        />
        <button type="button" className="secondary-button" disabled={loading} onClick={showMemoryIds}>
          Show ids
        </button>
      </div>}
      <div className="search-row">
        <input
          value={query}
          onChange={(event) => dispatch(setQuery(event.target.value))}
          placeholder={compact ? 'Search memories...' : 'Semantic search'}
        />
        {compact ? null : <input
          value={labelsFilter}
          onChange={(event) => dispatch(setLabelsFilter(event.target.value))}
          placeholder="Tags"
        />}
        {compact ? null : <input
          value={keywordsFilter}
          onChange={(event) => dispatch(setKeywordsFilter(event.target.value))}
          placeholder="Keywords"
        />}
        <button type="submit" disabled={loading}>
          Search
        </button>
      </div>
      {compact ? null : <div className="memory-actions-row">
        <button type="button" className="secondary-button" disabled={loading} onClick={() => void runExport('json', false)}>
          Download matching JSON
        </button>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => void runExport('json', true)}>
          Download all JSON
        </button>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => void runExport('csv', false)}>
          CSV
        </button>
        <button type="button" className="danger-button" disabled={loading} onClick={() => void runDelete(false)}>
          {hasFilters ? 'Delete matching' : 'Delete active'}
        </button>
        <button type="button" className="danger-button" disabled={loading} onClick={() => void runDelete(true)}>
          Delete all
        </button>
      </div>}
    </form>
  );
}
