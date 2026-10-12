from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
            title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', pages INTEGER DEFAULT 0,
            active_index TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS indexes (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
            fingerprint TEXT NOT NULL, embedding_fingerprint TEXT NOT NULL,
            config_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
            index_id TEXT NOT NULL REFERENCES indexes(id), status TEXT NOT NULL,
            stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
            index_id TEXT NOT NULL REFERENCES indexes(id), ordinal INTEGER NOT NULL,
            text TEXT NOT NULL, kind TEXT NOT NULL, section TEXT NOT NULL,
            pages_json TEXT NOT NULL, boxes_json TEXT NOT NULL, block_ids_json TEXT NOT NULL,
            token_count INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS chunks_index ON chunks(index_id, ordinal);
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id),
            role TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'complete', created_at TEXT NOT NULL);
        ''')
        self.connection.commit()

    def rows(self, sql: str, parameters=()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, parameters).fetchall()]

    def one(self, sql: str, parameters=()) -> dict | None:
        rows = self.rows(sql, parameters)
        return rows[0] if rows else None

    def execute(self, sql: str, parameters=()):
        with self.lock:
            self.connection.execute(sql, parameters)
            self.connection.commit()

    def documents(self):
        return self.rows('''SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.index_id=d.active_index)
            AS chunk_count, (SELECT id FROM jobs j WHERE j.document_id=d.id ORDER BY created_at DESC LIMIT 1)
            AS job_id FROM documents d ORDER BY d.created_at DESC''')

    def document(self, document_id: str):
        return self.one('SELECT * FROM documents WHERE id=?', (document_id,))

    def create_job(self, document_id: str, settings) -> dict:
        with self.lock:
            running = self.one("SELECT * FROM jobs WHERE document_id=? AND status IN ('queued','running')", (document_id,))
            if running:
                return running
            index_id, job_id, timestamp = uuid4().hex, uuid4().hex, now()
            self.connection.execute('INSERT INTO indexes VALUES(?,?,?,?,?,?,?)', (
                index_id, document_id, settings.index_fingerprint, settings.embedding_fingerprint,
                json.dumps(settings.model_dump(), ensure_ascii=False), 'processing', timestamp))
            self.connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (
                job_id, document_id, index_id, 'queued', '等待处理', 0, None, timestamp, timestamp))
            self.connection.execute("UPDATE documents SET status='queued',error=NULL,updated_at=? WHERE id=?", (timestamp, document_id))
            self.connection.commit()
            return self.one('SELECT * FROM jobs WHERE id=?', (job_id,))

    def update_job(self, job_id: str, stage: str, progress: float, status='running', error=None):
        self.execute('UPDATE jobs SET stage=?,progress=?,status=?,error=?,updated_at=? WHERE id=?',
                     (stage, progress, status, error, now(), job_id))

    def store_chunks(self, chunks: list[dict]):
        with self.lock:
            self.connection.executemany('INSERT OR REPLACE INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)', [
                (c['id'], c['document_id'], c['index_id'], i, c['text'], c['kind'], c['section'],
                 json.dumps(c['pages']), json.dumps(c['boxes']), json.dumps(c['block_ids']), c['token_count'])
                for i, c in enumerate(chunks)])
            self.connection.commit()

    def publish(self, document_id: str, index_id: str, title: str, pages: int):
        with self.lock:
            self.connection.execute("UPDATE indexes SET status='ready' WHERE id=?", (index_id,))
            self.connection.execute("UPDATE documents SET active_index=?,status='ready',title=?,pages=?,error=NULL,updated_at=? WHERE id=?",
                                    (index_id, title, pages, now(), document_id))
            self.connection.commit()

    def active_chunks(self, document_ids: list[str] | None = None) -> list[dict]:
        if document_ids == []:
            return []
        sql = 'SELECT c.*,d.title FROM chunks c JOIN documents d ON c.index_id=d.active_index'
        parameters = ()
        if document_ids is not None:
            sql += ' WHERE d.id IN (' + ','.join('?' for _ in document_ids) + ')'
            parameters = tuple(document_ids)
        rows = self.rows(sql + ' ORDER BY c.document_id,c.ordinal', parameters)
        for row in rows:
            for key in ['pages', 'boxes', 'block_ids']:
                row[key] = json.loads(row.pop(key + '_json'))
        return rows

    def recover(self):
        self.execute("UPDATE jobs SET status='queued',stage='上次处理已中断，等待恢复' WHERE status='running'")
        self.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")

    def create_chat(self, title='新对话'):
        chat_id, timestamp = uuid4().hex, now()
        self.execute('INSERT INTO chats VALUES(?,?,?,?)', (chat_id, title, timestamp, timestamp))
        return self.one('SELECT * FROM chats WHERE id=?', (chat_id,))

    def messages(self, chat_id: str):
        rows = self.rows('SELECT * FROM messages WHERE chat_id=? ORDER BY created_at', (chat_id,))
        for row in rows:
            row['sources'] = json.loads(row.pop('sources_json'))
        return rows

    def add_message(self, chat_id: str, role: str, content: str, status='complete', sources=None):
        message_id = uuid4().hex
        self.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (
            message_id, chat_id, role, content, json.dumps(sources or [], ensure_ascii=False), status, now()))
        self.execute('UPDATE chats SET updated_at=? WHERE id=?', (now(), chat_id))
        return message_id

    def finish_message(self, message_id: str, content: str, sources: list[dict], status: str):
        self.execute('UPDATE messages SET content=?,sources_json=?,status=? WHERE id=?',
                     (content, json.dumps(sources, ensure_ascii=False), status, message_id))

    def close(self):
        self.connection.close()
