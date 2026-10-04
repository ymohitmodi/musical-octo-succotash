"""Backtesting harness for the quant screener signal (LLM-free).

What it tests: "rank the universe by the deep-value composite score each
month, hold the top N equal-weighted, with slippage" — i.e. the factory's
intake conveyor, which decides what the LLM committee ever gets to see.
The LLM committee itself is not simulated (historical LLM judgments can't be
replayed honestly), so treat results as a CEILING on signal quality, not a
forecast of fund returns.

Honesty guards built in:
  * Point-in-time fundamentals: an annual filing is only visible to the
    simulation `filing_lag_days` (default 90) after its fiscal year end —
    no look-ahead bias.
  * Trades fill at the rebalance day's close plus slippage.
  * Known limitation (stated, not hidden): a hand-written universe of
    today's tickers carries survivorship bias. Prefer judging RELATIVE
    scores (top-N vs benchmark vs bottom-N) over absolute CAGR.
"""

from __future__ import annotations

import bisect
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import quant
from ..config import ROOT, Config
from ..data import EdgarClient, MarketData
from ..storage import DB

log = logging.getLogger("hedgefund.backtest")


# ---------------------------------------------------------------- pure helpers
def point_in_time(fundamentals: dict[str, list[dict]], as_of: str,
                  filing_lag_days: int = 90, keep: int = 4) -> dict[str, list[dict]]:
    """Filter full annual series down to what was PUBLIC on `as_of`.

    A datapoint with fiscal end E becomes visible on E + filing_lag_days.
    Series stay newest-first.
    """
    cutoff = (datetime.strptime(as_of, "%Y-%m-%d")
              - timedelta(days=filing_lag_days)).strftime("%Y-%m-%d")
    out: dict[str, list[dict]] = {}
    for concept, series in fundamentals.items():
        visible = [row for row in series if row["end"] <= cutoff]
        if visible:
            out[concept] = visible[:keep]
    return out


def perf_stats(navs: list[float], dates: list[str],
               bench: list[float] | None = None) -> dict:
    """CAGR, vol, Sharpe (rf=0), max drawdown, and benchmark comparison."""
    if len(navs) < 2:
        return {}
    total = navs[-1] / navs[0] - 1
    d0 = datetime.strptime(dates[0], "%Y-%m-%d")
    d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
    years = max((d1 - d0).days / 365.25, 1 / 365.25)
    cagr = (navs[-1] / navs[0]) ** (1 / years) - 1
    rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol = (var ** 0.5) * (252 ** 0.5)
    sharpe = (mean * 252) / vol if vol > 0 else None
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out = {"start": dates[0], "end": dates[-1], "years": round(years, 2),
           "total_return": round(total, 4), "cagr": round(cagr, 4),
           "ann_vol": round(vol, 4),
           "sharpe": round(sharpe, 2) if sharpe is not None else None,
           "max_drawdown": round(mdd, 4)}
    if bench and len(bench) == len(navs) and bench[0]:
        btotal = bench[-1] / bench[0] - 1
        bcagr = (bench[-1] / bench[0]) ** (1 / years) - 1
        out["benchmark_total_return"] = round(btotal, 4)
        out["benchmark_cagr"] = round(bcagr, 4)
        out["excess_cagr"] = round(cagr - bcagr, 4)
    return out


class _PriceBook:
    """Fast as-of price lookups over full daily histories."""

    def __init__(self):
        self._dates: dict[str, list[str]] = {}
        self._closes: dict[str, list[float]] = {}

    def load(self, ticker: str, bars: list[dict]) -> None:
        self._dates[ticker] = [b["date"] for b in bars]
        self._closes[ticker] = [b["close"] for b in bars]

    def has(self, ticker: str) -> bool:
        return bool(self._dates.get(ticker))

    def price_at(self, ticker: str, day: str) -> float | None:
        dates = self._dates.get(ticker)
        if not dates:
            return None
        i = bisect.bisect_right(dates, day) - 1
        if i < 0:
            return None
        if dates[i] < (datetime.strptime(day, "%Y-%m-%d")
                       - timedelta(days=10)).strftime("%Y-%m-%d"):
            return None  # stale >10 days: likely delisted by then
        return self._closes[ticker][i]

    def snapshot_at(self, ticker: str, day: str) -> dict | None:
        """Trailing-252-bar stats as of `day` (for the composite score)."""
        dates = self._dates.get(ticker)
        if not dates:
            return None
        i = bisect.bisect_right(dates, day)
        window = self._closes[ticker][max(0, i - 252):i]
        if len(window) < 60:
            return None
        last = window[-1]
        return {"price": last, "pct_off_high": last / max(window) - 1}


