from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.app.cleaning import clean_blocks, normalize_text
from backend.app.config import Settings, load_settings
from backend.app.database import Database, now
from backend.app.llm import missing_supplement, validate_citations
from backend.app.main import create_app
from backend.app.retrieval import Retriever, VectorStore


def pdf_bytes(encrypted=False):
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    if encrypted:
        writer.encrypt('fixture-password')
    writer.write(stream)
    return stream.getvalue()


def test_cleaning_preserves_body_numbers_and_scientific_notation():
    def block(text, page, y, label='text'):
        return {'id': uuid4().hex, 'raw_text': text, 'kind': 'text', 'label': label, 'pages': [page],
            'positions': [{'page': page, 'bbox': [0,y,300,y+10], 'width':595,'height':800}]}
    blocks = [block('Journal of Hazardous Materials 425 (2022)', page, 20) for page in range(1,5)]
    blocks += [block('2', 2, 400), block('150 mL, 2.5 mg/L, Fe3+, SO4\n•−', 2, 400), block('2', 2, 760)]
    cleaned, logs = clean_blocks(blocks, 4, Settings().cleaning)
    assert all(item['removed'] for item in cleaned[:4])
    assert not cleaned[4]['removed']
    assert '2.5 mg/L' in cleaned[5]['text'] and 'SO4•−' in cleaned[5]['text']
    assert cleaned[6]['removed']
    assert normalize_text('acti\u00ad\nvation photo-\ncharge') == 'activation photo-charge'
    assert len(logs) == 6


def test_config_rejects_overlap_and_truncation():
    with pytest.raises(ValueError):
        Settings.model_validate({'chunking': {'max_tokens': 700, 'overlap_tokens': 700}})
    with pytest.raises(ValueError):
        Settings.model_validate({'embedding': {'max_tokens': 600}})


def test_missing_supplement_does_not_treat_a_mention_as_a_table():
    mention = [{'kind':'text','text':'The water quality is shown in Table S1.'}]
    assert missing_supplement('Table S1 的参数是多少？', mention)
    assert missing_supplement('补充材料表 S1 有什么数值？', mention)
    assert missing_supplement('Table S1 的参数是多少？', [{'kind':'table','text':'Table S1 Water quality'}]) is None


def test_invalid_citation_is_flagged():
    answer, warning = validate_citations('去除率超过 99%。[S1] 其他结果。[S99]', [{'source_id':'S1'}])
    assert '[S1]' in answer and '[S99]' not in answer and warning


@pytest.fixture
def client(tmp_path):
    config = load_settings()
    config.storage.directory = str(tmp_path / 'library')
    # 模型不会在接口测试中加载，但缓存路径仍使用已下载的真实模型。
    with TestClient(create_app(config, start_worker=False)) as client:
        yield client


def test_batch_duplicate_invalid_and_encrypted_uploads(client):
    content = pdf_bytes()
    response = client.post('/api/documents', files=[
        ('files', ('valid.pdf', content, 'application/pdf')),
        ('files', ('duplicate.pdf', content, 'application/pdf')),
        ('files', ('bad.pdf', b'broken pdf', 'application/pdf')),
        ('files', ('locked.pdf', pdf_bytes(True), 'application/pdf'))])
    results = response.json()['results']
    assert response.status_code == 200 and results[0]['document_id']
    assert results[1]['duplicate']
    assert results[2]['error'] and results[3]['error']
    assert len(client.get('/api/documents').json()) == 1
    assert client.get('/api/documents/' + results[0]['document_id'] + '/file').content == content


