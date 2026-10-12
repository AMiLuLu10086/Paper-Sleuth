from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .chunking import make_chunks
from .cleaning import clean_blocks
from .config import Settings
from .database import now
from .parsing import parse_pdf

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, db, models, vectors, data_path: Path):
        self.db, self.models, self.vectors, self.data_path = db, models, vectors, data_path
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, name='pdf-ingestion', daemon=True)

    def start(self):
        self.db.recover()
        self.thread.start()
        self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        self.thread.join(timeout=5)

    def notify(self):
        self.wake.set()

    def loop(self):
        while not self.stop_event.is_set():
            job = self.db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if not job:
                self.wake.wait(timeout=2)
                self.wake.clear()
                continue
            self.process(job)

    def process(self, job):
        document_id, index_id, job_id = job['document_id'], job['index_id'], job['id']
        index = self.db.one('SELECT * FROM indexes WHERE id=?', (index_id,))
        settings = Settings.model_validate_json(index['config_json'])
        source = self.data_path / 'documents' / document_id / 'original.pdf'
        output = self.data_path / 'documents' / document_id / 'indexes' / index_id
        self.db.execute("UPDATE documents SET status='processing',error=NULL,updated_at=? WHERE id=?", (now(), document_id))
        update = lambda stage, value: self.db.update_job(job_id, stage, value)
        try:
            parsed_file = output / 'parsed.json'
            if parsed_file.exists():
                parsed = json.loads(parsed_file.read_text(encoding='utf-8'))
                update('恢复已保存的解析结果', .5)
            else:
                parsed = parse_pdf(source, output, settings, update)
                parsed['blocks'], parsed['cleaning_log'] = clean_blocks(parsed['blocks'], parsed['pages'], settings.cleaning)
                pending = output / 'parsed.pending.json'
                pending.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding='utf-8')
                pending.replace(parsed_file)
            update('加载本地向量模型', .55)
            chunks = make_chunks(parsed['blocks'], parsed['title'], document_id, index_id,
                                 self.models.tokenizer(settings), settings.chunking)
            if not chunks:
                raise ValueError('没有提取到可检索内容，请检查 PDF 文本层或启用 OCR')
            self.db.store_chunks(chunks)
            self.vectors.ensure_collection(settings)
            total = len(chunks)
            for start in range(0, total, settings.embedding.batch_size):
                if self.stop_event.is_set():
                    self.db.update_job(job_id, '服务停止，启动后继续', .6, 'queued')
                    return
                batch = chunks[start:start+settings.embedding.batch_size]
                embeddings = self.models.encode([chunk['text'] for chunk in batch], settings)
                self.vectors.upsert(batch, embeddings, settings)
                update(f'向量化 {min(start+len(batch),total)} / {total} 个片段', .6+.35*min(start+len(batch),total)/total)
            self.db.publish(document_id, index_id, parsed['title'], parsed['pages'])
            self.db.update_job(job_id, '处理完成', 1, 'complete')
        except Exception as exc:
            logger.exception('Document processing failed: %s', document_id)
            # API 与日志不回显任何密钥；失败不会切换旧的 active_index。
            message = str(exc)[:500] or type(exc).__name__
            self.db.update_job(job_id, '处理失败，可重试', job['progress'], 'failed', message)
            self.db.execute("UPDATE indexes SET status='failed' WHERE id=?", (index_id,))
            self.db.execute("UPDATE documents SET status='error',error=?,updated_at=? WHERE id=?", (message, now(), document_id))
