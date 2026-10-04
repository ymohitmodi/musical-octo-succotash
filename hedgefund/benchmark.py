"""The NYX Cognitive Battery: benchmark the model army on the fund's own tasks.

`python -m hedgefund.main benchmark` scores every model in the heavy + light
chains on the SPECIFIC cognitive skills this fund needs — not generic
leaderboard trivia. All scoring is deterministic (exact-match / rule-based),
no LLM judges. Each model is tested in ISOLATION (no fallback chain), so a
weak model can't hide behind a strong one.

Tasks:
  T1 extraction    faithful numeric extraction from a filing excerpt
  T2 no_fabricate  returns null for facts NOT present (hallucination guard)
  T3 needle        recall of planted facts from a long (~10k char) dossier
  T4 contradiction rejects a bullish claim the data contradicts
  T5 consistency   same dossier 3x -> same verdict (judgment stability)
  T6 json          raw JSON discipline without repair prompts

Interpretation guide (printed with results): a model must clear ~0.8
composite to be trusted on the heavy tier; below 0.6 it should only serve
the light tier or be dropped from the chain.
"""

from __future__ import annotations

import json
import statistics
import time

from .config import ROOT, Config
from .llm.ollama_client import LLMClient

FILING_EXCERPT = """
For fiscal year 2025, the Company reported total revenue of $8,214 million,
compared to $7,905 million in fiscal 2024. Net income attributable to
shareholders was $612 million. Cash flow from operating activities was
$1,108 million, and purchases of property, plant and equipment totaled
$397 million. Long-term debt stood at $2,850 million at year end. The Board
declared dividends totaling $205 million during the year.
"""

EXTRACTION_KEY = {"revenue_musd": 8214, "net_income_musd": 612, "cfo_musd": 1108,
                  "capex_musd": 397, "long_term_debt_musd": 2850,
                  "dividends_musd": 205}

# T2: goodwill and share count are NOT in the excerpt — nulls expected
NOFAB_FIELDS = {"revenue_musd": 8214, "goodwill_musd": None,
                "shares_outstanding_m": None, "capex_musd": 397}

NEEDLES = {"warranty_reserve_musd": 88, "auditor_name": "Hollis & Marsh LLP",
           "ceo_share_purchase_shares": 25000}

CONTRA_METRICS = {"revenue_by_year_musd": [4100, 4650, 5200],   # newest first? state it
                  "order_note": "years listed NEWEST FIRST (2025, 2024, 2023)",
                  "cfo_by_year_musd": [-120, 310, 480],
                  "receivables_by_year_musd": [1900, 1100, 700]}
CONTRA_CLAIM = ("The company shows accelerating revenue growth with strengthening "
                "cash generation and healthy receivables management.")

CONSISTENCY_DOSSIER = {"pe": 6.1, "fcf_yield": 0.14, "debt_to_equity": 0.3,
                       "f_score": "8/9", "altman_z": 3.4, "beneish_m": -2.6,
                       "pct_off_high": -0.45, "revenue_trend": "flat 3 years",
                       "note": "boring industrial, clean accounts, heavy insider buying"}


def _needle_dossier() -> str:
    filler = []
    for i in range(220):
        filler.append(f"line_item_{i:03d}: segment revenue ${(1000 + i * 7) % 900} million, "
                      f"margin {(12 + i) % 38}.{i % 10}%")
    filler.insert(60, f"The warranty reserve was ${NEEDLES['warranty_reserve_musd']} million.")
    filler.insert(140, f"The Company's independent auditor is {NEEDLES['auditor_name']}.")
    filler.insert(200, "In March the CEO purchased "
                       f"{NEEDLES['ceo_share_purchase_shares']} shares in the open market.")
    return "\n".join(filler)


# ------------------------------------------------------------ scoring (pure)
def score_extraction(ans: dict) -> float:
    ok = sum(1 for k, v in EXTRACTION_KEY.items()
             if isinstance(ans.get(k), (int, float)) and abs(ans[k] - v) < 0.5)
    return ok / len(EXTRACTION_KEY)


def score_nofab(ans: dict) -> float:
    ok = 0.0
    for k, v in NOFAB_FIELDS.items():
        got = ans.get(k, "MISSING")
        if v is None:
            ok += 1 if got is None or str(got).lower() in ("null", "none", "unknown", "n/a") else 0
        else:
            ok += 1 if isinstance(got, (int, float)) and abs(got - v) < 0.5 else 0
    return ok / len(NOFAB_FIELDS)


def score_needle(ans: dict) -> float:
    ok = 0.0
    if isinstance(ans.get("warranty_reserve_musd"), (int, float)) and \
            abs(ans["warranty_reserve_musd"] - NEEDLES["warranty_reserve_musd"]) < 0.5:
        ok += 1
    if "hollis" in str(ans.get("auditor_name", "")).lower():
        ok += 1
    if isinstance(ans.get("ceo_share_purchase_shares"), (int, float)) and \
            abs(ans["ceo_share_purchase_shares"] - NEEDLES["ceo_share_purchase_shares"]) < 0.5:
        ok += 1
    return ok / 3


def score_contradiction(ans: dict) -> float:
    supported = ans.get("claim_supported")
    if supported is False or str(supported).lower() == "false":
        # full credit needs at least one real reason cited
        reasons = " ".join(str(r) for r in ans.get("evidence", [])).lower()
        strong = any(w in reasons for w in ("receivab", "cash", "cfo", "operating"))
        return 1.0 if strong else 0.7
    return 0.0


