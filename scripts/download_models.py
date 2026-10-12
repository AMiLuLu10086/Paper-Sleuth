"""Download only the local inference weights needed by PaperSleuth."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings, prepare_environment

settings = load_settings()
prepare_environment(settings)

from huggingface_hub import snapshot_download

for model in [settings.embedding.model, settings.reranker.model] if settings.reranker.enabled else [settings.embedding.model]:
    print(f'Downloading {model}', flush=True)
    path = snapshot_download(model, cache_dir=str(settings.model_path),
        allow_patterns=['*.json', '*.txt', '*.model', '*.safetensors', 'pytorch_model*.bin'], max_workers=3)
    print(f'Ready: {model} at {path}', flush=True)

print('Local embedding and reranker model downloads complete.', flush=True)

from docling.utils.model_downloader import download_models
print('Downloading PDF layout, table structure and OCR models', flush=True)
download_models(output_dir=settings.model_path / 'docling', progress=True,
    with_code_formula=False, with_picture_classifier=False)
print('PDF processing model downloads complete.', flush=True)
