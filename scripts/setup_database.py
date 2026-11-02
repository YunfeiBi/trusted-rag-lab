"""Create only this project's role/database and enable pgvector. Never print credentials."""
from pathlib import Path
import secrets
import sys

import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
cfg = dotenv_values(env_file)
if not cfg.get("POSTGRES_ADMIN_PASSWORD"):
    print("BLOCKED: set POSTGRES_ADMIN_PASSWORD in the project .env (do not send it in chat).")
    sys.exit(2)
db = cfg.get("POSTGRES_DB") or "trusted_rag_lab"
user = cfg.get("POSTGRES_USER") or "trusted_rag_app"
if not db.startswith("trusted_rag") or not user.startswith("trusted_rag"):
    raise SystemExit("Refusing to modify a role/database outside the trusted_rag prefix.")
common = dict(host=cfg.get("POSTGRES_HOST") or "127.0.0.1", port=cfg.get("POSTGRES_PORT") or "5432", connect_timeout=5)
admin = dict(**common, user=cfg.get("POSTGRES_ADMIN_USER") or "postgres", password=cfg["POSTGRES_ADMIN_PASSWORD"])
try:
    with psycopg.connect(**admin, dbname=cfg.get("POSTGRES_ADMIN_DB") or "postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'").fetchone():
            raise SystemExit("BLOCKED: server-side vector extension is not installed.")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,)).fetchone()
        password = cfg.get("POSTGRES_PASSWORD")
        if exists and not password:
            raise SystemExit("Existing app role found: set its POSTGRES_PASSWORD; no password was changed.")
        if not exists:
            if not password:
                password = secrets.token_urlsafe(32)
                set_key(str(env_file), "POSTGRES_PASSWORD", password)
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(user), sql.Literal(password)))
        owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (db,)).fetchone()
        if owner and owner[0] != user:
            raise SystemExit("Database exists with another owner; refusing to change ownership.")
        if not owner:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(user)))
    with psycopg.connect(**admin, dbname=db, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    with psycopg.connect(**common, dbname=db, user=user, password=password) as conn:
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        conn.execute("CREATE TEMP TABLE environment_vector_check (v vector(3))")
        conn.execute("INSERT INTO environment_vector_check VALUES ('[1,0,0]'), ('[0,1,0]')")
        distance = conn.execute("SELECT min(v <-> '[1,0,0]'::vector) FROM environment_vector_check").fetchone()[0]
        if distance != 0:
            raise RuntimeError("Unexpected vector distance")
        conn.rollback()
    print(f"PASS: project database and limited login verified; pgvector {version}; vector query passed.")
except psycopg.Error as exc:
    print(f"Database setup failed ({type(exc).__name__}, SQLSTATE={exc.sqlstate}); credentials are not displayed.")
    sys.exit(1)
