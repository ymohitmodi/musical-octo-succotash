---
name: fund-doctor
description: Diagnose and fix problems with the AI hedge fund's 24/7 operation — engine not running, LLM failures, data source outages, stale heartbeat, task scheduler issues. Use when the user reports the fund is down, erroring, or behaving oddly.
---

# Fund Doctor — Diagnose & Repair

## Steps

1. **Run the built-in preflight** and read every FAIL/WARN:
   ```
   python -m hedgefund.main doctor
   ```
   (add `--skip-llm` if you only want data/runtime checks).
2. **Interpret by failure class:**
   - **Ollama daemon FAIL** → check the service:
     `Get-Process ollama` / restart the Ollama app. If `OLLAMA_HOST` is
     `https://ollama.com`, verify `OLLAMA_API_KEY` is set.
   - **Model WARN (not installed)** → `ollama pull <model>`; cloud models
     need `ollama signin` and an active subscription.
   - **LLM round-trip FAIL** → try each model manually
     (`ollama run <model> "say ready"`); reorder `OLLAMA_MODELS` so a
     working model is first; the chain degrades automatically but needs at
     least one live model.
   - **SEC EDGAR FAIL** → usually firewall/DNS, or a missing real email in
     `EDGAR_USER_AGENT` (SEC blocks anonymous UAs).
   - **Market data FAIL** → both Yahoo and Stooq down is almost always
     local network; test `curl https://stooq.com/q/d/l/?s=spy.us&i=d`.
   - **Heartbeat stale/missing** → engine isn't running. Check
     `Get-ScheduledTask AIHedgeFund | Get-ScheduledTaskInfo`, then
     `Start-ScheduledTask AIHedgeFund`. Tail `logs/hedgefund.log` for the
     crash reason before restarting.
3. **Check the journal for the machine's own error log:**
   ```
   python -c "import sqlite3; c=sqlite3.connect('data/fund.db'); [print(r) for r in c.execute(\"SELECT datetime(ts,'unixepoch'), ticker, payload FROM events WHERE kind='error' ORDER BY ts DESC LIMIT 15\")]"
   ```
   Repeated errors in one stage point at the broken component.
4. **Verify the fix**: re-run `doctor` until it prints READY, then confirm a
   fresh heartbeat appears within ~1 minute of the engine starting.

## Rules

- Fix configuration and environment, not strategy: never edit
  `config/constitution.yaml` hard limits as part of a "repair".
- If tests are in doubt after a code change: `python -m unittest discover tests`
  (27 offline tests must pass).
- Trading halts (`halted_until`, drawdown breaker) are SAFETY features, not
  bugs — never clear them mechanically; explain why they fired instead.