def score_consistency(verdicts: list[str]) -> float:
    if not verdicts:
        return 0.0
    top = max(verdicts.count(v) for v in set(verdicts))
    return top / len(verdicts)


# ---------------------------------------------------------------- the battery
class ModelBench:
    """Runs the battery against ONE model, no fallback, deterministic scoring."""

    def __init__(self, client: LLMClient, model: str):
        self.c = client
        self.model = model
        self.raw_json_ok = 0
        self.raw_json_total = 0

    def _ask(self, system: str, user: str, temperature: float = 0.1) -> dict | None:
        """Single-model call (no chain), one shot, raw JSON parse tracked."""
        try:
            text = self.c._call(self.model, [
                {"role": "system", "content": system +
                 "\nRespond with a single valid JSON object only."},
                {"role": "user", "content": user}], temperature)
        except Exception:  # noqa: BLE001 - model down => task scores 0
            return None
        self.raw_json_total += 1
        parsed = self.c._extract_json(text)
        if parsed is not None:
            self.raw_json_ok += 1
        return parsed

    def run(self) -> dict:
        t0 = time.monotonic()
        scores: dict[str, float | None] = {}

        ans = self._ask("You extract financial figures exactly as stated.",
                        f"Extract from this filing excerpt:\n{FILING_EXCERPT}\n"
                        f"JSON keys: {list(EXTRACTION_KEY)} (values in $ millions).")
        scores["extraction"] = score_extraction(ans) if ans else None

        ans = self._ask("You extract ONLY facts present in the text. If a value is "
                        "not stated, use null. Never estimate.",
                        f"Extract from this filing excerpt:\n{FILING_EXCERPT}\n"
                        f"JSON keys: {list(NOFAB_FIELDS)} (values in $ millions, "
                        "null when not stated).")
        scores["no_fabricate"] = score_nofab(ans) if ans else None

        ans = self._ask("You answer questions from the provided document only.",
                        f"DOCUMENT:\n{_needle_dossier()}\n\nFind: the warranty reserve "
                        "($M), the auditor's name, and how many shares the CEO bought. "
                        f"JSON keys: {list(NEEDLES)}.")
        scores["needle"] = score_needle(ans) if ans else None

        ans = self._ask("You are a forensic reviewer. Judge the claim strictly "
                        "against the data.",
                        f"DATA: {json.dumps(CONTRA_METRICS)}\n\nCLAIM: \"{CONTRA_CLAIM}\"\n"
                        'JSON: {"claim_supported": true|false, "evidence": ["..."]}')
        scores["contradiction"] = score_contradiction(ans) if ans else None

        verdicts = []
        for _ in range(3):
            ans = self._ask("You are a value analyst. Verdict on this candidate.",
                            f"METRICS: {json.dumps(CONSISTENCY_DOSSIER)}\n"
                            'JSON: {"verdict": "bullish|neutral|bearish"}',
                            temperature=0.2)
            if ans and ans.get("verdict"):
                verdicts.append(str(ans["verdict"]).lower())
        scores["consistency"] = score_consistency(verdicts) if verdicts else None

        scores["json_discipline"] = (self.raw_json_ok / self.raw_json_total
                                     if self.raw_json_total else None)
        done = [v for v in scores.values() if v is not None]
        composite = round(statistics.mean(done), 3) if done else None
        return {"model": self.model, "scores": scores, "composite": composite,
                "tasks_completed": len(done), "elapsed_sec": round(time.monotonic() - t0, 1)}


def run_benchmark(models: list[str] | None = None) -> int:
    cfg = Config.load()
    s = cfg.settings["llm"]
    light = [m.strip() for m in str(s.get("light_models", "")).split(",") if m.strip()]
    client = LLMClient(host=s["host"], models=cfg.models, api_key=s.get("api_key", ""),
                       timeout=int(s["request_timeout_sec"]), max_retries=1)
    todo = models or list(dict.fromkeys(cfg.models + light))

    print(f"\n== NYX cognitive battery — {len(todo)} models, 6 tasks each ==\n")
    results = []
    for m in todo:
        print(f"benchmarking {m} ...", flush=True)
        results.append(ModelBench(client, m).run())

    lines = [f"# NYX model benchmark — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             "", "| model | extract | no-fab | needle | contra | consist | json | composite |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: (x["composite"] is not None, x["composite"]),
                    reverse=True):
        f = lambda k: ("—" if r["scores"].get(k) is None else f"{r['scores'][k]:.2f}")  # noqa: E731
        lines.append(f"| {r['model']} | {f('extraction')} | {f('no_fabricate')} | "
                     f"{f('needle')} | {f('contradiction')} | {f('consistency')} | "
                     f"{f('json_discipline')} | "
                     f"{'—' if r['composite'] is None else f'{r_composite(r):.2f}'} |")
    lines += ["",
              "Reading the results: composite ≥ 0.80 → trusted on the HEAVY tier "
              "(money-moving calls). 0.60–0.80 → LIGHT tier only. < 0.60 or '—' "
              "(unreachable) → drop from the chain. no-fabricate and contradiction "
              "are the safety-critical columns: a model that invents numbers or "
              "agrees with data-contradicting claims has no place in a fund."]
    report = "\n".join(lines)
    print("\n" + report)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"benchmark_models_{time.strftime('%Y%m%d_%H%M')}.md").write_text(report)
    (out / "benchmark_models_latest.json").write_text(json.dumps(results, indent=2))
    return 0 if any(r["composite"] for r in results) else 1


def r_composite(r: dict) -> float:
    return r["composite"] or 0.0
