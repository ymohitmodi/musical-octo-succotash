---
name: fund-status
description: Check the AI hedge fund's operational health and portfolio state — heartbeat, NAV, positions, recent decisions, errors. Use when the user asks "how is the fund doing", "is the engine running", "what do we hold", or wants a morning briefing.
---

# Fund Status Briefing

Produce a concise operational + portfolio briefing from the fund's local state.

## Steps

1. **Engine liveness** — read `data/heartbeat.json`. Age < 15 min = live;
   15–35 min = probably between jobs (WARN); > 35 min = the watchdog should
   have restarted it, something is wrong (investigate `logs/hedgefund.log`).
2. **Portfolio state** — run:
   ```
   python -m hedgefund.main status
   ```
   (on Windows prefer `.venv\Scripts\python.exe`). This prints NAV, cash,
   positions with unrealized P&L, and LLM health.
3. **Recent activity** — query the journal directly (read-only!):
   ```
   python -c "import sqlite3,json,time; c=sqlite3.connect('data/fund.db'); c.row_factory=sqlite3.Row; [print(time.strftime('%m-%d %H:%M',time.localtime(r['ts'])), r['kind'], r['ticker'] or '', r['payload'][:120]) for r in c.execute(\"SELECT * FROM events WHERE ts>? ORDER BY ts DESC LIMIT 25\",(time.time()-86400,))]"
   ```
4. **Errors and vetoes** — count events of kind `error` and `veto` in the last
   24h. A few vetoes are healthy (the constitution is working). Repeated
   identical errors mean a data source or model is down → suggest
   `python -m hedgefund.main doctor`.
5. **Latest daily report** — if `reports/daily_YYYYMMDD.md` exists for today,
   include its highlights.

## Output format

A short briefing: one status line (LIVE/STALE/DOWN + NAV + day change),
positions table, notable decisions/vetoes/errors, and one "attention needed"
line if anything requires the user (otherwise say the factory needs nothing).

## Rules

- Read-only: never modify the DB, never place or close positions.
- If the engine is down, diagnose from `logs/hedgefund.log` tail before
  suggesting a restart via Task Scheduler (`Start-ScheduledTask -TaskName AIHedgeFund`).
