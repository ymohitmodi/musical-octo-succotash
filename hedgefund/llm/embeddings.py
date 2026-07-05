"""Semantic memory: embeddings-based retrieval of relevant lessons.

Recency-based lesson injection (the default) resurfaces the LAST mistakes;
semantic retrieval resurfaces the RELEVANT ones — when analyzing a levered
retailer, the fund should re-read its levered-retailer scars, not its most
recent airline note. Uses Ollama's /api/embed with any embedding model
(e.g. embeddinggemma, nomic-embed-text); vectors are cached in SQLite.

Fail-open by design: any failure (no embed model, endpoint down) silently
falls back to recency — memory quality degrades, memory never disappears.
"""

from __future__ import annotations

import hashlib
import logging
import math

import requests

from ..storage import DB

log = logging.getLogger("hedgefund.embeddings")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Embedder:
    def __init__(self, db: DB, host: str, model: str, api_key: str = "",
                 timeout: int = 30):
        self.db = db
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, text: str) -> list[float] | None:
        key = ("emb:" + self.model + ":"
               + hashlib.sha256(text.encode()).hexdigest()[:24])
        cached = self.db.cache_get(key)
        if cached:
            return cached
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = requests.post(f"{self.host}/api/embed", headers=headers,
                                 json={"model": self.model, "input": text},
                                 timeout=self.timeout)
            resp.raise_for_status()
            vecs = resp.json().get("embeddings") or []
            vec = vecs[0] if vecs else None
        except Exception as e:  # noqa: BLE001 - fail open to recency fallback
            log.debug("embed failed: %s", e)
            return None
        if vec:
            self.db.cache_set(key, vec, ttl_sec=30 * 86400)
        return vec


def relevant_lessons(db: DB, embedder: Embedder | None, context: str,
                     limit: int = 5) -> str:
    """Top lessons for THIS analysis context; recency fallback on any failure."""
    from ..agents.base import recent_lessons  # avoid cycle at import time
    if embedder is None or not context:
        return recent_lessons(db, limit)
    rows = db.query("SELECT id, ticker, category, lesson FROM lessons "
                    "ORDER BY ts DESC LIMIT 100")
    if not rows:
        return ""
    qvec = embedder.embed(context)
    if qvec is None:
        return recent_lessons(db, limit)
    scored = []
    for r in rows:
        lvec = embedder.embed(r["lesson"])
        if lvec is not None:
            scored.append((cosine(qvec, lvec), r))
    if not scored:
        return recent_lessons(db, limit)
    scored.sort(key=lambda t: t[0], reverse=True)
    lines = [f"- [{r['category']}/{r['ticker']}] {r['lesson']}"
             for _, r in scored[:limit]]
    return ("\n\n===== LESSONS FROM THE FUND'S OWN HISTORY MOST RELEVANT TO THIS "
            "ANALYSIS (do not repeat these mistakes) =====\n" + "\n".join(lines))
