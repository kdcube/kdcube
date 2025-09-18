# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/context/vector/conv_ticket_store.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from kdcube_ai_app.apps.chat.sdk.config import get_settings
import asyncpg, uuid, json
from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string

def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

def _as_dict(x) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return {}
    # tolerate [(k,v), ...]
    try:
        return dict(x)
    except Exception:
        return {}

def _as_vec(x) -> Optional[List[float]]:
    if x is None:
        return None
    if isinstance(x, list):
        try:
            return [float(v) for v in x]
        except Exception:
            return None
    if isinstance(x, str):
        import re
        nums = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', x)
        try:
            return [float(n) for n in nums] if nums else None
        except Exception:
            return None
    return None

def _rec_get(r, key, default=None):
    try:
        return r[key]
    except Exception:
        return default

@dataclass
class Ticket:
    ticket_id: str
    track_id: str
    title: str
    description: str
    status: str
    priority: int
    assignee: Optional[str]
    tags: List[str]
    created_at: str
    updated_at: str
    data: Dict[str, Any]
    turn_id: Optional[str]

    @staticmethod
    def _row_to_ticket_dict(r) -> Dict[str, Any]:
        return {
            "ticket_id": _rec_get(r, "ticket_id"),
            "track_id": _rec_get(r, "track_id"),
            "title": _rec_get(r, "title"),
            "description": _rec_get(r, "description") or "",
            "status": _rec_get(r, "status") or "open",
            "priority": int(_rec_get(r, "priority", 3) or 3),
            "assignee": _rec_get(r, "assignee"),
            "tags": list(_rec_get(r, "tags") or []),
            "created_at": str(_rec_get(r, "created_at")),
            "updated_at": str(_rec_get(r, "updated_at")),
            "data": dict(_rec_get(r, "data") or {}),
            "turn_id": _rec_get(r, "turn_id"),
        }

