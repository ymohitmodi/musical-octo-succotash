"""Deterministic deep-value quant library.

All classical value/quality/forensic math lives here, computed from the
normalized EDGAR fundamentals produced by EdgarClient.fundamentals() plus a
market snapshot. LLM agents interpret these numbers; they never invent them
(constitution P4: evidence over narrative).
"""

from __future__ import annotations

from typing import Any

Fundamentals = dict[str, list[dict]]  # {concept: [{fy, end, val}, ...] newest first}


def _v(f: Fundamentals, concept: str, year: int = 0) -> float | None:
    series = f.get(concept) or []
    if len(series) > year and series[year]["val"] is not None:
        return float(series[year]["val"])
    return None


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def valuation_metrics(f: Fundamentals, price: float, shares: float | None) -> dict[str, Any]:
    """Core value ratios. shares falls back to the XBRL shares series."""
    if shares is None:
        shares = _v(f, "shares")
    if not shares or shares <= 0 or price <= 0:
        return {}
    mcap = price * shares
    ni, rev, equity = _v(f, "net_income"), _v(f, "revenue"), _v(f, "equity")
    cfo, capex = _v(f, "cfo"), _v(f, "capex")
    ebit = _v(f, "ebit")
    cash, ltd = _v(f, "cash"), _v(f, "long_term_debt")
    fcf = (cfo - capex) if (cfo is not None and capex is not None) else None
    ev = mcap + (ltd or 0) - (cash or 0)
    out: dict[str, Any] = {
        "market_cap": mcap,
        "pe": _safe_div(mcap, ni) if (ni or 0) > 0 else None,
        "pb": _safe_div(mcap, equity) if (equity or 0) > 0 else None,
        "ps": _safe_div(mcap, rev),
        "fcf_yield": _safe_div(fcf, mcap),
        "earnings_yield_ev": _safe_div(ebit, ev) if ev > 0 else None,  # Greenblatt
        "ev_ebit": _safe_div(ev, ebit) if (ebit or 0) > 0 else None,
        "debt_to_equity": _safe_div(ltd, equity) if (equity or 0) > 0 else None,
        "roe": _safe_div(ni, equity),
        "net_cash": (cash or 0) - (ltd or 0),
    }
    # Graham net-net (rarely triggers on large caps, decisive when it does)
    ca, tl = _v(f, "current_assets"), _v(f, "liabilities")
    if ca is not None and tl is not None:
        out["ncav_per_share"] = (ca - tl) / shares
        out["price_to_ncav"] = _safe_div(price, out["ncav_per_share"]) \
            if out["ncav_per_share"] and out["ncav_per_share"] > 0 else None
    return out


def piotroski_f(f: Fundamentals) -> dict[str, Any]:
    """Piotroski F-Score (0-9) from two consecutive fiscal years."""
    checks: dict[str, bool | None] = {}

    def ratio(concept_a: str, concept_b: str, year: int) -> float | None:
        return _safe_div(_v(f, concept_a, year), _v(f, concept_b, year))

    ni0, ni1 = _v(f, "net_income", 0), _v(f, "net_income", 1)
    ta0, ta1 = _v(f, "assets", 0), _v(f, "assets", 1)
    cfo0 = _v(f, "cfo", 0)
    roa0, roa1 = _safe_div(ni0, ta0), _safe_div(ni1, ta1)

    checks["roa_positive"] = roa0 > 0 if roa0 is not None else None
    checks["cfo_positive"] = cfo0 > 0 if cfo0 is not None else None
    checks["roa_improving"] = roa0 > roa1 if (roa0 is not None and roa1 is not None) else None
    checks["accruals_ok"] = cfo0 > ni0 if (cfo0 is not None and ni0 is not None) else None

    lev0 = ratio("long_term_debt", "assets", 0)
    lev1 = ratio("long_term_debt", "assets", 1)
    checks["leverage_falling"] = lev0 <= lev1 if (lev0 is not None and lev1 is not None) else None

    cr0 = ratio("current_assets", "current_liabilities", 0)
    cr1 = ratio("current_assets", "current_liabilities", 1)
    checks["liquidity_improving"] = cr0 > cr1 if (cr0 is not None and cr1 is not None) else None

    sh0, sh1 = _v(f, "shares", 0), _v(f, "shares", 1)
    checks["no_dilution"] = sh0 <= sh1 * 1.02 if (sh0 and sh1) else None

    gm0 = ratio("gross_profit", "revenue", 0)
    gm1 = ratio("gross_profit", "revenue", 1)
    checks["margin_improving"] = gm0 > gm1 if (gm0 is not None and gm1 is not None) else None

    at0 = ratio("revenue", "assets", 0)
    at1 = ratio("revenue", "assets", 1)
    checks["turnover_improving"] = at0 > at1 if (at0 is not None and at1 is not None) else None

    known = {k: v for k, v in checks.items() if v is not None}
    return {"f_score": sum(known.values()), "f_max": len(known), "checks": checks}


