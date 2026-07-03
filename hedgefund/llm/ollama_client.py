"""Resilient Ollama client (local daemon or ollama.com cloud).

Dark-factory requirement: an LLM outage must degrade, never crash, the
pipeline. So this client implements:
  * a fallback chain of models (cloud-first, local last resort),
  * per-model retry with exponential backoff,
  * a circuit breaker per model (skip models that keep failing),
  * strict-JSON helper that repairs/retries malformed outputs.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

log = logging.getLogger("hedgefund.llm")


class LLMError(RuntimeError):
    pass


class _Breaker:
    """Simple circuit breaker: open after N consecutive failures, retry later."""

    def __init__(self, threshold: int = 3, cooldown: float = 600):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures = 0
        self.opened_at = 0.0

    def available(self) -> bool:
        return self.failures < self.threshold or time.time() - self.opened_at > self.cooldown

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.time()


class LLMClient:
    def __init__(self, host: str, models: list[str], api_key: str = "",
                 timeout: int = 240, max_retries: int = 3):
        self.host = host.rstrip("/")
        self.models = models
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._breakers: dict[str, _Breaker] = {m: _Breaker() for m in models}

    # ------------------------------------------------------------------ chat
    def chat(self, messages: list[dict], *, temperature: float = 0.3,
             model: str | None = None) -> str:
        """Return assistant text, walking the fallback chain on failure."""
        chain = [model] if model else self.models
        last_err: Exception | None = None
        for m in chain:
            breaker = self._breakers.setdefault(m, _Breaker())
            if not breaker.available():
                continue
            for attempt in range(self.max_retries):
                try:
                    text = self._call(m, messages, temperature)
                    breaker.record(True)
                    return text
                except Exception as e:  # noqa: BLE001 - resilience boundary
                    last_err = e
                    log.warning("model %s attempt %d failed: %s", m, attempt + 1, e)
                    time.sleep(min(2 ** attempt * 2, 30))
            breaker.record(False)
        raise LLMError(f"all models failed; last error: {last_err}")

    def _call(self, model: str, messages: list[dict], temperature: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = requests.post(
            f"{self.host}/api/chat",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message") or {}).get("content", "")
        if not content.strip():
            raise LLMError(f"empty response from {model}")
        return content

    # ------------------------------------------------------------- json mode
    def chat_json(self, system: str, user: str, *, schema_hint: str = "",
                  temperature: float = 0.2, retries: int = 2) -> dict:
        """Chat and parse a JSON object; retries with a repair prompt on failure."""
        sys_msg = system + "\n\nRespond with a single valid JSON object only. " \
                           "No markdown fences, no commentary." + \
                  (f"\nJSON shape: {schema_hint}" if schema_hint else "")
        messages = [{"role": "system", "content": sys_msg},
                    {"role": "user", "content": user}]
        for attempt in range(retries + 1):
            text = self.chat(messages, temperature=temperature)
            parsed = self._extract_json(text)
            if parsed is not None:
                return parsed
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content":
                             "That was not valid JSON. Output ONLY the corrected JSON object."})
        raise LLMError("could not obtain valid JSON from model")

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        text = text.strip()
        # strip <think> blocks and markdown fences
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
        if fence:
            text = fence.group(1)
        # locate outermost braces
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------- utilities
    def health(self) -> dict[str, Any]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=10,
                             headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
            r.raise_for_status()
            return {"ok": True, "models_installed": [m["name"] for m in r.json().get("models", [])]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
