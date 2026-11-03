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
    storage_dir: Path = ROOT / 'storage'
    max_upload_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 500
    max_text_chars: int = 500_000
    max_chunks: int = 1500
    parse_timeout_seconds: int = 60
    processing_timeout_seconds: int = 240

    @classmethod
    def load(cls):
        values = {**dotenv_values(ROOT / '.env'), **os.environ}
        limits = {}
        for name, default, low, high in [('max_upload_bytes',100*1024*1024,1024,1024*1024*1024),('max_pdf_pages',500,1,5000),('max_text_chars',500_000,100,5_000_000),('max_chunks',1500,1,10000),('parse_timeout_seconds',60,1,240),('processing_timeout_seconds',240,1,3600)]:
            limits[name] = int(values.get(name.upper()) or default)
            if not low <= limits[name] <= high:
                raise ValueError('Invalid resource limit: '+name.upper())
        return cls(URL.create(
            'postgresql+psycopg',
            username=values.get('POSTGRES_USER') or 'trusted_rag_app',
            password=values.get('POSTGRES_PASSWORD'),
            host=values.get('POSTGRES_HOST') or '127.0.0.1',
            port=int(values.get('POSTGRES_PORT') or 5432),
            database=values.get('POSTGRES_DB') or 'trusted_rag_lab',
        ), **limits)
