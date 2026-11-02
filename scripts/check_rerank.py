"""Check an OpenAI-style SiliconFlow rerank API with synthetic examples only."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
cfg = dotenv_values(root / ".env")
base = (cfg.get("RERANK_BASE_URL") or cfg.get("EMBEDDING_BASE_URL") or "").rstrip("/")
key = cfg.get("RERANK_API_KEY") or cfg.get("EMBEDDING_API_KEY")
model = cfg.get("RERANK_MODEL")
if not all([base, key, model]):
    raise SystemExit("BLOCKED: configure RERANK_MODEL and rerank or embedding base URL/API key")
url = urlsplit(base)
if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}):
    raise SystemExit("BLOCKED: remote endpoint must use HTTPS")
documents = [
    "今天午餐准备吃面条。",
    "项目通过成员关系与角色权限控制资料访问，检索前鉴权，原文查看时再次核验。",
    "文档解析完成后会被分块，并计算向量用于检索。",
]
result = {"checked_at": datetime.now(timezone.utc).isoformat(), "host": url.hostname, "model": model}
try:
    with httpx.Client(timeout=60) as client:
        response = client.post(base + "/rerank", headers={"Authorization": "Bearer " + key}, json={
            "model": model, "query": "如何控制项目文档的访问权限？", "documents": documents,
            "top_n": len(documents), "return_documents": False,
        })
    result["http_status"] = response.status_code
    if response.status_code != 200:
        raise RuntimeError("HTTP error")
    rows = response.json()["results"]
    if sorted(row["index"] for row in rows) != list(range(len(documents))):
        raise ValueError("Missing or duplicate indices")
    scores = [row["relevance_score"] for row in rows]
    if not all(type(s) in (int, float) and math.isfinite(s) for s in scores):
        raise ValueError("Invalid scores")
    if scores != sorted(scores, reverse=True):
        raise ValueError("Scores not descending")
    result["ranking"] = [{"index": row["index"], "score": round(row["relevance_score"], 6)} for row in rows]
    result["schema_check"] = "passed"
    if rows[0]["index"] != 1:
        raise ValueError("Directly relevant document not ranked first")
    result["synthetic_relevance_check"] = "passed"
    result["status"] = "passed"
except Exception as exc:
    result.update(status="failed", error_type=type(exc).__name__)
output = root / ".local" / "rerank-check.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=True, indent=2))
raise SystemExit(0 if result["status"] == "passed" else 1)
