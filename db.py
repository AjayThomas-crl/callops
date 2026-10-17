"""Shared Postgres connection for CallOps scripts."""
import os

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://callops:callops@localhost:5432/callops")


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)
