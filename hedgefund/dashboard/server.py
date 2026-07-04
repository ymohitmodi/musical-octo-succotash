"""Read-only web dashboard (stdlib http.server — zero new dependencies).

Serves a single page + JSON API over the fund's SQLite journal. Binds to
localhost by default; it exposes no write operations of any kind.

Run:  python -m hedgefund.main dashboard  [port]
"""

from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import ROOT, Config
from ..storage import DB

log = logging.getLogger("hedgefund.dashboard")

HTML_PATH = Path(__file__).parent / "dashboard.html"
HEARTBEAT = ROOT / "data" / "heartbeat.json"


def _summary(db: DB) -> dict:
    state = db.get_state()
    nav_rows = db.query(
        "SELECT ts, nav, cash, invested, benchmark_price FROM nav_history "
        "ORDER BY ts ASC LIMIT 5000")
    positions = [dict(r) for r in db.query("SELECT * FROM positions")]
    # last cached price per ticker (read-only: use the price already embedded
    # in nav history era; fall back to avg_cost)
    decisions = [dict(r) for r in db.query(
        "SELECT ts, ticker, action, conviction, target_weight, thesis, status "
        "FROM decisions ORDER BY ts DESC LIMIT 25")]
    trades = [dict(r) for r in db.query(
        "SELECT ts, ticker, side, qty, price FROM trades ORDER BY ts DESC LIMIT 25")]
    events = []
    for r in db.query("SELECT ts, kind, ticker, payload FROM events "
                      "ORDER BY ts DESC LIMIT 40"):
        p = json.loads(r["payload"])
        events.append({"ts": r["ts"], "kind": r["kind"], "ticker": r["ticker"],
                       "brief": json.dumps({k: p[k] for k in list(p)[:3]},
                                           default=str)[:200]})
    genomes = [dict(r) for r in db.query(
        "SELECT agent, generation, fitness, active, created_ts FROM genomes "
        "ORDER BY created_ts DESC LIMIT 40")]
    heartbeat = None
    if HEARTBEAT.exists():
        try:
            hb = json.loads(HEARTBEAT.read_text())
            heartbeat = {"age_sec": round(time.time() - float(hb.get("ts", 0))),
                         "status": hb.get("status")}
        except (json.JSONDecodeError, ValueError):
            pass
    backtest = None
    bt_path = ROOT / "reports" / "backtest_latest.json"
    if bt_path.exists():
        try:
            backtest = json.loads(bt_path.read_text()).get("stats")
        except json.JSONDecodeError:
            pass
    return {
        "generated": time.time(),
        "state": dict(state) if state else None,
        "nav_history": [dict(r) for r in nav_rows],
        "positions": positions,
        "decisions": decisions,
        "trades": trades,
        "events": events,
        "genomes": genomes,
        "heartbeat": heartbeat,
        "backtest": backtest,
    }


def serve(port: int = 8787, host: str = "127.0.0.1") -> None:
    cfg = Config.load()
    db = DB(cfg.data_dir / "fund.db")
    page = HTML_PATH.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                body, ctype = page, "text/html; charset=utf-8"
            elif self.path == "/api/summary":
                body = json.dumps(_summary(db), default=str).encode()
                ctype = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # quiet
            log.debug(fmt, *args)

    log.info("dashboard on http://%s:%d", host, port)
    print(f"Dashboard: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
