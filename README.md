# 🏭 Autonomous AI Deep-Value Fund

A fully autonomous, 24/7, multi-agent **deep-value research and paper-trading
system** for a Windows 11 mini PC. It runs on **Ollama cloud models**, uses
**only free data sources** (SEC EDGAR, Yahoo/Stooq, FRED, RSS), and applies
three modern agentic-AI practices end to end:

| Practice | How it's implemented here |
|---|---|
| **Dark factory** | Lights-out pipeline: screen → dossier → committee debate → PM decision → constitutional review → execution → learning. Every stage is fault-isolated and journaled; a Task Scheduler watchdog restarts anything that dies. No human in the loop — humans read the daily report. |
| **Constitutional AI** | A written constitution (`config/constitution.yaml`) enforced twice: an LLM critic reviews every decision against 10 principles and can veto, then deterministic **hard limits in code** (position caps, sector caps, cash floor, drawdown breakers, liquidity floors) run last and cannot be overridden by any model output. Fails **closed**: if the critic is unreachable, nothing gets approved. |
| **Evolving agents** | Each agent carries a "genome" of numeric parameters (skepticism, thresholds, weights, temperature). Nightly, genomes are scored on realized paper-trade excess returns vs SPY and bred (elitism + bounded mutation) with full lineage in the DB. Only parameters evolve — never code, prompts' safety text, or the constitution. |
| **Idea meritocracy** (Bridgewater-style) | Believability-weighted voting: analysts' votes are weighted by their track record. A dedicated **bear agent** is rewarded for killing bad ideas. Radical transparency: every thesis is journaled before the outcome is known and never edited. |

> ## ⚠️ Read this first
> This system **paper-trades by design**. It is a research tool, not
> financial advice, and no software — this one included — can promise
> "above-average returns" or Bridgewater-level performance. LLM-driven
> stock selection is experimental. If you ever connect real money, you do so
> at your own risk, after months of paper-trading evidence, and ideally
> after talking to a licensed advisor. The architecture manages a simulated
> $1.5M by default (`FUND_CAPITAL_USD`).

---

## Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │           24/7 SCHEDULER (dark factory)     │
                        │  premarket screen · intraday monitor ·      │
                        │  evening deep-dive · nightly evolution ·    │
                        │  weekend full rescan  + heartbeat/watchdog  │
                        └──────────────────────┬──────────────────────┘
                                               │
      FREE DATA LAYER                          ▼                 LLM LAYER (Ollama)
 ┌──────────────────────┐        ┌──────────────────────────┐  ┌────────────────────┐
 │ SEC EDGAR (XBRL,     │───────▶│  QUANT SCREENER (no LLM) │  │ cloud model chain  │
 │  filings, full-text) │        │  Graham · Greenblatt ·   │  │ with fallback,     │
 │ Yahoo ⇄ Stooq prices │───────▶│  Piotroski F · Altman Z ·│  │ retries, circuit   │
 │ FRED macro · RSS news│        │  Beneish M · composite   │  │ breakers           │
 └──────────────────────┘        └────────────┬─────────────┘  └────────┬───────────┘
                                              ▼ top candidates          │
                                 ┌──────────────────────────┐           │
                                 │   INVESTMENT COMMITTEE   │◀──────────┘
                                 │ fundamental · forensic · │
                                 │ moat · bear (red team) · │
                                 │ macro strategist         │
                                 └────────────┬─────────────┘
                                              ▼ believability-weighted debate
                                 ┌──────────────────────────┐
                                 │    PORTFOLIO MANAGER     │
                                 └────────────┬─────────────┘
                                              ▼
                                 ┌──────────────────────────┐
                                 │  CONSTITUTION ENGINE     │   LLM critique (veto) →
                                 │  (two-layer, fail-closed)│   hard limits in code
                                 └────────────┬─────────────┘
                                              ▼
                                 ┌──────────────────────────┐     ┌──────────────────┐
                                 │ PAPER BROKER (SQLite)    │────▶│ nightly EVOLUTION │
                                 │ slippage · stops · NAV   │     │ genome breeding   │
                                 └──────────────────────────┘     └──────────────────┘
```

Everything persists to a single SQLite journal (`data/fund.db`): candidates,
every agent report, every decision with its immutable thesis, trades, NAV
history, genome lineage, errors, and vetoes.

## Setup on Windows 11 (mini PC)

**Prerequisites:** [Python 3.11+](https://www.python.org/downloads/) (check
"Add to PATH") and [Ollama for Windows](https://ollama.com/download/windows).

```powershell
git clone https://github.com/ymohitmodi/musical-octo-succotash.git
cd musical-octo-succotash
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

Then:

