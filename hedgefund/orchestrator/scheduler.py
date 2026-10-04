"""24/7 market-aware scheduler with heartbeat (dark-factory shift plan).

Pure stdlib (no APScheduler dependency to break). Time math in the fund
timezone. Jobs are idempotent and journaled; a crash in one job never stops
the loop; a crash of the whole process is handled by the OS-level watchdog
(scripts/run_forever.ps1).

Shift plan (fund timezone, default America/New_York):
  07:30 weekdays  pre-market screen refresh
  09:30-16:00     intraday monitor every N minutes (stops, MTM, thesis review)
  18:00 weekdays  evening research cycle (the deep-dive committee)
  01:30 daily     evolution step + daily report
  Sat 10:00       weekend full-universe rescan
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import ROOT
from ..evolution import Evolver
from .pipeline import Pipeline

log = logging.getLogger("hedgefund.scheduler")

HEARTBEAT = ROOT / "data" / "heartbeat.json"


class Scheduler:
    def __init__(self, pipeline: Pipeline):
        self.p = pipeline
        self.cfg = pipeline.cfg
        self.tz = ZoneInfo(str(self.cfg.settings["fund"]["timezone"]))
        self.evolver = Evolver(pipeline.db, self.cfg.settings.get("evolution", {}))
        sch = self.cfg.settings["schedule"]
        self.t_screen = self._hm(sch["premarket_screen"])
        self.t_open = self._hm(sch["market_open"])
        self.t_close = self._hm(sch["market_close"])
        self.t_evening = self._hm(sch["evening_deepdive"])
        self.t_night = self._hm(sch["nightly_evolution"])
        self.t_weekend = self._hm(sch["weekend_full_rescan"])
        self.monitor_every = int(sch["intraday_monitor_minutes"]) * 60
        self.explore_every = float(sch.get("exploration_hours_between", 2)) * 3600
        self._done: dict[str, str] = {}   # job -> date string, idempotency guard
        self._last_monitor = 0.0
        self._last_explore = 0.0
        self._channel_idx = 0

    @staticmethod
    def _hm(s: str) -> tuple[int, int]:
        h, m = str(s).split(":")
        return int(h), int(m)

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    def _due_daily(self, job: str, hm: tuple[int, int], now: datetime,
                   weekdays_only: bool = False, weekday: int | None = None) -> bool:
        if weekdays_only and now.weekday() > 4:
            return False
        if weekday is not None and now.weekday() != weekday:
            return False
        stamp = now.strftime("%Y-%m-%d")
        if self._done.get(job) == stamp:
            return False
        due_at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        # fire within a generous 4h window after due time (survives restarts)
        if due_at <= now < due_at + timedelta(hours=4):
            self._done[job] = stamp
            return True
        return False

    def _market_open(self, now: datetime) -> bool:
        if now.weekday() > 4:
            return False
        o = now.replace(hour=self.t_open[0], minute=self.t_open[1], second=0)
        c = now.replace(hour=self.t_close[0], minute=self.t_close[1], second=0)
        return o <= now <= c

    def _heartbeat(self, status: str) -> None:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({"ts": time.time(), "status": status,
                                         "iso": datetime.now(timezone.utc).isoformat()}))

    def _run(self, name: str, fn, *args, **kwargs):
        """Fault isolation boundary: journal errors, never propagate."""
        log.info("job start: %s", name)
        self._heartbeat(f"running:{name}")
        try:
            result = fn(*args, **kwargs)
            log.info("job done: %s", name)
            return result
        except Exception as e:  # noqa: BLE001
            log.exception("job FAILED: %s", name)
            self.p.db.log_event("error", {"stage": f"job:{name}", "error": str(e)})
            return None

    # ------------------------------------------------------------------ main
    def run_forever(self) -> None:
        log.info("scheduler online — fund tz %s, LLM health: %s",
                 self.tz, self.p.llm.health())
        self.p.db.log_event("startup", {"llm": self.p.llm.health()})
        while True:
            now = self._now()
            try:
                if self._due_daily("premarket", self.t_screen, now, weekdays_only=True):
                    self._run("premarket_screen", self.p.run_screen)

                if self._market_open(now) and \
                        time.time() - self._last_monitor > self.monitor_every:
                    self._last_monitor = time.time()
                    self._run("monitor", self.p.monitor_cycle)

                if self._due_daily("evening", self.t_evening, now, weekdays_only=True):
                    self._run("research_cycle", self.p.research_cycle)

                if self._due_daily("nightly", self.t_night, now):
                    self._run("evolution", self._nightly)

                if self._due_daily("weekend", self.t_weekend, now, weekday=5):
                    self._run("weekend_rescan", self.p.research_cycle, weekend=True)

                # 24/7 exploration: while the market sleeps, the fund hunts.
                if not self._market_open(now) and \
                        time.time() - self._last_explore > self.explore_every:
                    self._last_explore = time.time()
                    from ..agents.prospector import CHANNELS
                    channel = CHANNELS[self._channel_idx % len(CHANNELS)]
                    self._channel_idx += 1
                    self._run(f"explore:{channel}", self.p.explore, channel)
            except Exception as e:  # noqa: BLE001 - belt never stops
                log.exception("scheduler loop error")
                self.p.db.log_event("error", {"stage": "scheduler", "error": str(e)})
            self._heartbeat("idle")
            time.sleep(30)

    def _nightly(self) -> dict:
        agents = ["screener", "fundamental", "forensic", "moat", "bear", "pm", "macro"]
        evolved = self.evolver.evolve_all(agents)
        report = self.p.daily_report()
        return {"evolved": evolved, "report_chars": len(report)}
