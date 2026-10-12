from __future__ import annotations

import threading
from pathlib import Path

import numpy as np


class ModelService:
    def __init__(self, cache: Path):
        self.cache = cache
        self.lock = threading.RLock()
        self.encoder = None
        self.reranker = None
        self.encoder_key = None
        self.reranker_key = None
        self.state = {'embedding': '尚未加载', 'reranker': '尚未加载', 'device': '待自检', 'warning': None}

    def device(self, requested: str):
        import torch
        if requested == 'cpu':
            return 'cpu'
        try:
            if torch.cuda.is_available():
                # 真正进行张量运算，不能仅凭 CUDA 驱动存在判断 Blackwell 能否推理。
                probe = torch.ones((32, 32), device='cuda')
                _ = (probe @ probe).sum().item()
                torch.cuda.synchronize()
                return 'cuda'
        except Exception as exc:
            self.state['warning'] = f'GPU 自检失败，已使用 CPU：{type(exc).__name__}'
        return 'cpu'

    def ensure_encoder(self, settings):
        with self.lock:
            key = (settings.embedding.model, settings.embedding.device, settings.embedding.max_tokens)
            if self.encoder_key == key:
                return self.encoder
            from sentence_transformers import SentenceTransformer
            import torch
            torch.set_num_threads(settings.pdf.threads)
            self.state['embedding'] = '正在加载（首次运行需下载）'
            device = self.device(settings.embedding.device)
            local = (self.cache / ('models--' + settings.embedding.model.replace('/', '--')) / 'snapshots').exists()
            try:
                encoder = SentenceTransformer(settings.embedding.model, device=device, cache_folder=str(self.cache), trust_remote_code=False, local_files_only=local)
            except Exception as exc:
                self.state['embedding'] = '加载失败'
                raise ValueError('本地向量模型未就绪，请运行 install.ps1 补齐模型，或检查模型配置') from exc
            if device == 'cuda':
                encoder.half()
            encoder.max_seq_length = settings.embedding.max_tokens
            if encoder.get_sentence_embedding_dimension() != settings.embedding.dimension:
                raise ValueError('向量维度与模型输出不匹配，请修改 embedding.dimension 后重新索引')
            self.encoder, self.encoder_key = encoder, key
            self.state.update(embedding='已加载', device=device)
            return encoder

    def tokenizer(self, settings):
        return self.ensure_encoder(settings).tokenizer

    def encode(self, texts: list[str], settings):
        with self.lock:
            encoder = self.ensure_encoder(settings)
            # 入库片段及查询均不能超限后静默截断。
            if any(len(encoder.tokenizer.encode(text)) > settings.embedding.max_tokens for text in texts):
                raise ValueError('文本超过 embedding.max_tokens，请调整切片参数')
            try:
                return encoder.encode(texts, batch_size=settings.embedding.batch_size, normalize_embeddings=True,
                                      convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
            except Exception as exc:
                import torch
                if str(encoder.device).startswith('cuda') and (isinstance(exc, torch.cuda.OutOfMemoryError) or 'CUDA' in str(exc)):
                    encoder.float().to('cpu')
                    self.state.update(device='cpu', warning='GPU 推理失败，已回退 CPU；可降低 batch_size')
                    torch.cuda.empty_cache()
                    return encoder.encode(texts, batch_size=1, normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)
                raise

    def rerank(self, query: str, texts: list[str], settings) -> list[float]:
        if not settings.reranker.enabled:
            return list(range(len(texts), 0, -1))
        with self.lock:
            key = (settings.reranker.model, settings.reranker.device, settings.reranker.max_tokens)
            if key != self.reranker_key:
                from sentence_transformers import CrossEncoder
                device = self.device(settings.reranker.device)
                self.state['reranker'] = '正在加载（首次运行需下载）'
                self.reranker = CrossEncoder(settings.reranker.model, device=device,
                    max_length=settings.reranker.max_tokens, cache_folder=str(self.cache), trust_remote_code=False,
                    local_files_only=(self.cache / ('models--' + settings.reranker.model.replace('/', '--')) / 'snapshots').exists())
                if device == 'cuda':
                    self.reranker.model.half()
                self.reranker_key = key
                self.state['reranker'] = '已加载'
            try:
                scores = self.reranker.predict([(query, text) for text in texts], batch_size=settings.reranker.batch_size, show_progress_bar=False)
            except Exception as exc:
                import torch
                if next(self.reranker.model.parameters()).is_cuda and (isinstance(exc, torch.cuda.OutOfMemoryError) or 'CUDA' in str(exc)):
                    self.reranker.model.float().to('cpu')
                    self.state['warning'] = '重排序 GPU 推理失败，已回退 CPU'
                    torch.cuda.empty_cache()
                    scores = self.reranker.predict([(query, text) for text in texts], batch_size=1, show_progress_bar=False)
                else:
                    raise
            return np.asarray(scores).reshape(-1).tolist()
