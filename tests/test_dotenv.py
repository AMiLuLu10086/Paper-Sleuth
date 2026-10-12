import os

from fastapi.testclient import TestClient

from backend.app.config import load_settings
from backend.app.llm import endpoint, headers
from backend.app.main import create_app


def clear_connection_environment(monkeypatch):
    for name in ['DEEPSEEK_BASE_URL', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL']:
        # 记录 dotenv 随后写入的键，确保测试结束后恢复原有进程环境。
        monkeypatch.setenv(name, 'temporary-test-value')
        monkeypatch.delenv(name)


def write_config(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('llm:\n  base_url: https://yaml.example.test\n  model: yaml-model\n', encoding='utf-8')
    return path


def test_dotenv_file_configures_requests_without_exposing_key(tmp_path, monkeypatch):
    clear_connection_environment(monkeypatch)
    config_path = write_config(tmp_path)
    secret = 'fake-test-key-${literal}-never-a-real-secret'
    (tmp_path / '.env').write_text(
        '\ufeffDEEPSEEK_BASE_URL=https://file.example.test/v1/\n'
        f'DEEPSEEK_API_KEY="{secret}"\n'
        'DEEPSEEK_MODEL=file-model\n', encoding='utf-8')
    settings = load_settings(config_path)
    assert endpoint(settings) == 'https://file.example.test/v1/chat/completions'
    assert settings.llm.model == 'file-model'
    assert headers(settings)['Authorization'] == 'Bearer ' + secret
    assert secret not in settings.model_dump_json()
    settings.storage.directory = str(tmp_path / 'data')
    with TestClient(create_app(settings, start_worker=False)) as client:
        health = client.get('/api/health')
        response = client.get('/api/settings')
        assert health.json()['llm_configured'] is True
        assert secret not in health.text and secret not in response.text
        assert response.json()['config']['llm']['base_url'] == 'https://file.example.test/v1'


def test_process_environment_takes_priority_over_dotenv(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    (tmp_path / '.env').write_text('DEEPSEEK_BASE_URL=https://file.example.test\nDEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=file-model\n', encoding='utf-8')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://process.example.test')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'process-key')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'process-model')
    settings = load_settings(config_path)
    assert settings.llm.base_url == 'https://process.example.test'
    assert settings.llm.model == 'process-model'
    assert headers(settings)['Authorization'] == 'Bearer process-key'


def test_missing_or_blank_dotenv_keeps_yaml_defaults(tmp_path, monkeypatch):
    clear_connection_environment(monkeypatch)
    config_path = write_config(tmp_path)
    settings = load_settings(config_path)
    assert settings.llm.base_url == 'https://yaml.example.test'
    (tmp_path / '.env').write_text('DEEPSEEK_BASE_URL=\nDEEPSEEK_API_KEY=\nDEEPSEEK_MODEL=\n', encoding='utf-8')
    settings = load_settings(config_path)
    assert settings.llm.base_url == 'https://yaml.example.test' and settings.llm.model == 'yaml-model'
    assert not os.environ['DEEPSEEK_API_KEY']