@dataclass
class BacktestResult:
    stats: dict = field(default_factory=dict)
    nav_curve: list[dict] = field(default_factory=list)   # [{date, nav, bench}]
    rebalances: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Backtester:
    def __init__(self, cfg: Config, db: DB | None = None):
        self.cfg = cfg
        self.db = db or DB(cfg.data_dir / "fund.db")
        s = cfg.settings
        self.edgar = EdgarClient(self.db, s["data"]["edgar_user_agent"],
                                 rps=float(s["data"]["edgar_rps"]))
        self.market = MarketData(self.db, rps=float(s["data"]["market_rps"]))

    # ------------------------------------------------------------------- data
    def _load(self, tickers: list[str]) -> tuple[_PriceBook, dict[str, dict]]:
        book = _PriceBook()
        funds: dict[str, dict] = {}
        for t in tickers:
            bars = self.market.full_history(t)
            if bars:
                book.load(t, bars)
            f = self.edgar.fundamentals(t, years=15)
            if f:
                funds[t] = f
        return book, funds

    @staticmethod
    def _rebalance_days(trading_days: list[str]) -> list[str]:
        """First trading day of each month."""
        out, seen = [], set()
        for d in trading_days:
            key = d[:7]
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out

    # -------------------------------------------------------------------- run
    def run(self, start: str, end: str, top_n: int = 20,
            capital: float = 1_500_000, invested_frac: float = 0.90,
            slippage_bps: float = 10, filing_lag_days: int = 90) -> BacktestResult:
        universe = [e["ticker"] for e in self.cfg.universe.get("seeds", [])]
        benchmark = str(self.cfg.settings["fund"]["benchmark"])
        log.info("backtest %s..%s over %d tickers", start, end, len(universe))

        book, funds = self._load(universe + [benchmark])
        result = BacktestResult()
        result.skipped = [t for t in universe if not book.has(t) or t not in funds]
        active = [t for t in universe if book.has(t) and t in funds]
        if not book.has(benchmark):
            raise RuntimeError(f"no price history for benchmark {benchmark}")

        # calendar = benchmark trading days inside the window
        cal = [d for d in book._dates[benchmark] if start <= d <= end]
        if len(cal) < 40:
            raise RuntimeError("window too short or no benchmark data in range")
        slip = slippage_bps / 10000.0

        cash = capital
        holdings: dict[str, float] = {}          # ticker -> shares
        rebal_days = set(self._rebalance_days(cal))

        for day in cal:
            if day in rebal_days:
                ranked = self._rank(active, book, funds, day, filing_lag_days)
                target = set(ranked[:top_n])
                # sells first
                for t in [t for t in holdings if t not in target]:
                    px = book.price_at(t, day)
                    if px:
                        cash += holdings.pop(t) * px * (1 - slip)
                # buys / top-ups toward equal weight
                nav_now = cash + sum(q * (book.price_at(t, day) or 0)
                                     for t, q in holdings.items())
                per_name = (nav_now * invested_frac) / max(top_n, 1)
                for t in target:
                    if t in holdings:
                        continue
                    px = book.price_at(t, day)
                    if not px:
                        continue
                    fill = px * (1 + slip)
                    qty = min(per_name, cash) / fill
                    if qty * fill < 1000:
                        continue
                    cash -= qty * fill
                    holdings[t] = qty
                result.rebalances.append({"date": day, "held": sorted(target),
                                          "ranked_top5": ranked[:5]})
            nav = cash + sum(q * (book.price_at(t, day) or 0)
                             for t, q in holdings.items())
            result.nav_curve.append({"date": day, "nav": round(nav, 2),
                                     "bench": book.price_at(benchmark, day)})

        navs = [p["nav"] for p in result.nav_curve]
        dates = [p["date"] for p in result.nav_curve]
        bench = [p["bench"] for p in result.nav_curve]
        result.stats = perf_stats(navs, dates, bench)
        result.stats["top_n"] = top_n
        result.stats["universe"] = len(active)
        result.stats["skipped"] = len(result.skipped)
        return result

    def _rank(self, tickers: list[str], book: _PriceBook, funds: dict,
              day: str, lag: int) -> list[str]:
        scored = []
        for t in tickers:
            snap = book.snapshot_at(t, day)
            pit = point_in_time(funds[t], day, filing_lag_days=lag)
            if not snap or len(pit.get("revenue", [])) < 2:
                continue
            metrics = quant.valuation_metrics(pit, snap["price"], None)
            if not metrics:
                continue
            fscore = quant.piotroski_f(pit)
            z = quant.altman_z(pit, metrics.get("market_cap"))
            m = quant.beneish_m(pit)
            if z is not None and z < 1.81:
                continue
            if m is not None and m > -1.78:
                continue
            scored.append((quant.composite_value_score(metrics, fscore, z, m, snap), t))
        scored.sort(reverse=True)
        return [t for _, t in scored]

    # --------------------------------------------------------------- reporting
    def report(self, result: BacktestResult) -> str:
        s = result.stats
        lines = [
            f"# Backtest Report — screener signal, generated {time.strftime('%Y-%m-%d')}",
            "",
            "> LLM-free replay of the quant screener (top-N composite, monthly",
            "> rebalance, point-in-time fundamentals with filing lag). Universe is",
            "> today's seed list => survivorship bias; judge relative, not absolute.",
            "",
            f"| Metric | Fund | Benchmark |",
            f"|---|---|---|",
            f"| Period | {s.get('start')} → {s.get('end')} ({s.get('years')}y) | |",
            f"| Total return | {s.get('total_return', 0):+.1%} | "
            f"{s.get('benchmark_total_return', 0):+.1%} |",
            f"| CAGR | {s.get('cagr', 0):+.2%} | {s.get('benchmark_cagr', 0):+.2%} |",
            f"| Excess CAGR | {s.get('excess_cagr', 0):+.2%} | |",
            f"| Sharpe (rf=0) | {s.get('sharpe')} | |",
            f"| Ann. vol | {s.get('ann_vol', 0):.1%} | |",
            f"| Max drawdown | {s.get('max_drawdown', 0):.1%} | |",
            f"| Universe / skipped | {s.get('universe')} / {s.get('skipped')} | |",
            "",
            "## Rebalances",
        ]
        for r in result.rebalances[-12:]:
            lines.append(f"- **{r['date']}** top5: {', '.join(r['ranked_top5'])}")
        if result.skipped:
            lines += ["", f"_Skipped (no data): {', '.join(result.skipped)}_"]
        report = "\n".join(lines)
        out_dir = ROOT / "reports"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"backtest_{time.strftime('%Y%m%d_%H%M')}.md").write_text(report)
        (out_dir / "backtest_latest.json").write_text(
            json.dumps({"stats": result.stats, "nav_curve": result.nav_curve},
                       default=str))
        return report
