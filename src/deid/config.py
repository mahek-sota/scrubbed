"""Central configuration. Every value can be overridden with an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "article_assets"

# The Ollama endpoint must be local: note text never leaves the machine.
_LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1", "http://[::1]")


@dataclass(frozen=True)
class OllamaConfig:
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    # Ollama's default context window is small and it truncates silently, so set it explicitly.
    num_ctx: int = 8192
    timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> OllamaConfig:
        cfg = cls(
            url=os.environ.get("DEID_OLLAMA_URL", cls.url).rstrip("/"),
            model=os.environ.get("DEID_OLLAMA_MODEL", cls.model),
            num_ctx=int(os.environ.get("DEID_OLLAMA_NUM_CTX", cls.num_ctx)),
            timeout_s=float(os.environ.get("DEID_OLLAMA_TIMEOUT_S", cls.timeout_s)),
        )
        if not cfg.url.startswith(_LOCAL_HOSTS):
            raise ValueError(
                f"DEID_OLLAMA_URL={cfg.url!r} is not local. This project never sends note text "
                "to a remote host."
            )
        return cfg


SEED = int(os.environ.get("DEID_SEED", "1234"))
N_NOTES = int(os.environ.get("DEID_N_NOTES", "200"))
