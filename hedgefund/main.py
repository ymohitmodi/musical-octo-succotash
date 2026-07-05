"""Entrypoints.

  python -m hedgefund.main run        # 24/7 dark-factory loop (production)
  python -m hedgefund.main once       # one research cycle now, then exit
  python -m hedgefund.main screen     # run the quant screen only, print table
  python -m hedgefund.main report     # print current daily report
  python -m hedgefund.main status     # NAV, positions, health
  python -m hedgefund.main doctor     # preflight: verify LLM, data, disk, DB
  python -m hedgefund.main validate [TICKER]          # live end-to-end check of
                                      # every data integration (format+throttle)
  python -m hedgefund.main benchmark [m1,m2]          # NYX cognitive battery:
                                      # score each model on the fund's own tasks
  python -m hedgefund.main dashboard [port]           # web dashboard (default 8787)
  python -m hedgefund.main backtest START [END] [N]   # screener backtest, e.g.
                                      # backtest 2016-01-01 2025-12-31 20
"""

from __future__ import annotations

import json
import logging
import sys

from .config import Config, ROOT


def _setup_logging() -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(logs / "hedgefund.log", encoding="utf-8")])


def main() -> None:
    _setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = Config.load()

    if cmd == "doctor":
        from .doctor import run_doctor
        sys.exit(run_doctor(skip_llm="--skip-llm" in sys.argv))

    if cmd == "validate":
        from .validate import run_validation
        args = [a for a in sys.argv[2:] if not a.startswith("-")]
        sys.exit(run_validation(ticker=args[0].upper() if args else "INTC"))

    if cmd == "benchmark":
        from .benchmark import run_benchmark
        models = sys.argv[2].split(",") if len(sys.argv) > 2 else None
        sys.exit(run_benchmark(models))

    from .orchestrator import Pipeline, Scheduler
    pipeline = Pipeline(cfg)

    if cmd == "run":
        Scheduler(pipeline).run_forever()
    elif cmd == "once":
        print(json.dumps(pipeline.research_cycle(), indent=2, default=str))
    elif cmd == "screen":
        for c in pipeline.run_screen():
            m = c["metrics"]
            print(f"{c['ticker']:>6}  score={c['score']:6.1f}  "
                  f"ev/ebit={m.get('ev_ebit') and round(m['ev_ebit'], 1)}  "
                  f"fcf_yield={m.get('fcf_yield') and round(m['fcf_yield'], 3)}  "
                  f"F={c['f_score']['f_score']}/{c['f_score']['f_max']}  "
                  f"Z={c['altman_z'] and round(c['altman_z'], 2)}")
    elif cmd == "report":
        print(pipeline.daily_report())
    elif cmd == "status":
        nav, cash, invested = pipeline.broker.nav()
        print(json.dumps({
            "nav": nav, "cash": cash, "invested": invested,
            "positions": [{k: p[k] for k in ('ticker', 'qty', 'avg_cost',
                                             'last_price', 'unrealized_pct')}
                          for p in pipeline.broker.positions()],
            "llm": pipeline.llm.health(),
            "llm_budget": pipeline.llm.budget.status() if pipeline.llm.budget else None,
        }, indent=2, default=str))
    elif cmd == "dashboard":
        from .dashboard import serve
        serve(port=int(sys.argv[2]) if len(sys.argv) > 2 else 8787)
    elif cmd == "backtest":
        if len(sys.argv) < 3:
            print("usage: backtest START [END] [TOP_N], dates as YYYY-MM-DD")
            sys.exit(1)
        from datetime import date
        from .backtest import Backtester
        start = sys.argv[2]
        end = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()
        top_n = int(sys.argv[4]) if len(sys.argv) > 4 else 20
        bt = Backtester(cfg, db=pipeline.db)
        result = bt.run(start, end, top_n=top_n, capital=cfg.capital)
        print(bt.report(result))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
