"""Smoke-check installed packages, FastAPI routing, PDF parsing and Chinese segmentation."""
import importlib
from io import BytesIO

for name in ["fastapi", "uvicorn", "sqlalchemy", "psycopg", "pgvector", "pydantic_settings", "httpx", "pypdf", "jieba", "numpy", "alembic", "pytest"]:
    importlib.import_module(name)
    print(f"PASS import {name}")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
import jieba

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

assert TestClient(app).get("/health").json() == {"status": "ok"}
buffer = BytesIO()
writer = PdfWriter()
writer.add_blank_page(width=100, height=100)
writer.write(buffer)
buffer.seek(0)
assert len(PdfReader(buffer).pages) == 1
assert list(jieba.cut("软件体系结构知识检索"))
print("PASS FastAPI request, PDF read/write and Chinese segmentation")
