from __future__ import annotations

import re
import threading
import unicodedata
from collections import defaultdict

import numpy as np
from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi


def lexical_tokens(text: str) -> list[str]:
    text = unicodedata.normalize('NFKC', text).lower()
    return re.findall(r'[a-z0-9]+(?:[+−-][a-z0-9]+)*|[\u4e00-\u9fff]', text)


class VectorStore:
    def __init__(self, path):
        self.client = QdrantClient(path=str(path))
        self.lock = threading.RLock()

    @staticmethod
    def collection(settings):
        return 'literature_' + settings.embedding_fingerprint

    def ensure_collection(self, settings):
        name = self.collection(settings)
        with self.lock:
            if not self.client.collection_exists(name):
                self.client.create_collection(name, vectors_config=models.VectorParams(
                    size=settings.embedding.dimension, distance=models.Distance.COSINE))

    def upsert(self, chunks, embeddings, settings):
        with self.lock:
            self.client.upsert(self.collection(settings), points=[models.PointStruct(
                id=chunk['id'], vector=embedding.tolist(), payload={'document_id': chunk['document_id'], 'index_id': chunk['index_id']})
                for chunk, embedding in zip(chunks, embeddings)])

    def search(self, vector, index_ids, settings):
        with self.lock:
            name = self.collection(settings)
            if not self.client.collection_exists(name):
                return []
            return self.client.query_points(name, query=vector.tolist(), limit=settings.retrieval.dense_top_k,
                query_filter=models.Filter(must=[models.FieldCondition(key='index_id', match=models.MatchAny(any=index_ids))])).points

    def close(self):
        self.client.close()


class Retriever:
    def __init__(self, db, models_service, vectors):
        self.db, self.models, self.vectors = db, models_service, vectors
        self.cache_lock = threading.RLock()
        self.bm25_cache = {}

    def retrieve(self, query: str, english_query: str, document_ids, settings):
        chunks = self.db.active_chunks(document_ids)
        if not settings.retrieval.include_references:
            chunks = [c for c in chunks if not re.search(r'\breferences\b', c['section'], re.I)]
        if not chunks:
            return []
        index_ids = sorted(set(c['index_id'] for c in chunks))
        fingerprints = self.db.rows('SELECT embedding_fingerprint FROM indexes WHERE id IN (' + ','.join('?' for _ in index_ids) + ')', index_ids)
        if any(row['embedding_fingerprint'] != settings.embedding_fingerprint for row in fingerprints):
            raise ValueError('所选文献的向量模型与当前配置不一致，请先重新索引，不能混用不同模型的向量')
        by_id = {chunk['id']: chunk for chunk in chunks}
        ranks = []
        queries = list(dict.fromkeys([query, english_query]))
        for vector in self.models.encode(queries, settings):
            ranks.append([str(point.id) for point in self.vectors.search(vector, index_ids, settings) if str(point.id) in by_id])
        cache_key = tuple(index_ids)
        with self.cache_lock:
            if cache_key not in self.bm25_cache:
                # 索引版本变化自动重建；缓存不保存到不可信 pickle。
                corpus = [lexical_tokens(chunk['text']) or ['_empty_'] for chunk in chunks]
                self.bm25_cache = {cache_key: (BM25Okapi(corpus), [chunk['id'] for chunk in chunks])}
            bm25, lexical_ids = self.bm25_cache[cache_key]
        scores = bm25.get_scores(lexical_tokens(english_query))
        ranks.append([lexical_ids[i] for i in np.argsort(scores)[::-1][:settings.retrieval.bm25_top_k] if scores[i] > 0])
        fused = defaultdict(float)
        for ranking in ranks:
            for rank, chunk_id in enumerate(ranking, 1):
                fused[chunk_id] += 1 / (settings.retrieval.rrf_k + rank)
        candidates = [by_id[chunk_id] for chunk_id in sorted(fused, key=fused.get, reverse=True)[:settings.retrieval.candidate_limit]]
        if not candidates:
            return []
        relevance = self.models.rerank(query, [c['text'] for c in candidates], settings)
        chosen = sorted(zip(candidates, relevance), key=lambda pair: pair[1], reverse=True)[:settings.retrieval.final_top_k]
        return [{**chunk, 'source_id': f'S{i+1}', 'score': float(score)} for i, (chunk, score) in enumerate(chosen)]
