# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/relational/psql/psql_base.py

import os
import urllib
import psycopg2
from psycopg2.extras import execute_values, register_default_json, register_default_jsonb
import json

from typing import Union, List, Optional, Dict

# Decode json/jsonb to Python dicts globally
register_default_json(loads=json.loads)
register_default_jsonb(loads=json.loads)


class PostgreSqlDbMgr:
    def __init__(self, connection_params: Optional[Dict[str, str]] = None):
        connection_params = connection_params or {}
        self.host = connection_params.get("host") or os.environ.get("POSTGRES_HOST")
        self.port = connection_params.get("port") or os.environ.get("POSTGRES_PORT")
        self.database = connection_params.get("database") or os.environ.get("POSTGRES_DATABASE")

        self.username = connection_params.get("username") or os.environ.get("POSTGRES_USER")
        self.password = connection_params.get("password") or os.environ.get("POSTGRES_PASSWORD")

        # Optional tuning knobs via env
        self.ssl = (os.environ.get("POSTGRES_SSL", "false").lower() == "true")
        self.appname = connection_params.get("application_name") or os.environ.get("POSTGRES_APPNAME", "kdcube-psql")
        self.statement_timeout_ms = int(os.environ.get("POSTGRES_STATEMENT_TIMEOUT_MS", "60000"))  # 60s
        self.search_path = connection_params.get("search_path") or os.environ.get("POSTGRES_SEARCH_PATH")  # optional

        # Build -c GUCs applied at session start
        opts = [
            "-c TimeZone=UTC",
            "-c datestyle=ISO, YMD",
            "-c intervalstyle=iso_8601",
            f"-c application_name={self.appname}",
            f"-c statement_timeout={self.statement_timeout_ms}",
            "-c extra_float_digits=3",
        ]
        if self.search_path:  # only if you truly need it; you already fully-qualify schema in SQL
            opts.append(f"-c search_path={self.search_path}")

        self._options = " ".join(opts)

    def get_connection(self):
        return psycopg2.connect(
            dbname=self.database,
            user=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            sslmode=("require" if self.ssl else "disable"),
            options=self._options,
        )

    def execute_sql_string(self, sql: str):
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                conn.commit()
        print(f"Executed SQL: {sql}")

    def execute_sql_file(self, file_path, substitutions=None):
        """
        Execute a SQL file.
        """
        with open(file_path, 'r') as file:
            sql = file.read()
            if substitutions:
                for key, value in substitutions.items():
                    if value is not None:
                        sql = sql.replace(f"<{key}>", value)
            self.execute_sql_string(sql)
        print(f"Executed SQL file: {file_path}")

    def execute_sql(
            self,
            sql: str,
            data: Union[tuple, List[tuple]] = None,
            as_dict: bool = True,
            debug: bool = False,
            bulk: bool = False
    ):
        """
        Execute arbitrary SQL with optional data.

        :param sql: The SQL query to execute.
        :param data: Optional tuple or list-of-tuples of data to bind.
        :param as_dict: Whether to return the results as a dictionary (list[dict])
                        or as a dict with "columns" / "rows".
        :param debug: If True, prints debug info.
        :param bulk: Use execute_values for bulk insertion.
        :return: Query results if it's a SELECT or if there's a RETURNING clause,
                 otherwise None.
        """
        if debug:
            print(f"Executing SQL: {sql}")
            print(f"With parameters: {data}")


        with self.get_connection() as conn:
            with conn.cursor() as cur:
                # If we want to use execute_values for bulk insertion
                if bulk and data:
                    execute_values(cur, sql, data)
                elif data:
                    cur.execute(sql, data)
                else:
                    cur.execute(sql)

                # 1) Check for "SELECT" or "RETURNING" in the query
                #    If present, we fetch rows

                stripped = sql.lstrip().lower()
                is_select = stripped.startswith("select") or stripped.startswith("with")
                has_returning = "returning" in stripped

                if is_select or has_returning:
                    # 2) fetch rows
                    rows = cur.fetchall()
                    columns = [desc[0] for desc in cur.description]
                    if as_dict:
                        return [dict(zip(columns, row)) for row in rows]
                    else:
                        return {"columns": columns, "rows": rows}

                # Otherwise (no data to return, e.g. normal INSERT/UPDATE/DELETE w/o returning)
                conn.commit()
                return None

    def list_schemas(self) -> list:
        sql = "SELECT schema_name FROM information_schema.schemata;"
        # Execute the query using the existing method
        results = self.execute_sql(sql)
        # Extract and return the schema names
        return [row["schema_name"] for row in results] if results else []