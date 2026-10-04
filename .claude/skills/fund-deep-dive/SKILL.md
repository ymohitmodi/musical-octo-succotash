---
name: fund-deep-dive
description: Run an on-demand deep-value research dossier on a specific stock ticker using the fund's own data layer (SEC EDGAR XBRL, quant scores, market data, news) and present a committee-style verdict. Use when the user asks "what do you think of TICKER", "research TICKER", or "should the fund look at TICKER".
---

# On-Demand Deep Dive

Research one ticker with the same primary-source discipline as the fund's
committee (constitution P4: evidence over narrative).

## Steps

1. **Pull the quant scorecard** (this is the ground truth — never invent numbers):
   ```
   python -c "
   from hedgefund.config import Config
   from hedgefund.storage import DB
   from hedgefund.data import EdgarClient, MarketData
   from hedgefund import quant
   import json
   cfg = Config.load(); db = DB(cfg.data_dir/'fund.db')
   e = EdgarClient(db, cfg.settings['data']['edgar_user_agent']); m = MarketData(db)
   t = 'TICKER'
   snap = m.snapshot(t); f = e.fundamentals(t)
   met = quant.valuation_metrics(f, snap['price'], None)
   fs = quant.piotroski_f(f); z = quant.altman_z(f, met.get('market_cap')); bm = quant.beneish_m(f)
   print(json.dumps({'snapshot':snap,'metrics':met,'piotroski':fs,'altman_z':z,'beneish_m':bm,'composite':quant.composite_value_score(met,fs,z,bm,snap)}, indent=2, default=str))
   "
   ```
   (Or, if the `hedgefund` MCP server is connected, call `value_scorecard`,
   `edgar_recent_filings`, and `ticker_news` instead.)
2. **Check hard disqualifiers first** (config/constitution.yaml `hard_limits`):
   Beneish M > -1.78 → likely manipulator, STOP. Altman Z < 1.81 → distress
   zone, STOP. Below liquidity/market-cap floors → untradeable for this fund.
3. **Play the committee roles** on the numbers you fetched:
   - *Fundamental*: is there a ≥30% margin of safety vs a conservative
     intrinsic value?
   - *Forensic*: receivables vs revenue, CFO vs net income, leverage trend.
   - *Moat*: cyclical cheapness (opportunity) or secular decline (value trap)?
   - *Bear*: state the strongest attack honestly.
4. **Verdict**: bullish / neutral / bearish with the 2–3 decisive facts, and
   whether it would clear the fund's screener (`composite ≥ 55`).

## Rules

- Every claim must trace to a number you actually fetched. If data is
  missing, say "unknown" — never estimate silently.
- This is research, NOT a trade instruction: the autonomous committee makes
  its own decisions on its schedule. Never insert rows into the fund DB.
- End with the standard reminder that this is not financial advice.
