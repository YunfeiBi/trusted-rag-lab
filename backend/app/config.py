from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import dotenv_values
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: URL
    session_hours: int = 8

    @classmethod
    def load(cls):
        values = {**dotenv_values(ROOT / '.env'), **os.environ}
        return cls(URL.create(
            'postgresql+psycopg',
            username=values.get('POSTGRES_USER') or 'trusted_rag_app',
            password=values.get('POSTGRES_PASSWORD'),
            host=values.get('POSTGRES_HOST') or '127.0.0.1',
            port=int(values.get('POSTGRES_PORT') or 5432),
            database=values.get('POSTGRES_DB') or 'trusted_rag_lab',
        ))
