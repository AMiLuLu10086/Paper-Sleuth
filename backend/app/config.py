from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]


class ServerSettings(BaseModel):
    host: str = '127.0.0.1'
    port: int = 8000


class StorageSettings(BaseModel):
    directory: str = 'data'
    model_cache: str = '.cache/models'


class PdfSettings(BaseModel):
    device: str = 'cpu'
    threads: int = Field(4, ge=1)
    ocr: bool = True
    ocr_language: str = 'en'
    table_structure: bool = True
    image_scale: float = Field(2.0, ge=1, le=4)
    max_file_mb: int = Field(100, ge=1)
    max_pages: int = Field(500, ge=1)


class CleaningSettings(BaseModel):
    margin_ratio: float = Field(.08, ge=0, le=.25)
    repeat_ratio: float = Field(.60, gt=0, le=1)
    min_repeat_pages: int = Field(3, ge=2)
    margin_patterns: list[str] = []


class ChunkSettings(BaseModel):
    max_tokens: int = Field(700, ge=64, le=7000)
    overlap_tokens: int = Field(100, ge=0)

    @model_validator(mode='after')
    def valid_overlap(self):
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError('overlap_tokens 必须小于 max_tokens')
        return self


class EmbeddingSettings(BaseModel):
    model: str = 'BAAI/bge-m3'
    device: str = 'auto'
    batch_size: int = Field(4, ge=1)
    dimension: int = Field(1024, ge=1)
    max_tokens: int = Field(1024, ge=64, le=8192)


class RerankerSettings(BaseModel):
    model: str = 'BAAI/bge-reranker-v2-m3'
    enabled: bool = True
    device: str = 'auto'
    batch_size: int = Field(4, ge=1)
    max_tokens: int = Field(1024, ge=64, le=8192)


class RetrievalSettings(BaseModel):
    dense_top_k: int = Field(20, ge=1, le=100)
    bm25_top_k: int = Field(20, ge=1, le=100)
    candidate_limit: int = Field(40, ge=1, le=200)
    final_top_k: int = Field(8, ge=1, le=30)
    rrf_k: int = Field(60, ge=1)
    rewrite_query: bool = True
    include_references: bool = True


class LlmSettings(BaseModel):
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    api_key_env: str = 'DEEPSEEK_API_KEY'
    thinking: bool = False
    timeout_seconds: int = Field(120, ge=10)
    max_output_tokens: int = Field(3000, ge=256)
    temperature: float = Field(.1, ge=0, le=2)
    history_turns: int = Field(4, ge=0, le=20)


class Settings(BaseModel):
    server: ServerSettings = ServerSettings()
    storage: StorageSettings = StorageSettings()
    pdf: PdfSettings = PdfSettings()
    cleaning: CleaningSettings = CleaningSettings()
    chunking: ChunkSettings = ChunkSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    reranker: RerankerSettings = RerankerSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    llm: LlmSettings = LlmSettings()

    @model_validator(mode='after')
    def valid_lengths(self):
        if self.chunking.max_tokens > self.embedding.max_tokens - 2:
            raise ValueError('embedding.max_tokens 至少比 chunking.max_tokens 大 2')
        return self

    @property
    def data_path(self) -> Path:
        return (ROOT / self.storage.directory).resolve()

    @property
    def model_path(self) -> Path:
        return (ROOT / self.storage.model_cache).resolve()

    @property
    def embedding_fingerprint(self) -> str:
        # 维度相同也不能混用不同模型或不同编码策略。
        payload = {'model': self.embedding.model, 'dimension': self.embedding.dimension,
                   'max_tokens': self.embedding.max_tokens, 'normalize': True, 'encoder': 'sentence-transformers-v1'}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    @property
    def index_fingerprint(self) -> str:
        payload = {key: self.model_dump()[key] for key in ['pdf', 'cleaning', 'chunking', 'embedding']}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or Path(os.environ.get('LITERATURE_CONFIG', ROOT / 'config.yaml'))
    # 使用明确路径，避免从终端的工作目录误读其他项目的 .env。
    # 保留终端/系统环境变量；修改已读取的 .env 后需要重启进程。
    load_dotenv(config_path.parent / '.env', override=False, encoding='utf-8-sig', interpolate=False)
    settings = Settings.model_validate(yaml.safe_load(config_path.read_text(encoding='utf-8')) or {})
    base_url = os.environ.get('DEEPSEEK_BASE_URL', '').strip()
    model = os.environ.get('DEEPSEEK_MODEL', '').strip()
    if base_url:
        settings.llm.base_url = base_url.rstrip('/')
    if model:
        settings.llm.model = model
    return settings


def prepare_environment(settings: Settings) -> None:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.model_path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('HF_HOME', str(settings.model_path))
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