def test_missing_key_allows_import_and_blocks_chat(client, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    assert client.get('/api/health').json()['llm_configured'] is False
    assert client.post('/api/documents', files={'files': ('valid.pdf', pdf_bytes(), 'application/pdf')}).status_code == 200
    chat = client.post('/api/chats').json()
    response = client.post('/api/chats/' + chat['id'] + '/messages', json={'question':'测试'})
    assert response.status_code == 409
    assert client.get('/api/chats/' + chat['id'] + '/messages').json() == []


def create_index(db, document_id, index_id, config):
    db.execute('INSERT INTO indexes VALUES(?,?,?,?,?,?,?)', (index_id, document_id, config.index_fingerprint,
        config.embedding_fingerprint, json.dumps(config.model_dump()), 'ready', now()))


def test_scope_filter_and_inactive_generation_are_excluded(tmp_path):
    config = Settings(embedding={'dimension':3}, reranker={'enabled':False})
    db = Database(tmp_path / 'db.sqlite')
    vectors = VectorStore(tmp_path / 'vectors')
    vectors.ensure_collection(config)
    docs, chunks = [], []
    for i in range(2):
        document_id, index_id = uuid4().hex, uuid4().hex
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)', (document_id, str(i), str(i)+'.pdf','Paper '+str(i),'ready',1,index_id,None,now(),now()))
        create_index(db, document_id, index_id, config)
        docs.append(document_id)
        chunks.append({'id':str(uuid4()),'document_id':document_id,'index_id':index_id,'text':'atrazine degradation evidence '+str(i),
                       'kind':'text','section':'Results','pages':[1],'boxes':[],'block_ids':['b1'],'token_count':8})
    old = {**chunks[0], 'id':str(uuid4()), 'index_id':uuid4().hex, 'text':'inactive dangerous evidence'}
    create_index(db, docs[0], old['index_id'], config)
    db.store_chunks([*chunks, old])
    vectors.upsert([*chunks, old], np.array([[1.,0.,0.],[.8,.2,0.],[1.,0.,0.]]), config)
    class StubModels:
        def encode(self, texts, settings): return np.array([[1.,0.,0.] for _ in texts])
        def rerank(self, query, texts, settings): return [1.] * len(texts)
    retriever = Retriever(db, StubModels(), vectors)
    results = retriever.retrieve('atrazine', 'atrazine', [docs[1]], config)
    assert results and all(result['document_id'] == docs[1] for result in results)
    assert old['id'] not in [r['id'] for r in retriever.retrieve('atrazine','atrazine',None,config)]
    assert retriever.retrieve('atrazine','atrazine',[],config) == []
    config.embedding.model = 'different/model'
    with pytest.raises(ValueError, match='不一致'):
        retriever.retrieve('atrazine','atrazine',None,config)
    vectors.close(); db.close()


def test_recovery_persistence_and_atomic_publication(tmp_path):
    path = tmp_path/'db.sqlite'
    db = Database(path)
    config = Settings()
    document_id = uuid4().hex
    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)', (document_id,'hash','test.pdf','Test','ready',1,'old-index',None,now(),now()))
    job = db.create_job(document_id, config)
    db.update_job(job['id'], '解析中', .3)
    db.recover()
    assert db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status'] == 'queued'
    assert db.document(document_id)['active_index'] == 'old-index'
    db.publish(document_id, job['index_id'], 'New title', 2)
    db.close()
    db = Database(path)
    assert db.document(document_id)['active_index'] == job['index_id']
    assert db.document(document_id)['pages'] == 2
    db.close()


def test_sse_streaming_and_persisted_sources(client, monkeypatch):
    import backend.app.main as main
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-placeholder')
    services = client.app.state.services
    services.db.active_chunks = lambda ids: [{'kind':'text','text':'ATZ removal exceeds 99%'}]
    source = {'id':str(uuid4()),'source_id':'S1','document_id':'fixture','index_id':'fixture',
              'title':'Fixture','text':'ATZ removal exceeds 99%','pages':[2],'kind':'text','section':'Results','boxes':[],'block_ids':['b1']}
    services.retriever.retrieve = lambda *args: [source]
    async def rewrite(*args): return '问题','question',None
    async def answer(*args):
        yield '去除率超过 '
        yield '99%。[S1]'
    monkeypatch.setattr(main, 'rewrite_query', rewrite)
    monkeypatch.setattr(main, 'stream_answer', answer)
    chat = client.post('/api/chats').json()
    response = client.post('/api/chats/'+chat['id']+'/messages',json={'question':'去除率是多少？'})
    assert response.status_code == 200 and 'event: sources' in response.text and 'event: done' in response.text
    messages = client.get('/api/chats/'+chat['id']+'/messages').json()
    assert len(messages) == 2 and messages[1]['content'] == '去除率超过 99%。[S1]'
    assert messages[1]['sources'][0]['pages'] == [2]
    assert messages[1]['status'] == 'complete'


@pytest.mark.parametrize('failure', ['rate_limit', 'timeout', 'early_disconnect'])
def test_llm_failures_keep_safe_messages(failure, monkeypatch):
    import asyncio
    import httpx
    from contextlib import asynccontextmanager
    import backend.app.llm as llm
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-secret-never-in-errors')
    class Response:
        status_code = 429 if failure == 'rate_limit' else 200
        async def aread(self): return b''
        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices':[{'delta':{'content':'partial'}}]})
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            if failure == 'timeout':
                raise httpx.ReadTimeout('test timeout')
            yield Response()
    async def no_delay(*args): pass
    monkeypatch.setattr(llm.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(llm.asyncio, 'sleep', no_delay)
    async def run():
        return [token async for token in llm.stream_answer('question',[],[],Settings())]
    with pytest.raises(llm.LlmError) as error:
        asyncio.run(run())
    assert 'test-secret-never-in-errors' not in str(error.value)
    assert {'rate_limit':'限流','timeout':'超时','early_disconnect':'提前结束'}[failure] in str(error.value)
