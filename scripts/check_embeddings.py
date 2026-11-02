"""Validate embedding batches and pgvector round-trip using synthetic text only."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg import sql

root = Path(__file__).resolve().parents[1]
cfg = dotenv_values(root / ".env")
base = (cfg.get("EMBEDDING_BASE_URL") or cfg.get("MODEL_BASE_URL") or "").rstrip("/")
key = cfg.get("EMBEDDING_API_KEY") or cfg.get("MODEL_API_KEY")
model = cfg.get("EMBEDDING_MODEL")
if not all([base, key, model]):
    raise SystemExit("BLOCKED: missing embedding base URL, API key or model")
url = urlsplit(base)
if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}):
    raise SystemExit("BLOCKED: remote endpoint must use HTTPS")

texts = ["如何管理项目文档的访问权限？", "通过项目成员关系控制资料访问。", "今天午餐准备吃面条。"]
result = {"checked_at": datetime.now(timezone.utc).isoformat(), "host": url.hostname, "model": model}
try:
    with httpx.Client(timeout=60) as client:
        response = client.post(base + "/embeddings", headers={"Authorization": "Bearer " + key}, json={"model": model, "input": texts})
    result["http_status"] = response.status_code
    if response.status_code != 200:
        raise RuntimeError("Embedding endpoint returned HTTP " + str(response.status_code))
    data = sorted(response.json()["data"], key=lambda item: item["index"])
    if [item["index"] for item in data] != list(range(len(texts))):
        raise RuntimeError("Unexpected batch indices")
    vectors = [item["embedding"] for item in data]
    dimensions = len(vectors[0])
    if not dimensions or any(len(v) != dimensions for v in vectors):
        raise RuntimeError("Inconsistent embedding dimensions")
    if any(type(x) not in (float, int) or not math.isfinite(x) for v in vectors for x in v):
        raise RuntimeError("Invalid embedding values")
    norms = [math.sqrt(sum(x*x for x in v)) for v in vectors]
    if not all(n > 0 for n in norms):
        raise RuntimeError("Zero embedding")
    def cosine(i, j):
        return sum(a*b for a, b in zip(vectors[i], vectors[j])) / (norms[i]*norms[j])
    result.update(embedding="passed", dimensions=dimensions, batch_size=len(texts), related_cosine=round(cosine(0, 1), 6), unrelated_cosine=round(cosine(0, 2), 6))
    with psycopg.connect(host=cfg.get("POSTGRES_HOST") or "127.0.0.1", port=cfg.get("POSTGRES_PORT") or "5432", dbname=cfg.get("POSTGRES_DB") or "trusted_rag_lab", user=cfg.get("POSTGRES_USER") or "trusted_rag_app", password=cfg.get("POSTGRES_PASSWORD"), connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE TEMP TABLE embedding_smoke (id int, embedding vector({}))").format(sql.Literal(dimensions)))
        for i, vector in enumerate(vectors):
            conn.execute("INSERT INTO embedding_smoke VALUES (%s, %s::vector)", (i, json.dumps(vector)))
        rows = conn.execute("SELECT id, embedding <=> %s::vector AS distance FROM embedding_smoke ORDER BY distance", (json.dumps(vectors[0]),)).fetchall()
        if rows[0][0] != 0 or abs(rows[0][1]) > 1e-5:
            raise RuntimeError("Unexpected pgvector nearest neighbor")
        result.update(database="passed", nearest_neighbor_ids=[row[0] for row in rows])
        conn.rollback()
except Exception as exc:
    result["error_type"] = type(exc).__name__
    result["status"] = "failed"
else:
    result["status"] = "passed"
output = root / ".local" / "embedding-check.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=True, indent=2))
raise SystemExit(0 if result["status"] == "passed" else 1)
