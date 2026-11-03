import math
import os
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx

from .config import ROOT
from .ingestion import IngestionError


class EmbeddingClient:
    def __init__(self, timeout=45):
        self.timeout = timeout
        cfg = {**dotenv_values(ROOT / '.env'), **os.environ}
        self.model = cfg.get('EMBEDDING_MODEL') or ''
        self.base = (cfg.get('EMBEDDING_BASE_URL') or cfg.get('MODEL_BASE_URL') or '').rstrip('/')
        self.key = cfg.get('EMBEDDING_API_KEY') or cfg.get('MODEL_API_KEY') or ''

    def embed(self, texts: list[str]) -> list[list[float]]:
        location = urlsplit(self.base)
        if not self.model or not self.key or not (location.scheme == 'https' or (location.scheme == 'http' and location.hostname in {'localhost', '127.0.0.1'})):
            raise IngestionError('MODEL_NOT_CONFIGURED')
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.base+'/embeddings', headers={'Authorization': 'Bearer '+self.key}, json={'model': self.model, 'input': texts})
            if response.status_code != 200:
                raise IngestionError('EMBEDDING_HTTP_ERROR', retryable=response.status_code in {408,429} or response.status_code >= 500)
            rows = sorted(response.json()['data'], key=lambda row: row['index'])
            if any(type(row['index']) is not int for row in rows) or [row['index'] for row in rows] != list(range(len(texts))):
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


class RerankClient:
    def __init__(self):
        cfg = {**dotenv_values(ROOT / '.env'), **os.environ}
        self.model = cfg.get('RERANK_MODEL') or ''
        self.base = (cfg.get('RERANK_BASE_URL') or cfg.get('EMBEDDING_BASE_URL') or '').rstrip('/')
        self.key = cfg.get('RERANK_API_KEY') or cfg.get('EMBEDDING_API_KEY') or ''

    def rerank(self, query: str, documents: list[str]):
        location = urlsplit(self.base)
        if not self.model or not self.key or not (location.scheme == 'https' or (location.scheme == 'http' and location.hostname in {'localhost','127.0.0.1'})):
            raise IngestionError('RERANK_NOT_CONFIGURED')
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(self.base+'/rerank', headers={'Authorization': 'Bearer '+self.key}, json={
                    'model': self.model, 'query': query, 'documents': documents,
                    'top_n': len(documents), 'return_documents': False,
                })
            if response.status_code != 200:
                raise IngestionError('RERANK_HTTP_ERROR')
            rows = response.json()['results']
            if any(type(row['index']) is not int for row in rows) or sorted(row['index'] for row in rows) != list(range(len(documents))):
                raise ValueError('Invalid rerank indices')
            scores = [row['relevance_score'] for row in rows]
            if any(type(score) not in (int,float) or not math.isfinite(score) for score in scores) or scores != sorted(scores,reverse=True):
                raise ValueError('Invalid rerank scores')
            return [{'index': row['index'], 'score': float(row['relevance_score'])} for row in rows]
        except IngestionError:
            raise
        except httpx.RequestError as exc:
            raise IngestionError('RERANK_UNAVAILABLE') from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise IngestionError('RERANK_INVALID_RESPONSE') from exc