1. **Edit `.env`** —
   - `EDGAR_USER_AGENT`: must include **your email** (SEC's fair-access rule).
   - `OLLAMA_MODELS`: your model chain, strongest first. With an Ollama
     cloud subscription: `ollama signin`, then
     `ollama pull deepseek-v3.1:671b-cloud` (runs via your local daemon, no
     GPU needed on the mini PC). Keep a small local model (e.g.
     `llama3.1:8b`) at the end of the chain as an offline last resort.
   - `FRED_API_KEY` (optional, free): enables the macro agent's data feed.
2. **Preflight** — verifies Ollama, models, EDGAR, market data, FRED, disk,
   DB and heartbeat, with a fix hint for anything broken:
   ```powershell
   .\.venv\Scripts\python.exe -m hedgefund.main doctor
   ```
3. **Smoke test** (no LLM needed — pure quant screen):
   ```powershell
   .\.venv\Scripts\python.exe -m hedgefund.main screen
   ```
4. **One full research cycle** (uses the LLM committee):
   ```powershell
   .\.venv\Scripts\python.exe -m hedgefund.main once
   ```
5. **Go 24/7** (registers a boot-time Task Scheduler task + watchdog;
   run as Administrator):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
   ```

Daily output lands in `reports/daily_YYYYMMDD.md`; logs in
`logs/hedgefund.log`; check live state anytime with
`python -m hedgefund.main status`.

## MCP server (SEC EDGAR + fund tools)

The fund's data layer is also exposed as an **MCP server** so Claude
Desktop, Claude Code, or any MCP client can research alongside the fund:

```powershell
pip install mcp
python -m hedgefund.mcp_server.server
```

Tools: `edgar_lookup_cik`, `edgar_recent_filings`, `edgar_fundamentals`,
`edgar_full_text_search`, `market_snapshot`, `value_scorecard`,
`ticker_news`, `portfolio_state`, `recent_decisions`.

This repo ships a `.mcp.json`, so Claude Code picks the server up
automatically. For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hedgefund": {
      "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "hedgefund.mcp_server.server"],
      "cwd": "C:\\path\\to\\repo"
    }
  }
}
```

## Web dashboard

A zero-dependency (stdlib `http.server`) read-only dashboard over the fund's
SQLite journal — NAV vs SPY chart (indexed, with crosshair tooltip), stat
tiles, open positions, committee decisions, the event journal, genome
lineage, and the latest backtest. Light/dark follows your OS theme.

```powershell
.\.venv\Scripts\python.exe -m hedgefund.main dashboard        # http://127.0.0.1:8787
```

It binds to localhost and exposes no write operations. An "engine live/stale"
indicator reads the scheduler's heartbeat so you can see at a glance that the
24/7 loop is healthy.

## Backtesting the screener signal

Replays the quant screener (the factory's intake conveyor) historically:
rank the universe by the deep-value composite each month, hold the top N
equal-weighted with slippage, compare against SPY.

```powershell
.\.venv\Scripts\python.exe -m hedgefund.main backtest 2016-01-01              # to today, top 20
.\.venv\Scripts\python.exe -m hedgefund.main backtest 2016-01-01 2024-12-31 15
```

Honesty guards: **point-in-time fundamentals** (an annual filing only becomes
visible 90 days after fiscal year end — no look-ahead bias), fills at close
plus slippage, and stale prices treated as delistings. Two stated
limitations: the LLM committee is *not* simulated (historical LLM judgments
can't be replayed honestly), and a hand-written universe of today's tickers
carries survivorship bias — so judge the top-N *relative* to the benchmark,
not the absolute CAGR. Reports land in `reports/backtest_*.md` and feed the
dashboard's backtest panel.

## Resilience design

- **LLM**: model fallback chain → per-model retry with backoff → per-model
  circuit breaker → JSON repair loop. Committee tolerates individual agent
  failures (quorum rule), but the **forensic agent is mandatory** — no
  clearance, no trade (fail-closed).
- **Data**: dual price sources (Yahoo ⇄ Stooq), SQLite caching with a
  14-day stale-copy emergency tier, polite rate-limiting per host, retries.
- **Process**: heartbeat file + PowerShell watchdog (restarts on crash *or*
  hang) + Task Scheduler auto-start at boot + `powercfg` never-sleep.
- **Capital**: drawdown breaker (−15% halts all buying), daily-loss breaker
  (−3%), mechanical stop-losses, max-holding-period exits, trade rate limit —
  all deterministic code, all outside the reach of any LLM output.

## Repo map

```
config/           constitution.yaml · settings.yaml · universe.yaml
hedgefund/
  quant.py        Graham/Greenblatt/Piotroski/Altman/Beneish math (pure, tested)
  data/           edgar.py · market.py · macro.py · news.py · http.py
  agents/         screener · fundamental · forensic · moat · bear · macro · PM
  constitution/   two-layer enforcement engine
  portfolio/      paper broker (fills, stops, NAV)
  evolution/      genome breeding on realized P&L
  orchestrator/   pipeline.py (the factory line) · scheduler.py (24/7 shifts)
  backtest/       point-in-time screener backtester
  dashboard/      stdlib web dashboard (NAV chart, positions, journal)
  mcp_server/     MCP tools over stdio
scripts/          install_windows.ps1 · run_forever.ps1 · register_task.ps1
tests/            offline unit tests (quant math, vetoes, evolution bounds)
```

## Running the tests

```powershell
python -m unittest discover tests
```
