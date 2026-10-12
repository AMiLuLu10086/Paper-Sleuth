from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, load_settings, prepare_environment
from .database import Database, now
from .jobs import JobWorker
from .llm import LlmError, missing_supplement, rewrite_query, stream_answer, validate_citations
from .models import ModelService
from .parsing import inspect_pdf
from .retrieval import Retriever, VectorStore

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


class Services:
    def __init__(self, settings):
        self.settings = settings
        prepare_environment(settings)
        self.db = Database(settings.data_path / 'library.sqlite3')
        self.models = ModelService(settings.model_path)
        self.vectors = VectorStore(settings.data_path / 'vectors')
        self.retriever = Retriever(self.db, self.models, self.vectors)
        self.worker = JobWorker(self.db, self.models, self.vectors, settings.data_path)
        self.chat_lock = threading.Lock()
        self.active_chats = set()

    def current_settings(self):
        try:
            settings = load_settings()
        except Exception as exc:
            raise HTTPException(422, f'config.yaml 无效：{exc}') from exc
        if settings.data_path != self.settings.data_path or settings.model_path != self.settings.model_path:
            raise HTTPException(409, '存储或模型缓存路径已修改，请重启服务')
        return settings

    def document_directory(self, document_id):
        return self.settings.data_path / 'documents' / document_id


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = Field(default=None, max_length=1000)


class SearchInput(ChatInput):
    english_query: str | None = Field(default=None, max_length=2000)


