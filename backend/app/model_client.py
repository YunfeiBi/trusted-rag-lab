import math
import os
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx

from .config import ROOT
from .ingestion import IngestionError


class EmbeddingClient:
    def __init__(self):
        cfg = {**dotenv_values(ROOT / '.env'), **os.environ}
        self.model = cfg.get('EMBEDDING_MODEL') or ''
        self.base = (cfg.get('EMBEDDING_BASE_URL') or cfg.get('MODEL_BASE_URL') or '').rstrip('/')
        self.key = cfg.get('EMBEDDING_API_KEY') or cfg.get('MODEL_API_KEY') or ''

    def embed(self, texts: list[str]) -> list[list[float]]:
        location = urlsplit(self.base)
        if not self.model or not self.key or not (location.scheme == 'https' or (location.scheme == 'http' and location.hostname in {'localhost', '127.0.0.1'})):
            raise IngestionError('MODEL_NOT_CONFIGURED')
        try:
            with httpx.Client(timeout=45) as client:
                response = client.post(self.base+'/embeddings', headers={'Authorization': 'Bearer '+self.key}, json={'model': self.model, 'input': texts})
            if response.status_code != 200:
                raise IngestionError('EMBEDDING_HTTP_ERROR', retryable=response.status_code in {408,429} or response.status_code >= 500)
            rows = sorted(response.json()['data'], key=lambda row: row['index'])
            if [row['index'] for row in rows] != list(range(len(texts))):
                raise ValueError('Invalid indices')
            vectors = [row['embedding'] for row in rows]
            for vector in vectors:
                if len(vector) != 1024 or not all(type(v) in (int, float) and math.isfinite(v) for v in vector) or not any(vector):
                    raise ValueError('Invalid vector')
            return vectors
        except IngestionError:
            raise
        except httpx.RequestError as exc:
            raise IngestionError('EMBEDDING_UNAVAILABLE', retryable=True) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise IngestionError('EMBEDDING_INVALID_RESPONSE') from exc
