"""Small shared pgvector helpers.

Kept dependency-light: a connection factory that registers the pgvector type and
a helper to format a Python float list as a pgvector literal for SQL.
"""
from __future__ import annotations

from typing import List


def connect(database_url: str):
    """Open a psycopg connection with the pgvector type registered.

    Imports are local so that importing the store modules (and therefore the
    graph) does not require psycopg/pgvector to be installed or a DB to be up.
    """
    import psycopg  # lazy
    from pgvector.psycopg import register_vector  # lazy

    conn = psycopg.connect(database_url, autocommit=True)
    register_vector(conn)
    return conn


def to_vector_literal(vec: List[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2,...]'. Works regardless of whether
    numpy is present, and is accepted by a `vector` column cast."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