def create_app(settings_override=None, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        services = Services(settings_override or load_settings())
        app.state.services = services
        if start_worker:
            services.worker.start()
        try:
            yield
        finally:
            services.worker.stop() if start_worker else None
            # 未结束的后台线程需要继续保存中断状态；不能关闭其正在使用的数据库。
            if not services.worker.thread.is_alive():
                services.vectors.close()
                services.db.close()

    app = FastAPI(title='PaperSleuth 文献研究工作台', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    def get_document(services, document_id):
        document = services.db.document(document_id)
        if not document:
            raise HTTPException(404, '文献不存在')
        return document

    @app.get('/api/health')
    def health(request: Request):
        services = request.app.state.services
        settings = services.current_settings() if settings_override is None else services.settings
        return {'status': 'ok', 'models': services.models.state,
                'llm_configured': bool(os.environ.get(settings.llm.api_key_env, '').strip()),
                'llm_model': settings.llm.model, 'local_processing': True,
                'pending_jobs': services.db.one("SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')")['count']}

    @app.get('/api/settings')
    def settings_view(request: Request):
        services = request.app.state.services
        settings = services.current_settings() if settings_override is None else services.settings
        return {'config': settings.model_dump(), 'index_fingerprint': settings.index_fingerprint,
                'embedding_fingerprint': settings.embedding_fingerprint,
                'llm_configured': bool(os.environ.get(settings.llm.api_key_env, '').strip()), 'config_path': str(ROOT / 'config.yaml')}

    @app.get('/api/documents')
    def documents(request: Request):
        services = request.app.state.services
        jobs = {row['document_id']: row for row in services.db.rows('SELECT * FROM jobs ORDER BY created_at')}
        docs = services.db.documents()
        for document in docs:
            document['job'] = jobs.get(document['id'])
        return docs

    @app.post('/api/documents')
    async def upload_documents(request: Request, files: list[UploadFile] = File(...)):
        services = request.app.state.services
        settings = services.current_settings() if settings_override is None else services.settings
        if len(files) > 100:
            raise HTTPException(413, '每批最多上传 100 个 PDF')
        results = []
        for upload in files:
            name = Path(upload.filename or 'document.pdf').name
            try:
                if not name.lower().endswith('.pdf'):
                    raise ValueError('只支持 PDF 文件')
                content = bytearray()
                while block := await upload.read(1024 * 1024):
                    content.extend(block)
                    if len(content) > settings.pdf.max_file_mb * 1024 * 1024:
                        raise ValueError(f'文件超过 {settings.pdf.max_file_mb} MB 限制')
                if not bytes(content[:1024]).lstrip().startswith(b'%PDF-'):
                    raise ValueError('文件不是有效 PDF')
                digest = hashlib.sha256(content).hexdigest()
                existing = services.db.one('SELECT * FROM documents WHERE sha256=?', (digest,))
                if existing:
                    results.append({'filename': name, 'document_id': existing['id'], 'duplicate': True})
                    continue
                document_id = uuid4().hex
                directory = services.document_directory(document_id)
                directory.mkdir(parents=True, exist_ok=True)
                original = directory / 'original.pdf'
                original.write_bytes(content)
                metadata = await asyncio.to_thread(inspect_pdf, original, settings.pdf.max_pages)
                timestamp = now()
                services.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)', (
                    document_id, digest, name, metadata['title'], 'queued', metadata['pages'], None, None, timestamp, timestamp))
                job = services.db.create_job(document_id, settings)
                results.append({'filename': name, 'document_id': document_id, 'job_id': job['id'], 'duplicate': False})
            except ValueError as exc:
                results.append({'filename': name, 'error': str(exc)})
            finally:
                await upload.close()
        services.worker.notify()
        return {'results': results}

    @app.get('/api/documents/{document_id}')
    def document_view(document_id: str, request: Request):
        return get_document(request.app.state.services, document_id)

    @app.get('/api/documents/{document_id}/file')
    def document_file(document_id: str, request: Request):
        services = request.app.state.services
        document = get_document(services, document_id)
        return FileResponse(services.document_directory(document_id) / 'original.pdf', media_type='application/pdf',
                            filename=document['filename'], content_disposition_type='inline')

    @app.get('/api/documents/{document_id}/content')
    def document_content(document_id: str, request: Request, index_id: str | None = None):
        services = request.app.state.services
        document = get_document(services, document_id)
        chosen = index_id or document['active_index']
        if not chosen:
            latest = services.db.one('SELECT * FROM indexes WHERE document_id=? ORDER BY created_at DESC LIMIT 1', (document_id,))
            chosen = latest['id'] if latest else None
        index = services.db.one('SELECT * FROM indexes WHERE id=? AND document_id=?', (chosen, document_id))
        if not index:
            raise HTTPException(409, '文献尚未完成解析，请等待任务进度')
        parsed_path = services.document_directory(document_id) / 'indexes' / chosen / 'parsed.json'
        if not parsed_path.exists():
            raise HTTPException(409, '文献正在解析，请稍后查看')
        parsed = json.loads(parsed_path.read_text(encoding='utf-8'))
        parsed.update(index_id=chosen, fingerprint=index['fingerprint'])
        for block in parsed['blocks']:
            if block.get('asset'):
                block['asset_url'] = f'/api/documents/{document_id}/assets/{chosen}/{block["asset"]}'
        return parsed

    @app.get('/api/documents/{document_id}/assets/{index_id}/{filename}')
    def document_asset(document_id: str, index_id: str, filename: str, request: Request):
        services = request.app.state.services
        if not services.db.one('SELECT id FROM indexes WHERE id=? AND document_id=?', (index_id, document_id)):
            raise HTTPException(404, '索引不存在')
        if not filename.endswith('.png') or Path(filename).name != filename or '\\' in filename:
            raise HTTPException(400, '无效的图片名称')
        path = services.document_directory(document_id) / 'indexes' / index_id / filename
        if not path.is_file():
            raise HTTPException(404, '图片尚未生成')
        return FileResponse(path, media_type='image/png')

    @app.post('/api/documents/{document_id}/reindex')
    def reindex(document_id: str, request: Request):
        services = request.app.state.services
        get_document(services, document_id)
        settings = services.current_settings() if settings_override is None else services.settings
        job = services.db.create_job(document_id, settings)
        services.worker.notify()
        return job

    @app.get('/api/jobs/{job_id}')
    def job_view(job_id: str, request: Request):
        row = request.app.state.services.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not row:
            raise HTTPException(404, '任务不存在')
        return row

    @app.post('/api/jobs/{job_id}/retry')
    def job_retry(job_id: str, request: Request):
        services = request.app.state.services
        job = services.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job:
            raise HTTPException(404, '任务不存在')
        if job['status'] != 'failed':
            raise HTTPException(409, '只有失败的任务需要重试')
        running = services.db.one("SELECT id FROM jobs WHERE document_id=? AND status IN ('queued','running')", (job['document_id'],))
        if running:
            return services.db.one('SELECT * FROM jobs WHERE id=?', (running['id'],))
        services.db.update_job(job_id, '等待重试', job['progress'], 'queued')
        services.db.execute("UPDATE documents SET status='queued',error=NULL WHERE id=?", (job['document_id'],))
        services.worker.notify()
        return services.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))

    @app.get('/api/chats')
    def chats(request: Request):
        return request.app.state.services.db.rows('SELECT * FROM chats ORDER BY updated_at DESC')

    @app.post('/api/search')
    def search_evidence(body: SearchInput, request: Request):
        # 本地检索可独立验证，不需要把问题发给云端模型。
        services = request.app.state.services
        settings = services.current_settings() if settings_override is None else services.settings
        if not body.question.strip():
            raise HTTPException(422, '请输入检索问题')
        if body.document_ids is not None:
            for document_id in body.document_ids:
                get_document(services, document_id)
        refusal = missing_supplement(body.question, services.db.active_chunks(body.document_ids))
        if refusal:
            return {'sources': [], 'notice': refusal}
        try:
            sources = services.retriever.retrieve(body.question, body.english_query or body.question, body.document_ids, settings)
            return {'sources': sources, 'notice': None}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/chats')
    def new_chat(request: Request):
        return request.app.state.services.db.create_chat()

    @app.get('/api/chats/{chat_id}/messages')
    def chat_messages(chat_id: str, request: Request):
        services = request.app.state.services
        if not services.db.one('SELECT id FROM chats WHERE id=?', (chat_id,)):
            raise HTTPException(404, '对话不存在')
        return services.db.messages(chat_id)

    @app.post('/api/chats/{chat_id}/messages')
    async def send_message(chat_id: str, body: ChatInput, request: Request):
        services = request.app.state.services
        settings = services.current_settings() if settings_override is None else services.settings
        if not services.db.one('SELECT id FROM chats WHERE id=?', (chat_id,)):
            raise HTTPException(404, '对话不存在')
        if not body.question.strip():
            raise HTTPException(422, '请输入问题')
        if not os.environ.get(settings.llm.api_key_env, '').strip():
            raise HTTPException(409, f'请在项目根目录的 .env 中填写 {settings.llm.api_key_env} 后重启服务；文献导入和查看不受影响')
        if body.document_ids is not None:
            for document_id in body.document_ids:
                get_document(services, document_id)
        with services.chat_lock:
            if chat_id in services.active_chats:
                raise HTTPException(409, '当前对话正在生成，请停止或等待完成')
            services.active_chats.add(chat_id)
        history = [m for m in services.db.messages(chat_id) if m['status'] == 'complete']
        services.db.add_message(chat_id, 'user', body.question)
        if not history:
            services.db.execute('UPDATE chats SET title=? WHERE id=?', (body.question[:30], chat_id))
        message_id = services.db.add_message(chat_id, 'assistant', '', status='streaming')

        async def events():
            answer, sources, status = '', [], 'interrupted'
            try:
                yield sse('start', {'message_id': message_id})
                all_chunks = await asyncio.to_thread(services.db.active_chunks, body.document_ids)
                if not all_chunks:
                    answer = '当前选择的文献尚无已完成的索引，没有足够证据回答。请先导入文献，或等待入库完成。'
                else:
                    yield sse('status', {'message': '整理检索问题'})
                    question, english, warning = await rewrite_query(body.question, history, settings)
                    if warning:
                        yield sse('warning', {'message': warning})
                    answer = missing_supplement(question, all_chunks) or ''
                    if not answer:
                        yield sse('status', {'message': '检索文献并核对相关证据'})
                        sources = await asyncio.to_thread(services.retriever.retrieve, question, english, body.document_ids, settings)
                        if await request.is_disconnected():
                            return
                        yield sse('sources', {'sources': sources, 'english_query': english})
                        if not sources:
                            answer = '当前文献中没有足够证据回答这个问题。'
                        else:
                            yield sse('status', {'message': '根据文献生成回答'})
                            async for token in stream_answer(body.question, history, sources, settings):
                                if await request.is_disconnected():
                                    return
                                answer += token
                                yield sse('token', {'text': token})
                if answer and not sources:
                    yield sse('token', {'text': answer})
                answer, warning = validate_citations(answer, sources)
                if warning:
                    yield sse('warning', {'message': warning})
                status = 'complete'
                yield sse('done', {'message_id': message_id, 'content': answer, 'sources': sources})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = 'error'
                error = str(exc) if isinstance(exc, (ValueError, LlmError)) else '处理请求失败，请查看后端日志后重试'
                logging.getLogger(__name__).exception('Chat failed')
                if not answer:
                    answer = error
                yield sse('error', {'message': error, 'content': answer})
            finally:
                services.db.finish_message(message_id, answer, sources, status)
                with services.chat_lock:
                    services.active_chats.discard(chat_id)
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    dist = ROOT / 'frontend' / 'dist'
    if (dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='static-assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, '接口不存在')
        index = dist / 'index.html'
        if index.is_file():
            return FileResponse(index)
        return {'message': '后端已启动；请先构建 frontend，或通过 http://localhost:5173 访问开发网页'}

    return app


app = create_app()
