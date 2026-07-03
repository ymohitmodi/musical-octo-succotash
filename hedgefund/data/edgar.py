"""SEC EDGAR client — the fund's primary-source backbone (free, official).

Endpoints used (all public, no key; SEC asks for a descriptive User-Agent):
  * https://www.sec.gov/files/company_tickers.json           ticker -> CIK map
  * https://data.sec.gov/submissions/CIK##########.json      filing index
  * https://data.sec.gov/api/xbrl/companyfacts/CIK#.json     full XBRL facts
  * https://efts.sec.gov/LATEST/search-index?q=...           full-text search
"""

from __future__ import annotations

import logging
from typing import Any

from ..storage import DB
from .http import RateLimitedHTTP

log = logging.getLogger("hedgefund.edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

# us-gaap tag fallbacks, in preference order, for normalized fundamentals.
TAG_MAP: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "ebit": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "depreciation": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                     "Depreciation"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "interest_expense": ["InterestExpense"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
}


class EdgarClient:
    def __init__(self, db: DB, user_agent: str, rps: float = 5.0):
        self.db = db
        self.http = RateLimitedHTTP(rps=rps, user_agent=user_agent)

    # ------------------------------------------------------------- CIK lookup
    def ticker_map(self) -> dict[str, dict]:
        cached = self.db.cache_get("edgar:tickers")
        if cached:
            return cached
        data = self.http.get(TICKERS_URL).json()
        result = {row["ticker"].upper(): {"cik": row["cik_str"], "name": row["title"]}
                  for row in data.values()}
        self.db.cache_set("edgar:tickers", result, ttl_sec=7 * 86400)
        return result

    def cik_for(self, ticker: str) -> int | None:
        entry = self.ticker_map().get(ticker.upper())
        return int(entry["cik"]) if entry else None

    # --------------------------------------------------------------- filings
    def recent_filings(self, ticker: str, forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
                       limit: int = 12) -> list[dict]:
        cik = self.cik_for(ticker)
        if cik is None:
            return []
        key = f"edgar:subs:{cik}"
        subs = self.db.cache_get(key)
        if subs is None:
            subs = self.http.get(SUBMISSIONS_URL.format(cik=cik)).json()
            self.db.cache_set(key, subs, ttl_sec=6 * 3600)
        recent = subs.get("filings", {}).get("recent", {})
        out = []
        for i, form in enumerate(recent.get("form", [])):
            if form in forms:
                accession = recent["accessionNumber"][i].replace("-", "")
                out.append({
                    "form": form,
                    "filed": recent["filingDate"][i],
                    "accession": recent["accessionNumber"][i],
                    "primary_doc": recent["primaryDocument"][i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
                           f"{recent['primaryDocument'][i]}",
                })
            if len(out) >= limit:
                break
        return out

    def full_text_search(self, query: str, forms: str = "", limit: int = 10) -> list[dict]:
        params: dict[str, Any] = {"q": query}
        if forms:
            params["forms"] = forms
        try:
            data = self.http.get(FTS_URL, params=params).json()
        except Exception as e:  # noqa: BLE001 - optional capability
            log.warning("EDGAR full-text search unavailable: %s", e)
            return []
        hits = []
        for hit in data.get("hits", {}).get("hits", [])[:limit]:
            src = hit.get("_source", {})
            hits.append({
                "form": src.get("file_type"),
                "filed": src.get("file_date"),
                "company": (src.get("display_names") or [""])[0],
                "excerpt": src.get("file_description", ""),
                "id": hit.get("_id", ""),
            })
        return hits

    # -------------------------------------------------------- XBRL fundamentals
    def company_facts(self, ticker: str) -> dict | None:
        cik = self.cik_for(ticker)
        if cik is None:
            return None
        key = f"edgar:facts:{cik}"
        cached = self.db.cache_get(key)
        if cached:
            return cached
        try:
            facts = self.http.get(FACTS_URL.format(cik=cik)).json()
        except Exception as e:  # noqa: BLE001
            log.warning("companyfacts failed for %s: %s", ticker, e)
            return None
        self.db.cache_set(key, facts, ttl_sec=24 * 3600)
        return facts

    def fundamentals(self, ticker: str, years: int = 4) -> dict[str, list[dict]] | None:
        """Normalize XBRL into {concept: [{fy, end, val}, ...]} newest-first annual series."""
        facts = self.company_facts(ticker)
        if not facts:
            return None
        gaap = facts.get("facts", {}).get("us-gaap", {})
        dei = facts.get("facts", {}).get("dei", {})
        out: dict[str, list[dict]] = {}
        for concept, tags in TAG_MAP.items():
            series = self._annual_series(gaap, tags)
            if series:
                out[concept] = series[:years]
        shares = self._annual_series(dei, ["EntityCommonStockSharesOutstanding"],
                                     unit_keys=("shares",))
        if not shares:
            shares = self._annual_series(gaap, ["CommonStockSharesOutstanding",
                                                "WeightedAverageNumberOfSharesOutstandingBasic"],
                                         unit_keys=("shares",))
        if shares:
            out["shares"] = shares[:years]
        return out or None

    @staticmethod
    def _annual_series(taxonomy: dict, tags: list[str],
                       unit_keys: tuple[str, ...] = ("USD",)) -> list[dict]:
        for tag in tags:
            node = taxonomy.get(tag)
            if not node:
                continue
            units = node.get("units", {})
            values = None
            for uk in unit_keys:
                if uk in units:
                    values = units[uk]
                    break
            if not values:
                continue
            # keep 10-K (FY) datapoints; dedupe by fiscal end date, newest first
            annual: dict[str, dict] = {}
            for v in values:
                if v.get("form") not in ("10-K", "20-F", "10-K/A") and v.get("fp") != "FY":
                    continue
                end = v.get("end")
                if not end or v.get("val") is None:
                    continue
                # prefer datapoints covering a full year when start is given
                if v.get("start"):
                    try:
                        span = (int(end[:4]) - int(v["start"][:4]))
                        if span == 0 and end[5:7] == v["start"][5:7]:
                            continue  # instant duplicates fine; skip sub-annual durations
                    except (ValueError, TypeError):
                        pass
                prev = annual.get(end)
                if prev is None or (v.get("fy") or 0) >= (prev.get("fy") or 0):
                    annual[end] = {"fy": v.get("fy"), "end": end, "val": v["val"]}
            if annual:
                return sorted(annual.values(), key=lambda r: r["end"], reverse=True)
        return []
