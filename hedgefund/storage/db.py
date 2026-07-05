"""SQLite persistence: the fund's single source of truth.

Everything the system knows, decides, and does is journaled here. The
evolution engine and daily reports read exclusively from this database —
"learn from the journal, not from memory" (constitution P8).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (               -- append-only journal
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                            -- e.g. screen, analysis, decision, trade, error, veto
    ticker TEXT,
    payload TEXT NOT NULL                          -- JSON
);
CREATE TABLE IF NOT EXISTS candidates (            -- screener output
    ticker TEXT NOT NULL,
    ts REAL NOT NULL,
    sector TEXT,
    score REAL NOT NULL,
    metrics TEXT NOT NULL,                         -- JSON valuation/quality metrics
    PRIMARY KEY (ticker, ts)
);
CREATE TABLE IF NOT EXISTS analyses (              -- one row per agent per deep-dive
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    agent TEXT NOT NULL,
    genome_id INTEGER,
    verdict TEXT NOT NULL,                         -- bullish | neutral | bearish
    confidence REAL NOT NULL,                      -- 0..100
    report TEXT NOT NULL                           -- JSON full structured report
);
CREATE TABLE IF NOT EXISTS decisions (             -- PM decisions after debate + constitution
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,                          -- buy | sell | hold | reject
    conviction REAL NOT NULL,
    target_weight REAL NOT NULL,
    thesis TEXT NOT NULL,
    constitution_review TEXT NOT NULL,             -- JSON critic output
    genome_ids TEXT NOT NULL,                      -- JSON {agent: genome_id}
    status TEXT NOT NULL DEFAULT 'open'            -- open | closed
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decision_id INTEGER,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,                            -- buy | sell
    qty REAL NOT NULL,
    price REAL NOT NULL,
    slippage REAL NOT NULL DEFAULT 0,
    commission REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    sector TEXT,
    qty REAL NOT NULL,
    avg_cost REAL NOT NULL,
    opened_ts REAL NOT NULL,
    decision_id INTEGER
);
CREATE TABLE IF NOT EXISTS nav_history (
    ts REAL PRIMARY KEY,
    nav REAL NOT NULL,
    cash REAL NOT NULL,
    invested REAL NOT NULL,
    benchmark_price REAL
);
CREATE TABLE IF NOT EXISTS genomes (               -- evolving agent parameter sets
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    params TEXT NOT NULL,                          -- JSON
    parent_id INTEGER,
    generation INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    fitness REAL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (                 -- 24/7 exploration output
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,                          -- exploration channel name
    note TEXT,
    score REAL                                     -- screener composite, when scored
);
CREATE TABLE IF NOT EXISTS watchlist (             -- quality names stalking a price
    ticker TEXT PRIMARY KEY,
    added_ts REAL NOT NULL,
    target_entry REAL NOT NULL,                    -- committee-set trigger price
    thesis TEXT NOT NULL,
    source TEXT,
    sector TEXT,
    triggered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lessons (               -- institutional memory (post-mortems)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    decision_id INTEGER,
    outcome_pct REAL,
    category TEXT NOT NULL,                        -- process | thesis | timing | luck
    lesson TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fund_state (            -- single-row operational state
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    halted_until REAL NOT NULL DEFAULT 0,
    halt_reason TEXT,
    peak_nav REAL NOT NULL DEFAULT 0,
    day_open_nav REAL NOT NULL DEFAULT 0,
    day_open_date TEXT
);
"""


class DB:
    def __init__(self, path: Path | str):
        self._path = str(path)
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return c

    # ---- journal -----------------------------------------------------------
    def log_event(self, kind: str, payload: dict, ticker: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO events (ts, kind, ticker, payload) VALUES (?,?,?,?)",
                (time.time(), kind, ticker, json.dumps(payload, default=str)),
            )

    # ---- cache ---------------------------------------------------------------
    def cache_get(self, key: str) -> Any | None:
        row = self.conn().execute(
            "SELECT value FROM kv_cache WHERE key=? AND expires_at>?", (key, time.time())
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_set(self, key: str, value: Any, ttl_sec: float) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO kv_cache (key, value, expires_at) VALUES (?,?,?)",
                (key, json.dumps(value, default=str), time.time() + ttl_sec),
            )

    # ---- fund state ------------------------------------------------------------
    def get_state(self) -> sqlite3.Row | None:
        return self.conn().execute("SELECT * FROM fund_state WHERE id=1").fetchone()

    def init_state(self, cash: float) -> None:
        if self.get_state() is None:
            with self.conn() as c:
                c.execute(
                    "INSERT INTO fund_state (id, cash, peak_nav, day_open_nav) VALUES (1,?,?,?)",
                    (cash, cash, cash),
                )

    def update_state(self, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE fund_state SET {cols} WHERE id=1", tuple(fields.values()))

    # ---- generic helpers ----------------------------------------------------
    def insert(self, table: str, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(fields.values())
            )
            return int(cur.lastrowid)

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, args).fetchall()
