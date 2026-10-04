"""Preflight self-diagnosis: `python -m hedgefund.main doctor`.

Run this on the machine that will host the fund. It checks every external
dependency the 24/7 loop needs and prints PASS / WARN / FAIL per check with
a fix hint. Exit code 0 = ready to run, 1 = something is broken.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass

from .config import ROOT, Config


@dataclass
class Check:
    name: str
    status: str          # PASS | WARN | FAIL
    detail: str
    fix: str = ""


def _check_config(cfg: Config) -> list[Check]:
    out = []
    ua = str(cfg.settings["data"]["edgar_user_agent"])
    if "@" not in ua or "change-me" in ua or "example.com" in ua:
        out.append(Check("EDGAR User-Agent", "FAIL", f"'{ua}'",
                         "Set EDGAR_USER_AGENT in .env to include YOUR real email "
                         "(SEC fair-access requirement)"))
    else:
        out.append(Check("EDGAR User-Agent", "PASS", ua))
    if not (ROOT / ".env").exists():
        out.append(Check(".env file", "WARN", "missing — using defaults",
                         "copy .env.example to .env and edit it"))
    else:
        out.append(Check(".env file", "PASS", "present"))
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(str(cfg.settings["fund"]["timezone"]))
        out.append(Check("Timezone", "PASS", str(cfg.settings["fund"]["timezone"])))
    except Exception as e:  # noqa: BLE001
        out.append(Check("Timezone", "FAIL", str(e), "fix FUND_TIMEZONE in .env"))
    return out


def _check_llm(cfg: Config) -> list[Check]:
    from .llm import LLMClient
    llm = LLMClient(host=cfg.settings["llm"]["host"], models=cfg.models,
                    api_key=str(cfg.settings["llm"].get("api_key", "")), timeout=20)
    h = llm.health()
    if not h.get("ok"):
        return [Check("Ollama daemon", "FAIL", str(h.get("error", ""))[:120],
                      "start Ollama (it runs as a Windows service after install); "
                      "check OLLAMA_HOST in .env")]
    out = [Check("Ollama daemon", "PASS", f"reachable at {llm.host}")]
    installed = h.get("models_installed", [])
    for m in cfg.models:
        if any(i == m or i.startswith(m) for i in installed):
            out.append(Check(f"Model {m}", "PASS", "installed"))
        else:
            out.append(Check(f"Model {m}", "WARN", "not in local list",
                             f"'ollama pull {m}' (cloud models need 'ollama signin' first)"))
    # live round-trip on the chain (small prompt, generous timeout)
    try:
        llm.timeout = 120
        reply = llm.chat([{"role": "user", "content":
                           "Reply with exactly the word: ready"}], temperature=0)
        ok = "ready" in reply.lower()
        out.append(Check("LLM round-trip", "PASS" if ok else "WARN", reply.strip()[:60],
                         "" if ok else "model responded oddly; check model choice"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("LLM round-trip", "FAIL", str(e)[:120],
                         "verify at least one model in OLLAMA_MODELS works: "
                         "'ollama run <model>'"))
    return out


def _check_data(cfg: Config) -> list[Check]:
    from .data import EdgarClient, MacroData, MarketData, NewsFeed
    from .storage import DB
    db = DB(cfg.data_dir / "fund.db")
    out = []
    edgar = EdgarClient(db, cfg.settings["data"]["edgar_user_agent"])
    try:
        n = len(edgar.ticker_map())
        cik = edgar.cik_for("INTC")
        f = edgar.fundamentals("INTC", years=2) or {}
        detail = f"{n} tickers; INTC CIK={cik}; {len(f)} XBRL concepts"
        out.append(Check("SEC EDGAR", "PASS" if f else "WARN", detail,
                         "" if f else "XBRL fetch failed; retry later"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("SEC EDGAR", "FAIL", str(e)[:120],
                         "check internet access / firewall for sec.gov"))
    market = MarketData(db)
    try:
        snap = market.snapshot("SPY")
        if snap:
            out.append(Check("Market data", "PASS",
                             f"SPY ${snap['price']:.2f}, sources OK"))
        else:
            out.append(Check("Market data", "FAIL", "no bars from Yahoo or Stooq",
                             "check internet access for query1.finance.yahoo.com / stooq.com"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("Market data", "FAIL", str(e)[:120], "check network"))
    key = str(cfg.settings["data"].get("fred_api_key", ""))
    if not key:
        out.append(Check("FRED macro", "WARN", "no API key (macro agent degrades gracefully)",
                         "free key: https://fred.stlouisfed.org/docs/api/api_key.html"))
    else:
        snap = MacroData(db, api_key=key).snapshot()
        got = [k for k in snap if k not in ("available", "note")]
        out.append(Check("FRED macro", "PASS" if got else "FAIL",
                         f"{len(got)} series" if got else "key set but no data",
                         "" if got else "verify FRED_API_KEY"))
    try:
        items = NewsFeed(db).market_wide(limit=4)
        out.append(Check("RSS news", "PASS" if items else "WARN",
                         f"{len(items)} headlines",
                         "" if items else "feeds unreachable (news is best-effort)"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("RSS news", "WARN", str(e)[:100], "news is best-effort"))
    return out


def _check_runtime(cfg: Config) -> list[Check]:
    out = []
    free_gb = shutil.disk_usage(str(ROOT)).free / 1e9
    out.append(Check("Disk space", "PASS" if free_gb > 2 else "WARN",
                     f"{free_gb:.1f} GB free",
                     "" if free_gb > 2 else "free up disk; SQLite cache grows over time"))
    try:
        from .storage import DB
        db = DB(cfg.data_dir / "fund.db")
        db.log_event("doctor", {"ok": True})
        out.append(Check("Database", "PASS", str(cfg.data_dir / "fund.db")))
    except Exception as e:  # noqa: BLE001
        out.append(Check("Database", "FAIL", str(e)[:120], "check data/ permissions"))
    hb = ROOT / "data" / "heartbeat.json"
    if hb.exists():
        try:
            age = time.time() - float(json.loads(hb.read_text()).get("ts", 0))
            status = "PASS" if age < 900 else "WARN"
            out.append(Check("Engine heartbeat", status, f"{age:.0f}s old",
                             "" if age < 900 else
                             "engine stale — is the scheduled task running? "
                             "(the watchdog restarts it within ~30 min)"))
        except (json.JSONDecodeError, ValueError):
            out.append(Check("Engine heartbeat", "WARN", "unreadable", ""))
    else:
        out.append(Check("Engine heartbeat", "WARN", "not found",
                         "expected before first run; after register_task.ps1 it "
                         "should always exist"))
    if os.name == "nt":
        out.append(Check("Platform", "PASS", "Windows — use scripts/register_task.ps1 for 24/7"))
    else:
        out.append(Check("Platform", "PASS", f"{os.name} (dev mode)"))
    return out


def run_doctor(skip_llm: bool = False) -> int:
    cfg = Config.load()
    checks: list[Check] = []
    checks += _check_config(cfg)
    if not skip_llm:
        checks += _check_llm(cfg)
    checks += _check_data(cfg)
    checks += _check_runtime(cfg)

    icons = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    width = max(len(c.name) for c in checks)
    print("\n== hedgefund doctor ==\n")
    for c in checks:
        print(f"{icons[c.status]} {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != "PASS":
            print(f"   {'':<{width}}  fix: {c.fix}")
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    print(f"\n{len(checks) - len(fails) - len(warns)} pass, "
          f"{len(warns)} warn, {len(fails)} fail")
    if fails:
        print("NOT READY — fix the FAIL items above before running 24/7.")
        return 1
    print("READY. Start 24/7 with: powershell -File scripts\\register_task.ps1")
    return 0
