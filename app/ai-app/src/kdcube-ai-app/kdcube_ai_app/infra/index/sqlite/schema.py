# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""SQLite schema for the hybrid index: one document table, a standalone FTS5
mirror for lexical search, a vectors table (embed-on-write, so rebuilds never
re-embed), and a meta table for the build-version signature."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS docs (
    id            TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    ts            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS vectors (
    rowid INTEGER PRIMARY KEY,   -- == docs.rowid
    vec   TEXT NOT NULL          -- JSON list[float]
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(text);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""