class ConvTicketStore:
    """
    Tracks (thematic threads within a conversation), Track DAGs, and Track Tickets.
    Schema is derived from tenant/project; we do NOT duplicate tenant/project in rows.
    """
    def __init__(self,
                 pool: Optional[asyncpg.Pool] = None):

        self._pool: Optional[asyncpg.Pool] = pool
        self._settings = get_settings()
        t = self._settings.TENANT.replace("-", "_").replace(" ", "_")
        p = self._settings.PROJECT.replace("-", "_").replace(" ", "_")
        schema = f"{t}_{p}"
        self.schema = f"kdcube_{schema}" if not schema.startswith("kdcube_") else schema

    async def init(self):
        async def _init_conn(conn: asyncpg.Connection):
            # Encode/decode json & jsonb as Python dicts automatically
            await conn.set_type_codec('json',  encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
            await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
        if not self._pool:
            self._pool = await asyncpg.create_pool(
                host=self._settings.PGHOST, port=self._settings.PGPORT,
                user=self._settings.PGUSER, password=self._settings.PGPASSWORD, database=self._settings.PGDATABASE,
                ssl=self._settings.PGSSL,
                init=_init_conn,
            )

    async def close(self):
        if self._pool: await self._pool.close()

    async def ensure_schema(self):
        # rely on external DDL execution; this makes sure the tables exist in dev
        ddl = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.conv_track_tickets (
          ticket_id TEXT PRIMARY KEY,
          track_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          conversation_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'open',
          priority SMALLINT NOT NULL DEFAULT 3,
          assignee TEXT,
          tags TEXT[] NOT NULL DEFAULT '{{}}',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          embedding VECTOR(1536),
          
          data JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          turn_id TEXT
        );
        
        -- Helpful indexes
        CREATE INDEX IF NOT EXISTS conv_track_tickets_lookup_idx
          ON {self.schema}.conv_track_tickets (user_id, conversation_id, track_id, status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS conv_track_tickets_tags_gin
          ON {self.schema}.conv_track_tickets USING GIN (tags);
        -- If you use pgvector searches in prod, also:
        -- CREATE INDEX IF NOT EXISTS conv_track_tickets_embed_ivfflat
        --   ON {self.schema}.conv_track_tickets USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
        """
        async with self._pool.acquire() as con:
            for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
                await con.execute(stmt)

    async def create_ticket(self, *,
                            track_id: str,
                            user_id: str,
                            conversation_id: str,
                            turn_id: Optional[str] = None,
                            title: str,
                            description: str,
                            embed_texts_fn: Callable[[List[str]], Awaitable[List[Any]]],
                            priority: int = 3,
                            tags: Optional[List[str]] = None,
                            assignee: Optional[str] = None,
                            data: Optional[Dict[str, Any]] = None, ) -> Ticket:
        """
        Create a ticket and return the full ticket row as a dict.
        """
        tid = _new_id("tkt")
        [v] = await embed_texts_fn([f"{title}\n\n{description}"])
        async with self._pool.acquire() as con:
            row = await con.fetchrow(
                f"""INSERT INTO {self.schema}.conv_track_tickets
                    (ticket_id, track_id, user_id, conversation_id, title, description,
                     status, priority, assignee, tags, embedding, data, turn_id)
                 VALUES ($1,$2,$3,$4,$5,$6,'open',$7,$8,$9,$10::vector,$11::jsonb,$12)
                 RETURNING *""",
                tid, track_id, user_id, conversation_id,
                title, description, int(priority), assignee, (tags or []),
                convert_embedding_to_string(v),
                (data or {}), turn_id
            )
        return Ticket(**Ticket._row_to_ticket_dict(row))

    async def update_ticket(self, *,
                            ticket_id: str,
                            embed_texts_fn: Optional[Callable[[List[str]], Awaitable[List[Any]]]] = None,
                            title: Optional[str] = None, description: Optional[str] = None,
                            status: Optional[str] = None, priority: Optional[int] = None,
                            assignee: Optional[str] = None, tags: Optional[List[str]] = None,
                            data_patch: Optional[Dict[str, Any]] = None,
                            turn_id: Optional[str] = None
    ) -> None:
        sets=[]; vals=[]

        need_reembed = bool(embed_texts_fn) and (title is not None or description is not None)
        cur_title = title
        cur_desc  = description
        if need_reembed and (title is None or description is None):
            async with self._pool.acquire() as con:
                cur = await con.fetchrow(
                    f"SELECT title, description FROM {self.schema}.conv_track_tickets WHERE ticket_id=$1",
                    ticket_id
                )
            if cur_title is None: cur_title = cur["title"]
            if cur_desc  is None: cur_desc  = cur["description"]
        if need_reembed:
            [v] = await embed_texts_fn([f"{cur_title}\n\n{cur_desc}"])
            sets.append("embedding=$"+str(len(vals)+1))
            vals.append(convert_embedding_to_string(v))

        if title is not None: sets.append("title=$"+str(len(vals)+1)); vals.append(title)
        if description is not None: sets.append("description=$"+str(len(vals)+1)); vals.append(description)
        if status is not None: sets.append("status=$"+str(len(vals)+1)); vals.append(status)
        if priority is not None: sets.append("priority=$"+str(len(vals)+1)); vals.append(int(priority))
        if assignee is not None: sets.append("assignee=$"+str(len(vals)+1)); vals.append(assignee)
        if tags is not None: sets.append("tags=$"+str(len(vals)+1)); vals.append(tags)
        if data_patch:              sets.append(f"data = coalesce(data,'{{}}'::jsonb) || ${len(vals)+1}::jsonb"); vals.append(data_patch)
        if turn_id is not None:     sets.append(f"turn_id=${len(vals)+1}");     vals.append(turn_id)


        sets.append("updated_at=now()")
        q = f"UPDATE {self.schema}.conv_track_tickets SET {', '.join(sets)} WHERE ticket_id=${len(vals)+1}"
        vals.append(ticket_id)
        async with self._pool.acquire() as con:
            await con.execute(q, *vals)

    async def sem_search_tickets(self, *, track_id: str,
                                 query: str,
                                 embed_texts_fn: Callable[[List[str]], Awaitable[List[Any]]],
                                 top_k: int = 6) -> List[Ticket]:
        [v] = await embed_texts_fn([query])
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                f"""SELECT *, 1 - (embedding <=> $2) AS score
                    FROM {self.schema}.conv_track_tickets
                    WHERE track_id=$1 AND embedding IS NOT NULL
                    ORDER BY embedding <=> $2
                    LIMIT {int(top_k)}""",
                track_id, convert_embedding_to_string(v)
            )
        out=[]
        for r in rows:
            out.append(Ticket(
                ticket_id=r["ticket_id"], track_id=r["track_id"], title=r["title"], description=r["description"],
                status=r["status"], priority=int(r["priority"] or 3), assignee=r["assignee"],
                tags=list(r["tags"] or []), created_at=str(r["created_at"]), updated_at=str(r["updated_at"]),
                data=dict(r["data"] or {}), turn_id=r["turn_id"]
            ))
        return out

    async def list_tickets(
            self,
            *,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            track_id: Optional[str] = None,
            turn_id: Optional[str] = None,
            status: Optional[str] = None
    ) -> List[Ticket]:
        """
        List tickets filtered by any of the provided fields.
        All filters are optional; if none are provided, returns the most recent tickets.
        """
        where: List[str] = []
        params: List[Any] = []

        def add(cond: str, val: Any):
            params.append(val)
            where.append(f"{cond.replace('$n', '$'+str(len(params)))}")

        if user_id is not None:
            add("user_id=$n", user_id)
        if conversation_id is not None:
            add("conversation_id=$n", conversation_id)
        if track_id is not None:
            add("track_id=$n", track_id)
        if turn_id is not None:
            add("turn_id=$n", turn_id)
        if status is not None:
            add("status=$n", status)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        q = f"SELECT * FROM {self.schema}.conv_track_tickets{where_sql} ORDER BY updated_at DESC"

        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *params)

        out: List[Ticket] = []
        for r in rows:
            d = Ticket._row_to_ticket_dict(r)
            out.append(Ticket(
                ticket_id=d["ticket_id"],
                track_id=d["track_id"],
                title=d["title"],
                description=d["description"],
                status=d["status"],
                priority=d["priority"],
                assignee=d["assignee"],
                tags=d["tags"],
                created_at=d["created_at"],
                updated_at=d["updated_at"],
                data=d["data"],
                turn_id=d["turn_id"],
            ))
        return out

    async def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        async with self._pool.acquire() as con:
            r = await con.fetchrow(
                f"SELECT * FROM {self.schema}.conv_track_tickets WHERE ticket_id=$1",
                ticket_id
            )
        if not r:
            return None
        return Ticket(
            ticket_id=r["ticket_id"], track_id=r["track_id"], title=r["title"], description=r["description"],
            status=r["status"], priority=int(r["priority"] or 3), assignee=r["assignee"],
            tags=list(r["tags"] or []), created_at=str(r["created_at"]), updated_at=str(r["updated_at"]),
            data=dict(r["data"] or {}), turn_id=r["turn_id"]
        )