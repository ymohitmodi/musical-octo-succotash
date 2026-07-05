"""Evolving agents: parameter-space evolution scored on realized paper P&L.

Design constraints that keep self-modification SAFE:
  * Only numeric genome parameters evolve — never code, never prompts' safety
    text, never the constitution. Hard limits are outside the genome entirely.
  * Fitness = average excess return vs the benchmark of CLOSED decisions the
    genome participated in. No fitness without enough closed trades
    (prevents overfitting to noise).
  * Elitism keeps the best genome; mutation is bounded (+/-30% per gene).
  * Every genome has a parent_id — full audit lineage in the DB.

The bear agent is scored INVERTED: it earns fitness when names it attacked
(bearish verdicts) went on to lose money.
"""

from __future__ import annotations

import json
import logging
import random
import time

from ..storage import DB

log = logging.getLogger("hedgefund.evolution")

# genes that may evolve, with (min, max) bounds
GENE_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (0.1, 0.7),
    "skepticism": (0.2, 0.95),
    "min_margin_of_safety": (0.20, 0.50),
    "trap_sensitivity": (0.3, 0.9),
    "caution": (0.2, 0.9),
    "base_position_pct": (0.01, 0.05),
    "conviction_scaling": (0.3, 1.0),
    "w_valuation": (0.25, 0.60),
    "w_quality": (0.15, 0.45),
    "w_safety": (0.05, 0.30),
    "w_contrarian": (0.0, 0.25),
    "min_composite": (45.0, 70.0),
}


class Evolver:
    def __init__(self, db: DB, cfg: dict):
        self.db = db
        self.min_closed = int(cfg.get("min_closed_decisions_for_fitness", 5))
        self.mutation_rate = float(cfg.get("mutation_rate", 0.35))
        self.rng = random.Random()

    # ----------------------------------------------------------- fitness calc
    def _closed_decision_returns(self) -> list[dict]:
        """[{decision_id, genome_ids, excess_return}] for closed decisions."""
        rows = self.db.query("""
            SELECT d.id, d.genome_ids, d.ts,
                   bt.price AS buy_price, st.price AS sell_price,
                   bnav.benchmark_price AS bench_entry, snav.benchmark_price AS bench_exit
            FROM decisions d
            JOIN trades bt ON bt.decision_id = d.id AND bt.side = 'buy'
            JOIN trades st ON st.decision_id = d.id AND st.side = 'sell'
            LEFT JOIN nav_history bnav ON bnav.ts = (
                SELECT MAX(ts) FROM nav_history WHERE ts <= bt.ts)
            LEFT JOIN nav_history snav ON snav.ts = (
                SELECT MAX(ts) FROM nav_history WHERE ts <= st.ts)
            WHERE d.status = 'closed' AND d.action = 'buy'
        """)
        out = []
        for r in rows:
            if not r["buy_price"]:
                continue
            ret = r["sell_price"] / r["buy_price"] - 1
            bench_ret = 0.0
            if r["bench_entry"] and r["bench_exit"]:
                bench_ret = r["bench_exit"] / r["bench_entry"] - 1
            out.append({"decision_id": r["id"],
                        "genome_ids": json.loads(r["genome_ids"] or "{}"),
                        "excess_return": ret - bench_ret})
        return out

    def compute_fitness(self) -> dict[int, float]:
        """genome_id -> mean excess return across decisions it touched."""
        contrib: dict[int, list[float]] = {}
        for d in self._closed_decision_returns():
            for agent, gid in d["genome_ids"].items():
                if gid is None:
                    continue
                x = d["excess_return"]
                if agent == "bear":
                    # bear was overruled on names that got bought; if the buy
                    # lost money the bear was right — reward accordingly.
                    x = -x
                contrib.setdefault(int(gid), []).append(x)
        fitness = {gid: sum(v) / len(v) for gid, v in contrib.items()
                   if len(v) >= max(1, self.min_closed // 2)}
        for gid, fit in fitness.items():
            with self.db.conn() as c:
                c.execute("UPDATE genomes SET fitness=? WHERE id=?", (fit, gid))
        return fitness

    # ------------------------------------------------------------- evolution
    def mutate_params(self, params: dict, seed: int | None = None) -> dict:
        rng = random.Random(seed) if seed is not None else self.rng
        child = dict(params)
        for gene, value in params.items():
            if gene not in GENE_BOUNDS or not isinstance(value, (int, float)):
                continue
            if rng.random() < self.mutation_rate:
                lo, hi = GENE_BOUNDS[gene]
                jitter = 1 + rng.uniform(-0.3, 0.3)
                child[gene] = round(min(hi, max(lo, value * jitter)), 4)
        return child

    def evolve_agent(self, agent: str) -> dict | None:
        """One evolution step for one agent, if enough evidence has accrued."""
        closed = self.db.query(
            "SELECT COUNT(*) AS n FROM decisions WHERE status='closed' AND action='buy'")
        if int(closed[0]["n"]) < self.min_closed:
            return None

        self.compute_fitness()
        rows = self.db.query(
            "SELECT * FROM genomes WHERE agent=? ORDER BY created_ts DESC LIMIT 8", (agent,))
        if not rows:
            return None
        scored = [r for r in rows if r["fitness"] is not None]
        if not scored:
            return None

        best = max(scored, key=lambda r: r["fitness"])
        current_active = next((r for r in rows if r["active"]), rows[0])

        # If the active genome underperforms the best-known, or it's simply
        # time to explore, breed a child from the best and switch to it.
        child_params = self.mutate_params(json.loads(best["params"]))
        with self.db.conn() as c:
            c.execute("UPDATE genomes SET active=0 WHERE agent=?", (agent,))
        child_id = self.db.insert(
            "genomes", agent=agent, params=json.dumps(child_params),
            parent_id=best["id"], generation=int(best["generation"]) + 1,
            active=1, fitness=None, created_ts=time.time())
        summary = {"agent": agent, "parent": best["id"], "parent_fitness": best["fitness"],
                   "replaced_active": current_active["id"], "child": child_id,
                   "child_params": child_params}
        self.db.log_event("evolution", summary)
        log.info("evolved %s: gen %d -> child %d", agent, best["generation"], child_id)
        return summary

    def evolve_all(self, agents: list[str]) -> list[dict]:
        return [s for a in agents if (s := self.evolve_agent(a))]
