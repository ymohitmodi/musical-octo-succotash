"""Entrypoints.

  python -m hedgefund.main run        # 24/7 dark-factory loop (production)
  python -m hedgefund.main once       # one research cycle now, then exit
  python -m hedgefund.main screen     # run the quant screen only, print table
  python -m hedgefund.main report     # print current daily report
  python -m hedgefund.main status     # NAV, positions, health
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
        }, indent=2, default=str))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
