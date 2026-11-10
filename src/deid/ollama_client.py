"""Minimal client for a local Ollama server, with an on-disk response cache.

Every call either returns a `ChatResult` or raises `OllamaError` with a `kind`
("timeout", "http_error", "too_long"). Callers count those as failures.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from deid.config import DATA_DIR, OllamaConfig

CACHE_DIR = DATA_DIR / "llm_cache"
# Conservative chars-per-token estimate for the pre-flight length check (English ~4).
_CHARS_PER_TOKEN = 3


class OllamaError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    eval_tokens: int
    done_reason: str
    runtime_s: float
    error: str = ""  # set when the cached call failed (kind of OllamaError)


class OllamaClient:
    def __init__(self, cfg: OllamaConfig | None = None, cache_dir: Path | None = CACHE_DIR):
        self.cfg = cfg or OllamaConfig.from_env()
        self.cache_dir = cache_dir

    def _key(self, payload: dict, tag: str) -> str:
        blob = json.dumps({"payload": payload, "tag": tag}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def chat(
        self,
        system: str,
        user: str,
        *,
        format: dict | str | None = None,
        num_predict: int = 2048,
        temperature: float | None = None,
        cache_tag: str = "",
    ) -> ChatResult:
        """One non-streaming chat call. `cache_tag` distinguishes repeated runs of the same
        prompt (e.g. "run=1") so each repetition is a real, separately cached sample."""
        est_tokens = (len(system) + len(user)) // _CHARS_PER_TOKEN
        if est_tokens + num_predict > self.cfg.num_ctx:
            # Ollama would silently drop the start of the prompt. Fail loudly instead.
            raise OllamaError(
                "too_long", f"~{est_tokens} prompt tokens, num_ctx={self.cfg.num_ctx}"
            )

        options: dict = {"num_ctx": self.cfg.num_ctx, "num_predict": num_predict}
        if temperature is not None:
            options["temperature"] = temperature
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": options,
        }
        if format is not None:
            payload["format"] = format

        path = self.cache_dir / f"{self._key(payload, cache_tag)}.json" if self.cache_dir else None
        if path and path.exists():
            res = ChatResult(**json.loads(path.read_text()))
        else:
            res = self._call(payload)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(asdict(res)))
        if res.error:
            raise OllamaError(res.error, "cached failure")
        return res

    def _call(self, payload: dict) -> ChatResult:
        t0 = time.perf_counter()
        try:
            r = requests.post(f"{self.cfg.url}/api/chat", json=payload, timeout=self.cfg.timeout_s)
        except requests.Timeout:
            return ChatResult("", 0, 0, "", time.perf_counter() - t0, error="timeout")
        except requests.RequestException:
            return ChatResult("", 0, 0, "", time.perf_counter() - t0, error="http_error")
        elapsed = time.perf_counter() - t0
        if r.status_code != 200:
            return ChatResult("", 0, 0, "", elapsed, error="http_error")
        body = r.json()
        return ChatResult(
            content=body.get("message", {}).get("content", ""),
            prompt_tokens=body.get("prompt_eval_count", 0),
            eval_tokens=body.get("eval_count", 0),
            done_reason=body.get("done_reason", ""),
            runtime_s=elapsed,
        )
