import pytest

from deid.config import OllamaConfig


def test_defaults(monkeypatch):
    for k in ("DEID_OLLAMA_URL", "DEID_OLLAMA_MODEL", "DEID_OLLAMA_NUM_CTX"):
        monkeypatch.delenv(k, raising=False)
    cfg = OllamaConfig.from_env()
    assert cfg.model == "qwen2.5:7b-instruct"
    assert cfg.num_ctx == 8192


def test_env_override(monkeypatch):
    monkeypatch.setenv("DEID_OLLAMA_MODEL", "llama3.1:8b")
    monkeypatch.setenv("DEID_OLLAMA_NUM_CTX", "16384")
    cfg = OllamaConfig.from_env()
    assert cfg.model == "llama3.1:8b"
    assert cfg.num_ctx == 16384


def test_rejects_remote_url(monkeypatch):
    monkeypatch.setenv("DEID_OLLAMA_URL", "https://api.example.com")
    with pytest.raises(ValueError, match="not local"):
        OllamaConfig.from_env()
