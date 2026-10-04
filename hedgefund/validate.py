"""Live integration validation: `python -m hedgefund.main validate [TICKER]`.

Exercises EVERY external data integration end-to-end against the real
endpoints, asserting response FORMAT (not just reachability), measuring
latency, and verifying throttle compliance against each provider's
documented limits. Run this on the production machine; the same format
assertions run offline in tests/test_integrations.py against recorded
payloads.

Exit code 0 = all integrations validated; 1 = at least one hard failure.
"""

from __future__ import annotations

import time

from .config import Config
from .doctor import Check
from .storage import DB

# our configured rps vs the provider's documented/safe ceiling
THROTTLE_POLICY = {
    "SEC EDGAR": {"ours_key": "edgar_rps", "provider_max": 10.0,
                  "doc": "SEC fair-access: max 10 req/s"},
    "Market data": {"ours_key": "market_rps", "provider_max": 2.0,
                    "doc": "unofficial free endpoints: stay ≤ 2 req/s"},
}


def _timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, (time.monotonic() - t0) * 1000


def run_validation(ticker: str = "INTC") -> int:
    cfg = Config.load()
    db = DB(cfg.data_dir / "fund.db")
    checks: list[Check] = []
    ua = cfg.settings["data"]["edgar_user_agent"]

    # ---------------------------------------------------------- throttle policy
    for name, pol in THROTTLE_POLICY.items():
        ours = float(cfg.settings["data"][pol["ours_key"]])
        ok = ours <= pol["provider_max"]
        checks.append(Check(f"Throttle policy: {name}",
                            "PASS" if ok else "FAIL",
                            f"configured {ours} req/s ({pol['doc']})",
                            "" if ok else f"lower {pol['ours_key']} in settings.yaml"))

    # ---------------------------------------------------------------- SEC EDGAR
    from .data import EdgarClient
    edgar = EdgarClient(db, ua, rps=float(cfg.settings["data"]["edgar_rps"]))
    try:
        tmap, ms = _timed(edgar.ticker_map)
        ok = len(tmap) > 5000 and "AAPL" in tmap and isinstance(tmap["AAPL"]["cik"], int)
        checks.append(Check("EDGAR ticker map", "PASS" if ok else "FAIL",
                            f"{len(tmap)} tickers, {ms:.0f}ms",
                            "" if ok else "unexpected format from company_tickers.json"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("EDGAR ticker map", "FAIL", str(e)[:100],
                            "check network + EDGAR_USER_AGENT email"))
    try:
        filings, ms = _timed(edgar.recent_filings, ticker)
        ok = bool(filings) and all(
            f["form"] and len(f["filed"]) == 10 and f["url"].startswith("https://")
            for f in filings)
        checks.append(Check("EDGAR filings index", "PASS" if ok else "FAIL",
                            f"{len(filings)} filings for {ticker}, {ms:.0f}ms",
                            "" if ok else "submissions JSON shape changed?"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("EDGAR filings index", "FAIL", str(e)[:100], "check network"))
    try:
        f, ms = _timed(edgar.fundamentals, ticker)
        concepts = list(f or {})
        years = len((f or {}).get("revenue", []))
        ok = f is not None and len(concepts) >= 8 and years >= 2 and \
            all(isinstance(r["val"], (int, float)) for r in f["revenue"])
        checks.append(Check("EDGAR XBRL fundamentals", "PASS" if ok else "FAIL",
                            f"{len(concepts)} concepts, {years}y revenue, {ms:.0f}ms",
                            "" if ok else f"thin XBRL for {ticker}; try another ticker"))
        # cache round-trip: second call must be near-instant (no re-fetch)
        _, ms2 = _timed(edgar.fundamentals, ticker)
        checks.append(Check("EDGAR cache", "PASS" if ms2 < 100 else "WARN",
                            f"repeat fetch {ms2:.0f}ms",
                            "" if ms2 < 100 else "cache not engaging; check data/fund.db"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("EDGAR XBRL fundamentals", "FAIL", str(e)[:100], "check network"))
    try:
        hits, ms = _timed(edgar.full_text_search, f'"{ticker}"', "8-K", 5)
        checks.append(Check("EDGAR full-text search",
                            "PASS" if hits else "WARN",
                            f"{len(hits)} hits, {ms:.0f}ms",
                            "" if hits else "no hits — endpoint may be unreachable "
                            "(optional; screener works without it)"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("EDGAR full-text search", "WARN", str(e)[:100],
                            "optional; screener works without it"))

    # ------------------------------------------------------------- market data
    from .data import MarketData
    market = MarketData(db, rps=float(cfg.settings["data"]["market_rps"]))
    for name, fn in (("Yahoo daily bars", market._yahoo_bars),
                     ("Stooq daily bars", market._stooq_bars)):
        try:
            bars, ms = _timed(fn, "SPY", 260)
            ok = bool(bars) and len(bars) >= 100 and \
                all(b["close"] > 0 for b in bars[-5:]) and \
                bars[-1]["date"] > bars[0]["date"]
            checks.append(Check(name, "PASS" if ok else "FAIL",
                                f"{len(bars or [])} bars, latest {bars[-1]['date'] if bars else '—'}, {ms:.0f}ms",
                                "" if ok else "format/reachability issue"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check(name, "FAIL", str(e)[:100],
                                "one source failing is survivable; both is not"))
    try:
        snap, ms = _timed(market.snapshot, ticker)
        need = ("price", "adv_dollars", "adv_shares", "pct_off_high")
        ok = snap is not None and all(snap.get(k) is not None for k in need)
        checks.append(Check("Market snapshot", "PASS" if ok else "FAIL",
                            f"{ticker} ${snap['price']:.2f}, ADV ${snap['adv_dollars'] / 1e6:.0f}M, {ms:.0f}ms"
                            if snap else "no snapshot",
                            "" if ok else "needed by screener + risk engine"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("Market snapshot", "FAIL", str(e)[:100], ""))

    # -------------------------------------------------------------------- FRED
    key = str(cfg.settings["data"].get("fred_api_key", ""))
    if key:
        from .data import MacroData
        try:
            snap, ms = _timed(MacroData(db, api_key=key).snapshot)
            series = [k for k, v in snap.items()
                      if isinstance(v, (int, float)) and k != "available"]
            ok = len(series) >= 4
            checks.append(Check("FRED macro", "PASS" if ok else "FAIL",
                                f"{len(series)} series ({', '.join(series[:4])}…), {ms:.0f}ms",
                                "" if ok else "verify FRED_API_KEY"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("FRED macro", "FAIL", str(e)[:100], "verify FRED_API_KEY"))
    else:
        checks.append(Check("FRED macro", "WARN", "no key — macro agent degrades gracefully",
                            "free key: fred.stlouisfed.org/docs/api/api_key.html"))

    # --------------------------------------------------------------------- RSS
    from .data import NewsFeed
    from .data.news import MARKET_FEEDS, TICKER_FEEDS
    news = NewsFeed(db)
    for tpl in TICKER_FEEDS:
        url = tpl.format(ticker=ticker)
        items = news._parse(url, 8)
        ok = bool(items) and all(i["title"] for i in items)
        checks.append(Check(f"RSS {url.split('/')[2]}", "PASS" if ok else "WARN",
                            f"{len(items)} headlines for {ticker}",
                            "" if ok else "feed empty/unreachable (news is best-effort)"))
    for url in MARKET_FEEDS:
        items = news._parse(url, 6)
        checks.append(Check(f"RSS {url.split('/')[2]}", "PASS" if items else "WARN",
                            f"{len(items)} headlines",
                            "" if items else "feed empty/unreachable (best-effort)"))

    # ------------------------------------------------------- live throttle proof
    try:
        t0 = time.monotonic()
        for _ in range(3):
            edgar.http.get(edgar_url_probe())
        spacing = (time.monotonic() - t0) / 2
        min_gap = 1.0 / float(cfg.settings["data"]["edgar_rps"])
        ok = spacing >= min_gap * 0.85
        checks.append(Check("Live throttle spacing", "PASS" if ok else "FAIL",
                            f"observed {spacing * 1000:.0f}ms between EDGAR calls "
                            f"(floor {min_gap * 1000:.0f}ms)",
                            "" if ok else "rate limiter not engaging — do not run 24/7"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("Live throttle spacing", "WARN", str(e)[:100], ""))

    # ------------------------------------------------------------------- output
    icons = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "SKIP": "⏭"}
    width = max(len(c.name) for c in checks)
    print(f"\n== integration validation (ticker={ticker}) ==\n")
    for c in checks:
        print(f"{icons[c.status]} {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != "PASS":
            print(f"   {'':<{width}}  fix: {c.fix}")
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    print(f"\n{len(checks) - len(fails) - len(warns)} pass, {len(warns)} warn, "
          f"{len(fails)} fail")
    print("ALL INTEGRATIONS VALIDATED." if not fails else
          "INTEGRATION FAILURES — fix before running 24/7.")
    return 1 if fails else 0


def edgar_url_probe() -> str:
    return "https://www.sec.gov/files/company_tickers.json"
