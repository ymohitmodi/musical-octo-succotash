# 🏭 Autonomous AI Deep-Value Fund

A fully autonomous, 24/7, multi-agent **deep-value research and paper-trading
system** for a Windows 11 mini PC. It runs on **Ollama cloud models**, uses
**only free data sources** (SEC EDGAR, Yahoo/Stooq, FRED, RSS), and applies
three modern agentic-AI practices end to end:

| Practice of world-class small funds | How it's made agentic here |
|---|---|
| **Written doctrine** — great shops run on teachable, written investment philosophy (owner earnings, margin of safety, inversion, second-level thinking), not individual brilliance | `hedgefund/doctrine/*.md`: six discipline playbooks the agents **literally read in their prompts** before every analysis — valuation, forensic, moats, cycles, portfolio, process. Hand-edited only; evolution and LLMs can never rewrite the teaching. |
| **Dark factory** | Lights-out pipeline: screen → dossier → committee debate → PM decision → pre-mortem → checklist → constitutional review → execution → learning. Every stage is fault-isolated and journaled; a Task Scheduler watchdog restarts anything that dies. |
| **Relentless idea sourcing** — top funds run standing hunts (52-week-low lists, insider clusters, spinoffs), not a static screen | The **Prospector** rotates through 5 exploration channels every 2h outside market hours, 24/7: deep drawdowns, Graham net-nets, crash-with-improving-quality, insider Form-4 clusters, Form-10 spinoffs. Leads are quant-scored into the deep-dive queue. |
| **Patience via watchlist** — "wonderful business, wrong price" gets stalked for months | The PM can rule **watch** instead of buy/reject: the name enters a watchlist with a committee-set target entry price, monitored every intraday cycle; when Mr. Market finally quotes the price, it jumps the research queue. |
| **Checklist + pre-mortem before capital moves** (Munger/Klarman practice) | A 10-item pre-buy checklist (`config/checklist.yaml`) answered item-by-item with evidence — **any failed critical item is a machine-enforced reject** — plus a mandatory pre-mortem ("it's 2 years later and this lost 40%: what killed it?") with kill-criteria attached to the memo. |
| **Constitutional AI** | A written constitution enforced twice: an LLM critic reviews every decision against 10 principles and can veto, then deterministic **hard limits in code** (position caps, sector caps, cash floor, drawdown breakers, liquidity floors) run last and cannot be overridden by any model output. Fails **closed**. |
| **Post-mortems that compound** — pain + reflection = progress | Every closed position gets a written post-mortem judging **process, not outcome**; its one-sentence lesson is stored and **injected into every future agent prompt** — institutional memory that compounds in prose, alongside numeric genome evolution on realized P&L. |
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

**One command sets up everything** — installs Python and Ollama via winget
if missing, creates the venv, installs dependencies, writes `.env` with your
SEC contact email, runs the 40-test offline suite, live-validates every data
integration, runs the doctor preflight, and (with `-Full`, as Administrator)
pulls your models and registers the 24/7 task:

```powershell
git clone https://github.com/ymohitmodi/musical-octo-succotash.git
cd musical-octo-succotash
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Email you@example.com -Full
```

(Without `-Full` it stops after validation so you can smoke-test manually.
`ollama signin` once beforehand if you use cloud models. Linux/macOS dev:
`./scripts/setup.sh you@example.com`.)

Useful commands after setup:

```powershell
.\.venv\Scripts\python.exe -m hedgefund.main validate   # live end-to-end check of every data integration
.\.venv\Scripts\python.exe -m hedgefund.main doctor     # Ollama + runtime preflight
.\.venv\Scripts\python.exe -m hedgefund.main screen     # quant screen (no LLM needed)
.\.venv\Scripts\python.exe -m hedgefund.main once       # one full research cycle
powershell -File scripts\register_task.ps1              # go 24/7 (as Administrator)
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
  14-day stale-copy emergency tier, polite rate-limiting per host (verified
  by tests and by `main validate` against each provider's documented
  limits — SEC ≤10 req/s, we use 5), 429-aware backoff, retries. Wire-format
  contract tests (`tests/test_integrations.py`) replay each source's real
  response shapes — including EDGAR quarterly-vs-annual mixing, Yahoo null
  closes, Stooq junk rows, FRED "." placeholders, and Atom-vs-RSS feeds —
  through the real parsers on every test run.
- **Process**: heartbeat file + PowerShell watchdog (restarts on crash *or*
  hang) + Task Scheduler auto-start at boot + `powercfg` never-sleep.
- **Capital**: drawdown breaker (−15% halts all buying), daily-loss breaker
  (−3%), mechanical stop-losses, max-holding-period exits, trade rate limit —
  all deterministic code, all outside the reach of any LLM output.

## Repo map

```
config/           constitution.yaml · checklist.yaml · settings.yaml · universe.yaml
hedgefund/
  doctrine/       the encoded teaching: valuation · forensic · moats · cycles ·
                  portfolio · process (agents read these in every prompt)
  quant.py        Graham/Greenblatt/Piotroski/Altman/Beneish math (pure, tested)
  data/           edgar.py · market.py · macro.py · news.py · http.py
  agents/         screener · prospector (24/7 idea channels) · fundamental ·
                  forensic · moat · bear · macro · PM (watch/pre/post-mortem)
  constitution/   two-layer enforcement engine + enforced pre-buy checklist
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
