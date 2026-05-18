import { FormEvent } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  deleteMemoriesBySearch,
  exportMemories,
  loadMemories,
  setKeywordsFilter,
  setLabelsFilter,
  setQuery,
  setScopeFilter,
  setStatus,
} from './memoriesSlice';
import type { ScopeFilter } from '../../api/types';

export function MemoryFilters() {
  const dispatch = useAppDispatch();
  const {
    allowAllUserMemories,
    keywordsFilter,
    labelsFilter,
    query,
    scopeFilter,
    status,
    loading,
  } = useAppSelector((state) => state.memories);
  const hasFilters = Boolean(query.trim() || labelsFilter.trim() || keywordsFilter.trim() || status !== 'active');

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

  return (
    <form className="filters" onSubmit={submit}>
      <div className="filter-row">
        <label>
          <span>Scope</span>
          <select
            value={scopeFilter}
            onChange={(event) => {
              dispatch(setScopeFilter(event.target.value as ScopeFilter));
              void dispatch(loadMemories());
            }}
          >
            <option value="current_bundle">This bundle</option>
            {allowAllUserMemories ? <option value="all_user_memories">All user memories</option> : null}
          </select>
        </label>
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
      </div>
      <div className="search-row">
        <input
          value={query}
          onChange={(event) => dispatch(setQuery(event.target.value))}
          placeholder="Semantic search"
        />
        <input
          value={labelsFilter}
          onChange={(event) => dispatch(setLabelsFilter(event.target.value))}
          placeholder="Tags"
        />
        <input
          value={keywordsFilter}
          onChange={(event) => dispatch(setKeywordsFilter(event.target.value))}
          placeholder="Keywords"
        />
        <button type="submit" disabled={loading}>
          Search
        </button>
      </div>
      <div className="memory-actions-row">
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
      </div>
    </form>
  );
}
