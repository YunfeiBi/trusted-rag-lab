import httpx
import pytest

from backend.app.ingestion import IngestionError
from backend.app.model_client import EmbeddingClient, RerankClient
from backend.app.generation import ChatClient


@pytest.mark.parametrize('payload',[
    {'results':[{'index':0,'relevance_score':0.9},{'index':0,'relevance_score':0.2}]},
    {'results':[{'index':False,'relevance_score':0.9},{'index':1,'relevance_score':0.2}]},
    {'results':[{'index':0,'relevance_score':0.1},{'index':1,'relevance_score':0.9}]},
])
def test_rerank_adapter_rejects_malformed_results(monkeypatch,payload):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request:httpx.Response(200,json=payload))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=transport,**kwargs))
    client = RerankClient()
    client.base,client.key,client.model = 'https://model.invalid/v1','test-only','test-rerank'
    with pytest.raises(IngestionError,match='RERANK_INVALID_RESPONSE'):
        client.rerank('test',['one','two'])


def test_embedding_adapter_rejects_wrong_dimension(monkeypatch):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request:httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0,0.0]}]}))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=transport,**kwargs))
    client = EmbeddingClient()
    client.base,client.key,client.model = 'https://model.invalid/v1','test-only','test-embedding'
    with pytest.raises(IngestionError,match='EMBEDDING_INVALID_RESPONSE'):
        client.embed(['test'])


@pytest.mark.parametrize('finish,content', [('length','{"answerable":false,"claims":[]}'), ('stop','not json'), ('stop','{"answerable":true,"claims":[]}')])
def test_generation_rejects_truncation_and_bad_structure(monkeypatch,finish,content):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':content}}]}))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=transport,**kwargs))
    client = ChatClient()
    client.base,client.key,client.model = 'https://model.invalid/v1','test-only','test-chat'
    with pytest.raises(IngestionError,match='GENERATION_INVALID_RESPONSE'):
        client.generate('test',[])


def test_embedding_rejects_boolean_index(monkeypatch):
    original_client = httpx.Client
    payload = {'data':[{'index':False,'embedding':[1.0]+[0.0]*1023}]}
    transport = httpx.MockTransport(lambda request:httpx.Response(200,json=payload))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=transport,**kwargs))
    client = EmbeddingClient()
    client.base,client.key,client.model = 'https://model.invalid/v1','test-only','test-embedding'
    with pytest.raises(IngestionError,match='EMBEDDING_INVALID_RESPONSE'):
        client.embed(['test'])


@pytest.mark.parametrize('kind', ['embedding','rerank','generation'])
def test_model_timeouts_have_safe_error_codes(monkeypatch,kind):
    original_client = httpx.Client

    def timeout(request):
        raise httpx.ReadTimeout('test-secret-upstream-detail',request=request)

    transport = httpx.MockTransport(timeout)
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original_client(transport=transport,**kwargs))
    client = {'embedding':EmbeddingClient,'rerank':RerankClient,'generation':ChatClient}[kind]()
    client.base,client.key,client.model = 'https://model.invalid/v1','test-only','test-model'
    with pytest.raises(IngestionError) as exc:
        if kind == 'embedding':
            client.embed(['test'])
        elif kind == 'rerank':
            client.rerank('test',['one','two'])
        else:
            client.generate('test',[])
    assert exc.value.code == {'embedding':'EMBEDDING_UNAVAILABLE','rerank':'RERANK_UNAVAILABLE','generation':'GENERATION_UNAVAILABLE'}[kind]
    assert 'test-secret-upstream-detail' not in str(exc.value)
