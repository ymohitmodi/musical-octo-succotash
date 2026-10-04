# AI Deep-Value Fund — project guide

Autonomous multi-agent deep-value **paper-trading** research system.
Python 3.10+, stdlib-first (deps: requests, PyYAML; optional: mcp).

## Commands

```
python -m hedgefund.main doctor      # preflight all dependencies (run first)
python -m hedgefund.main screen      # quant screen, no LLM needed
python -m hedgefund.main once        # one full research cycle (needs Ollama)
python -m hedgefund.main run         # 24/7 loop (production; started by Task Scheduler)
python -m hedgefund.main status      # NAV, positions, LLM health
python -m hedgefund.main dashboard   # read-only web UI on :8787
python -m hedgefund.main backtest 2016-01-01   # screener backtest
python -m unittest discover tests    # offline test suite; must pass before commit
```

On Windows use `.venv\Scripts\python.exe`. The 24/7 engine runs as Task
Scheduler task `AIHedgeFund` via `scripts/run_forever.ps1` (watchdog).

## Architecture in one paragraph

`orchestrator/scheduler.py` runs timed jobs that call
`orchestrator/pipeline.py`: quant screener (`agents/screener.py`, pure math
from `quant.py`) + overnight Prospector channels (`agents/prospector.py`,
rotating idea hunts every 2h off-hours) pick candidates → LLM committee
(`agents/`: fundamental, forensic, moat, bear + macro — each agent's prompt
embeds `doctrine/*.md` playbooks and recent post-mortem lessons) analyzes a
dossier built from `data/` (EDGAR XBRL, Yahoo/Stooq prices, RSS) → PM
synthesizes believability-weighted votes into buy / **watch** (watchlist
with target entry, stalked intraday) / reject → buy path runs pre-mortem →
enforced checklist (`config/checklist.yaml`) → `constitution/engine.py`
critique, then deterministic hard limits (the final authority) →
`portfolio/paper_broker.py` executes on paper → exits get post-mortems whose
lessons feed future prompts → nightly `evolution/evolver.py` breeds agent
genomes on realized P&L. Everything journals to SQLite (`storage/db.py`).

## Invariants — do not break

- **Hard limits in `config/constitution.yaml` are the safety floor.** Code
  in `constitution/engine.py:enforce_hard_limits` must run after ALL LLM
  steps and its veto is final. Never add a bypass.
- **Fail closed**: no forensic clearance → no trade; critic unreachable →
  no approval; LLM errors → hold/reject, never a default buy.
- **Paper trading only.** Do not wire a real brokerage into this codebase.
- **The journal is append-only**: never UPDATE/DELETE `events` or rewrite a
  recorded `decisions.thesis` (constitution P8).
- Evolution mutates only numeric genome params within
  `evolution/evolver.py:GENE_BOUNDS` — never prompts, code, or hard limits.
- All LLM JSON goes through `LLMClient.chat_json` (repair + fallback chain);
  don't parse model output ad hoc.
- The LLM budget governor (`llm/budget.py`) fails closed: budget exhausted →
  no committee → no new buys, while deterministic rails keep running. Never
  bypass it, and keep money-moving calls on the heavy tier.
- Data sources are free tiers: keep the rate limits in `data/http.py`, and
  keep the SEC User-Agent email requirement intact.

## Skills

`.claude/skills/` ships: `fund-status` (briefing), `fund-deep-dive`
(research a ticker), `fund-doctor` (diagnose 24/7 issues), `fund-tune`
(safe config changes), `fund-playbook` (read/evolve the doctrine and
checklist). Prefer these workflows over improvising.

## Doctrine — the encoded teaching

`hedgefund/doctrine/*.md` is the fund's investment curriculum; agents embed
it in every prompt via `doctrine.for_agent()`. Hand-edited only. Post-mortem
lessons (`lessons` table) are auto-injected institutional memory. Doctrine
edits go through the `fund-playbook` skill rules: decision-relevant,
imperative, never weakening the safety floors.

## Testing conventions

Tests are offline-only (no network, no LLM): fakes live in
`tests/test_e2e.py`. If you change pipeline behavior, extend the E2E test —
it drives the real pipeline/broker/constitution/evolution code end to end.
