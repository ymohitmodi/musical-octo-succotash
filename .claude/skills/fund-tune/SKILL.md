---
name: fund-tune
description: Safely adjust the fund's configuration — universe tickers, schedule times, risk limits, model chain, screener thresholds — with guardrail checks and tests. Use when the user wants to add/remove stocks, change position sizing, trade more/less aggressively, or swap Ollama models.
---

# Fund Tuning — Safe Configuration Changes

Config surface, from safest to most dangerous:

| File | Contents | Risk |
|---|---|---|
| `config/universe.yaml` | tickers + sectors to screen | low |
| `config/settings.yaml` / `.env` | models, schedule, batch sizes, capital | low-medium |
| `config/constitution.yaml` → `principles` | LLM-critic principles | medium |
| `config/constitution.yaml` → `hard_limits` | position/sector caps, breakers, floors | **HIGH** |

## Steps

1. Restate the requested change and which file it touches.
2. **For `hard_limits` changes, push back first**: these are the layer that
   cannot be overridden by any model output. Loosening them (bigger
   positions, weaker breakers, lower quality floors) increases tail risk in
   ways backtests won't show. Require the user to explicitly confirm the
   new value after you state the consequence, e.g. "raising
   max_position_pct_nav 0.05→0.10 means a single blowup can cost 10% of NAV".
   Never loosen more than one hard limit per session.
3. Make the edit. Keep YAML comments intact — they document intent.
4. **Validate**:
   ```
   python -m unittest discover tests
   python -c "from hedgefund.config import Config; c=Config.load(); print('ok', c.capital, c.models)"
   ```
   For universe changes, spot-check one added ticker resolves:
   `python -m hedgefund.main screen` (new names should appear or be
   legitimately filtered).
5. **Restart the engine** so changes load:
   `Stop-ScheduledTask AIHedgeFund; Start-ScheduledTask AIHedgeFund`
   (config is read at startup).
6. Log what changed and why in your reply — the user's future self will
   want the rationale.

## Never do

- Never edit genome rows in the DB by hand (evolution owns them; manual
  edits corrupt the fitness lineage).
- Never disable the forensic agent, the constitutional critic, or the
  drawdown breakers "temporarily".
- Never change `FUND_CAPITAL_USD` mid-flight to mask losses — start a fresh
  DB if the user wants a clean slate (`data/fund.db` → archive & delete).
