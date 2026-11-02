"""Make tiny synthetic requests to a configured OpenAI-compatible endpoint. No document data."""
from pathlib import Path
import sys
from dotenv import dotenv_values
import httpx

cfg = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
needed = ["MODEL_BASE_URL", "MODEL_API_KEY", "CHAT_MODEL", "EMBEDDING_MODEL"]
missing = [key for key in needed if not cfg.get(key)]
if missing:
    print("BLOCKED missing configuration: " + ", ".join(missing))
    sys.exit(2)
failed = False
with httpx.Client(timeout=45) as client:
    for name, path, data in [
        ("chat", "/chat/completions", {"model": cfg["CHAT_MODEL"], "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16}),
        ("embedding", "/embeddings", {"model": cfg["EMBEDDING_MODEL"], "input": "environment connectivity check"}),
    ]:
        try:
            base = cfg["MODEL_BASE_URL"]
            key = cfg["MODEL_API_KEY"]
            if name == "embedding":
                base = cfg.get("EMBEDDING_BASE_URL") or base
                key = cfg.get("EMBEDDING_API_KEY") or key
            base = base.rstrip("/")
            if not base.startswith("https://") and not base.startswith(("http://127.0.0.1", "http://localhost")):
                raise ValueError("Remote endpoint must use HTTPS")
            response = client.post(base + path, json=data, headers={"Authorization": "Bearer " + key})
            if response.status_code != 200:
                print(f"FAIL {name}: HTTP {response.status_code} (response body omitted)")
                failed = True
                continue
            payload = response.json()
            if name == "chat":
                assert payload["choices"][0]["message"].get("content")
                print("PASS chat: valid completion received; content omitted")
            else:
                vector = payload["data"][0]["embedding"]
                assert vector and all(isinstance(n, (int, float)) for n in vector)
                print(f"PASS embedding: dimensions={len(vector)}")
        except Exception as exc:
            print(f"FAIL {name}: {type(exc).__name__} (details omitted)")
            failed = True
sys.exit(1 if failed else 0)