def altman_z(f: Fundamentals, market_cap: float | None) -> float | None:
    ta = _v(f, "assets")
    tl = _v(f, "liabilities")
    if not ta or not tl:
        return None
    ca, cl = _v(f, "current_assets") or 0, _v(f, "current_liabilities") or 0
    wc = ca - cl
    re = _v(f, "retained_earnings") or 0
    ebit = _v(f, "ebit") or 0
    rev = _v(f, "revenue") or 0
    mve = market_cap or 0
    return (1.2 * wc / ta + 1.4 * re / ta + 3.3 * ebit / ta
            + (0.6 * mve / tl if tl else 0) + 1.0 * rev / ta)


def beneish_m(f: Fundamentals) -> float | None:
    """Beneish M-Score (earnings-manipulation probability). Best effort:
    returns None unless the core inputs exist for two years."""
    rev0, rev1 = _v(f, "revenue", 0), _v(f, "revenue", 1)
    rec0, rec1 = _v(f, "receivables", 0), _v(f, "receivables", 1)
    gp0, gp1 = _v(f, "gross_profit", 0), _v(f, "gross_profit", 1)
    ta0, ta1 = _v(f, "assets", 0), _v(f, "assets", 1)
    if None in (rev0, rev1, ta0, ta1) or 0 in (rev0, rev1, ta0, ta1):
        return None

    dsri = _safe_div(_safe_div(rec0, rev0), _safe_div(rec1, rev1)) \
        if None not in (rec0, rec1) else 1.0
    gmi = _safe_div(_safe_div(gp1, rev1), _safe_div(gp0, rev0)) \
        if None not in (gp0, gp1) and gp0 and gp1 else 1.0
    sgi = rev0 / rev1

    ca0, ca1 = _v(f, "current_assets", 0) or 0, _v(f, "current_assets", 1) or 0
    ppe0, ppe1 = _v(f, "ppe_net", 0) or 0, _v(f, "ppe_net", 1) or 0
    aqi_num = 1 - (ca0 + ppe0) / ta0
    aqi_den = 1 - (ca1 + ppe1) / ta1
    aqi = aqi_num / aqi_den if aqi_den not in (0, None) else 1.0

    dep0, dep1 = _v(f, "depreciation", 0), _v(f, "depreciation", 1)
    if dep0 and dep1 and (dep1 + ppe1) and (dep0 + ppe0):
        depi = (dep1 / (dep1 + ppe1)) / (dep0 / (dep0 + ppe0))
    else:
        depi = 1.0

    sga0, sga1 = _v(f, "sga", 0), _v(f, "sga", 1)
    sgai = _safe_div(_safe_div(sga0, rev0), _safe_div(sga1, rev1)) \
        if None not in (sga0, sga1) and sga1 else 1.0

    tl0, tl1 = _v(f, "liabilities", 0), _v(f, "liabilities", 1)
    lvgi = _safe_div(_safe_div(tl0, ta0), _safe_div(tl1, ta1)) \
        if None not in (tl0, tl1) else 1.0

    ni0, cfo0 = _v(f, "net_income", 0), _v(f, "cfo", 0)
    tata = ((ni0 - cfo0) / ta0) if None not in (ni0, cfo0) else 0.0

    dsri, gmi, aqi, sgai, lvgi = (x if x is not None else 1.0
                                  for x in (dsri, gmi, aqi, sgai, lvgi))
    return (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
            + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)


def composite_value_score(metrics: dict, fscore: dict, z: float | None,
                          m: float | None, snapshot: dict,
                          weights: dict | None = None) -> float:
    """0-100 deep-value composite used to rank screener candidates.

    Weights are part of the screener genome, so evolution can retune the blend.
    """
    w = {"valuation": 0.45, "quality": 0.30, "safety": 0.15, "contrarian": 0.10}
    if weights:
        w.update({k: v for k, v in weights.items() if k in w})

    val_pts = 0.0
    ey = metrics.get("earnings_yield_ev")
    if ey is not None:
        val_pts += max(0.0, min(1.0, ey / 0.15)) * 40          # 15%+ EV yield = full marks
    fy = metrics.get("fcf_yield")
    if fy is not None:
        val_pts += max(0.0, min(1.0, fy / 0.12)) * 35
    pb = metrics.get("pb")
    if pb is not None and pb > 0:
        val_pts += max(0.0, min(1.0, (2.0 - pb) / 2.0)) * 15
    if metrics.get("price_to_ncav") and metrics["price_to_ncav"] < 1.0:
        val_pts += 10                                           # Graham net-net bonus
    val_pts = min(val_pts, 100)

    qual_pts = (fscore["f_score"] / max(fscore["f_max"], 1)) * 100 if fscore else 50

    safety_pts = 50.0
    if z is not None:
        safety_pts = 100 if z >= 3 else (60 if z >= 1.81 else 0)
    if m is not None and m > -1.78:
        safety_pts = min(safety_pts, 20)                        # manipulation red flag

    contra_pts = 50.0
    off_high = snapshot.get("pct_off_high")
    if off_high is not None:                                    # beaten-down = hunting ground
        contra_pts = max(0.0, min(1.0, -off_high / 0.5)) * 100

    return round(w["valuation"] * val_pts + w["quality"] * qual_pts
                 + w["safety"] * safety_pts + w["contrarian"] * contra_pts, 2)
