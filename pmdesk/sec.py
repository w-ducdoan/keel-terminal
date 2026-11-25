"""Full financial statements from SEC filings (data.sec.gov XBRL company facts).

One request per company returns every number it has tagged in its 10-K and 10-Q filings. This module
turns those facts into clean annual and quarterly income statements, balance sheets and cash flow
statements, derives fourth quarters and un-accumulates year-to-date cash flows, and computes ratios.

SEC requires a contact in every automated request. Keel never stores one itself: you set it in
.secrets/sec_user_agent (git-ignored) or the KEEL_SEC_CONTACT environment variable.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date
from typing import Any

import requests

from .config import DATA_DIR, SECRETS_DIR

CACHE = DATA_DIR / "sec"
_lock = threading.Lock()
_mem: dict[str, tuple[float, Any]] = {}


class NoContact(RuntimeError):
    """SEC.gov needs a contact in the User-Agent and none is set."""


def _ua() -> dict[str, str]:
    c = os.environ.get("KEEL_SEC_CONTACT")
    f = SECRETS_DIR / "sec_user_agent"
    if not c and f.exists():
        c = f.read_text(encoding="utf-8").strip()
    if not c or "@" not in c:
        raise NoContact("SEC.gov needs a contact email. Set it once in .secrets/sec_user_agent (see README).")
    return {"User-Agent": c, "Accept-Encoding": "gzip, deflate"}


def _get_json(url: str, cache_file: str, max_age: float) -> Any:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / cache_file
    if p.exists() and time.time() - p.stat().st_mtime < max_age:
        return json.loads(p.read_text(encoding="utf-8"))
    with _lock:  # SEC allows 10 requests a second; one at a time is plenty here
        r = requests.get(url, headers=_ua(), timeout=60)
        time.sleep(0.15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    p.write_text(r.text, encoding="utf-8")
    return r.json()


def cik(ticker: str) -> int | None:
    data = _get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", 7 * 86400)
    t = ticker.upper().replace(".", "-")
    for v in (data or {}).values():
        if v["ticker"].upper() in (t, ticker.upper()):
            return int(v["cik_str"])
    return None


def facts(ticker: str) -> dict[str, Any] | None:
    c = cik(ticker)
    if c is None:
        return None
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c:010d}.json", f"CIK{c:010d}.json", 86400)


# ---------- statement definitions ----------
# (key, label, [tags in priority order], kind, indent, bold). kind: flow (income / cash flow) or instant (balance).
IS = [
    ("rev", "Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet", "Revenue", "RevenueFromContractsWithCustomers"], 0, True),
    ("cogs", "Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices", "CostOfSales"], 1, False),
    ("gp", "Gross profit", ["GrossProfit"], 0, True),
    ("rnd", "Research & development", ["ResearchAndDevelopmentExpense"], 1, False),
    ("sga", "Selling, general & administrative", ["SellingGeneralAndAdministrativeExpense"], 1, False),
    ("da", "Depreciation & amortization", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortisationExpense", "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"], 1, False),
    ("restr", "Restructuring", ["RestructuringCharges", "RestructuringAndRelatedCostIncurredCost"], 1, False),
    ("opex", "Total operating expenses", ["OperatingExpenses", "CostsAndExpenses"], 1, False),
    ("ebit", "Operating income", ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"], 0, True),
    ("intexp", "Interest expense", ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "FinanceCosts"], 1, False),
    ("intinc", "Interest income", ["InvestmentIncomeInterest", "InterestIncomeOther", "FinanceIncome", "InterestRevenueExpense"], 1, False),
    ("othinc", "Other income / (expense)", ["OtherNonoperatingIncomeExpense", "NonoperatingIncomeExpense"], 1, False),
    ("pretax", "Pretax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments", "ProfitLossBeforeTax"], 0, True),
    ("tax", "Income tax", ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations"], 1, False),
    ("ni", "Net income (to common)", ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], 0, True),
    ("eps", "Diluted EPS", ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare", "DilutedEarningsLossPerShareFromContinuingOperations"], 0, False),
    ("eps_b", "Basic EPS", ["EarningsPerShareBasic", "BasicEarningsLossPerShare", "BasicEarningsLossPerShareFromContinuingOperations"], 1, False),
    ("shares", "Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares", "WeightedAverageShares"], 0, False),
    ("sbc", "Stock-based compensation", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense", "ExpenseFromSharebasedPaymentTransactionsWithEmployees"], 1, False),
    ("div_ps", "Dividends per share", ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid", "DividendsRecognisedAsDistributionsToOwnersPerShare"], 1, False),
]
BS = [
    ("cash", "Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash", "CashAndCashEquivalents"], 1, False),
    ("sti", "Short-term investments", ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "CurrentInvestments", "OtherCurrentFinancialAssets"], 1, False),
    ("ar", "Accounts receivable", ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"], 1, False),
    ("inv", "Inventory", ["InventoryNet", "Inventories"], 1, False),
    ("ca", "Total current assets", ["AssetsCurrent", "CurrentAssets"], 0, True),
    ("ppe", "Property, plant & equipment (net)", ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipment"], 1, False),
    ("gw", "Goodwill", ["Goodwill"], 1, False),
    ("intang", "Intangible assets (net)", ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet", "IntangibleAssetsOtherThanGoodwill"], 1, False),
    ("rou", "Operating lease right-of-use assets", ["OperatingLeaseRightOfUseAsset", "RightofuseAssets"], 1, False),
    ("assets", "Total assets", ["Assets"], 0, True),
    ("ap", "Accounts payable", ["AccountsPayableCurrent", "TradeAndOtherCurrentPayables", "CurrentTradePayables", "TradeAndOtherCurrentPayablesToTradeSuppliers"], 1, False),
    ("defrev", "Deferred revenue (current)", ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent", "CurrentContractLiabilities"], 1, False),
    ("std", "Short-term debt & current portion", ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "LongTermDebtAndCapitalLeaseObligationsCurrent", "CurrentBorrowings", "ShorttermBorrowings", "CurrentPortionOfLongtermBorrowings"], 1, False),
    ("cl", "Total current liabilities", ["LiabilitiesCurrent", "CurrentLiabilities"], 0, True),
    ("ltd", "Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt", "NoncurrentBorrowings", "LongtermBorrowings"], 1, False),
    ("oplease", "Operating lease liabilities (long-term)", ["OperatingLeaseLiabilityNoncurrent", "NoncurrentLeaseLiabilities"], 1, False),
    ("liab", "Total liabilities", ["Liabilities"], 0, True),
    ("equity", "Shareholders' equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "EquityAttributableToOwnersOfParent", "Equity"], 0, True),
    ("treas", "Treasury stock", ["TreasuryStockValue", "TreasuryStockCommonValue", "TreasuryShares"], 1, False),
    ("re", "Retained earnings", ["RetainedEarningsAccumulatedDeficit", "RetainedEarnings"], 1, False),
    ("shout", "Shares outstanding", ["CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding"], 0, False),
]
CF = [
    ("cf_ni", "Net income", ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], 1, False),
    ("cf_da", "Depreciation & amortization", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "AdjustmentsForDepreciationAndAmortisationExpense"], 1, False),
    ("cf_sbc", "Stock-based compensation", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense", "AdjustmentsForSharebasedPayments"], 1, False),
    ("cf_wc", "Change in working capital", ["IncreaseDecreaseInOperatingCapital", "IncreaseDecreaseInWorkingCapital"], 1, False),
    ("cfo", "Cash from operations", ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "CashFlowsFromUsedInOperatingActivities"], 0, True),
    ("capex", "Capital expenditures", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], 1, False),
    ("acq", "Acquisitions", ["PaymentsToAcquireBusinessesNetOfCashAcquired", "CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities"], 1, False),
    ("cfi", "Cash from investing", ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations", "CashFlowsFromUsedInInvestingActivities"], 0, True),
    ("debt_in", "Debt issued", ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromBorrowingsClassifiedAsFinancingActivities"], 1, False),
    ("debt_out", "Debt repaid", ["RepaymentsOfLongTermDebt", "RepaymentsOfDebt", "RepaymentsOfBorrowingsClassifiedAsFinancingActivities"], 1, False),
    ("buyback", "Share buybacks", ["PaymentsForRepurchaseOfCommonStock", "PurchaseOfTreasuryShares", "PaymentsToAcquireOrRedeemEntitysShares"], 1, False),
    ("div", "Dividends paid", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "DividendsPaidClassifiedAsFinancingActivities"], 1, False),
    ("cff", "Cash from financing", ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations", "CashFlowsFromUsedInFinancingActivities"], 0, True),
]
KIND = {"income": ("flow", IS), "balance": ("instant", BS), "cashflow": ("flow", CF)}


def _days(f: dict[str, Any]) -> int | None:
    if not f.get("start"):
        return None
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


NON_ADDITIVE = {"eps", "eps_b", "shares", "div_ps", "shout"}  # averages and per-share: never derive by subtraction


FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A")


def _series(units: dict[str, list[dict[str, Any]]], kind: str, additive: bool = True,
            cur: str | None = None) -> dict[str, dict[str, float]]:
    """Per-period values from one concept's facts: annual (FY), quarterly (3-month), and YTD pieces.
    The latest filing wins for restated periods. cur keeps only that reporting currency (plus share counts
    and pure numbers), so a convenience EUR or USD copy never mixes into CHF figures.
    Returns {"A": {end: v}, "Q": {end: v}}."""
    if cur:
        units = {u: v for u, v in units.items() if u in (cur, f"{cur}/shares", "shares", "pure")}
    facts_ = [f for us in units.values() for f in us if f.get("form") in FORMS]
    best: dict[tuple[str | None, str], dict[str, Any]] = {}
    for f in facts_:
        k = (f.get("start"), f["end"])
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    A: dict[str, float] = {}
    Q: dict[str, float] = {}
    if kind == "instant":
        # Year-end dates come from the annual filings; the value is the latest filed for that date
        # (a later 10-Q repeats the prior year-end balance as a comparative, which must not hide it).
        fy_ends = {f["end"] for f in facts_ if not f.get("start") and f.get("form", "").startswith(("10-K", "20-F", "40-F"))
                   and f.get("fp") == "FY"}
        # A comparative balance repeated in a later annual filing is still a year-end
        fy_ends |= {f["end"] for f in facts_ if not f.get("start") and f.get("form", "").startswith(("20-F", "40-F"))}
        for (s, e), f in best.items():
            if s is None:
                Q[e] = f["val"]
                if e in fy_ends:
                    A[e] = f["val"]
        return {"A": A, "Q": Q}
    ytd: dict[str, list[tuple[int, str, float]]] = {}
    for (s, e), f in best.items():
        d = _days(f)
        if d is None:
            continue
        if 350 <= d <= 380:
            A[e] = f["val"]
        if 80 <= d <= 100:
            Q[e] = f["val"]
        ytd.setdefault(s, []).append((d, e, f["val"]))
    if not additive:
        return {"A": A, "Q": Q}
    # Un-accumulate year-to-date figures (cash flows are filed as 3, 6, 9 and 12 months from the year start)
    for s, pieces in ytd.items():
        pieces.sort()
        prev_v = 0.0
        prev_d = 0
        for d, e, v in pieces:
            band = round(d / 91)
            if band in (1, 2, 3, 4) and abs(d - 91 * band) <= 12 and e not in Q and round(prev_d / 91) == band - 1:
                Q[e] = v - prev_v
            prev_v, prev_d = v, d
    return {"A": A, "Q": Q}


def _pick(gaap: dict[str, Any], dei: dict[str, Any], tags: list[str], kind: str,
          additive: bool = True, cur: str | None = None) -> tuple[str | None, dict[str, dict[str, float]]]:
    """Merge the candidate tags: the first tag fills a period, later tags only fill periods it lacks
    (companies switch tags over the years, e.g. Revenues -> RevenueFromContractWithCustomer...)."""
    merged: dict[str, dict[str, float]] = {"A": {}, "Q": {}}
    used = None
    for t in tags:
        src = dei if t.startswith("dei:") else gaap
        c = src.get(t.split(":")[-1])
        if not c:
            continue
        s = _series(c.get("units", {}), kind, additive, None if t.startswith("dei:") else cur)
        for k in ("A", "Q"):
            for e, v in s[k].items():
                merged[k].setdefault(e, v)
        used = used or t
    return used, merged


def statements(ticker: str, years: int = 12, quarters: int = 16) -> dict[str, Any]:
    from . import esef
    try:
        f = facts(ticker)
    except NoContact as e:
        if not esef.is_eu(ticker):
            return {"ok": False, "error": str(e), "needs_contact": True}
        f = None
    except Exception as e:
        return {"ok": False, "error": f"SEC request failed: {str(e)[:160]}"}
    if not f and esef.is_eu(ticker):  # EU / UK listing: its annual reports in ESEF (filings.xbrl.org)
        try:
            f = esef.facts(ticker)
        except Exception as e:
            return {"ok": False, "error": f"European filings lookup failed: {str(e)[:160]}"}
        if not f:
            return {"ok": False, "error": f"No ESEF annual reports found for {ticker} on filings.xbrl.org (or its company couldn't be matched; "
                                          "pin it in config/lei_overrides.yaml)."}
    if not f:
        return {"ok": False, "error": f"{ticker} has no XBRL filings on SEC (foreign filer, fund or not SEC-registered)."}
    fx = f.get("facts", {})
    gaap, standard = (fx.get("us-gaap"), "US GAAP") if fx.get("us-gaap") else (fx.get("ifrs-full"), "IFRS")
    dei = fx.get("dei", {})
    if not gaap:
        return {"ok": False, "error": f"{ticker} has no US GAAP or IFRS tags on SEC; statements aren't available."}
    cur = reporting_currency(gaap)
    out: dict[str, Any] = {"ok": True, "ticker": ticker, "entity": f.get("entityName"), "cik": f.get("cik"), "statements": {},
                           "standard": standard, "currency": cur}
    lines: dict[str, dict[str, dict[str, float]]] = {}
    for name, (kind, rows) in KIND.items():
        rs = []
        for key, label, tags, indent, bold in rows:
            tag, s = _pick(gaap, dei, tags, kind, key not in NON_ADDITIVE, cur)
            if tag is None:
                continue
            lines[key] = s
            rs.append({"key": key, "label": label, "tag": tag, "indent": indent, "bold": bold, "A": s["A"], "Q": s["Q"]})
        out["statements"][name] = rs
    # Derive missing subtotals where the parts exist
    _derive(lines, out)
    # Periods: last N fiscal year ends and quarter ends that have revenue or assets
    anchor = lines.get("rev") or lines.get("assets") or {"A": {}, "Q": {}}
    a_ends = sorted(anchor["A"])[-years:]
    q_ends = sorted(set(anchor["Q"]) | set((lines.get("assets") or {"Q": {}})["Q"]))[-quarters:]
    out["periods"] = {"A": a_ends, "Q": q_ends}
    out["ratios"] = ratios(lines, a_ends, q_ends)
    # Every other tag the company files (the "all lines" view), with SEC's own label
    mapped = {r["tag"].split(":")[-1] for st in out["statements"].values() for r in st}
    extra = []
    for tag, c in gaap.items():
        if tag in mapped:
            continue
        units = c.get("units", {})
        if cur not in units and f"{cur}/shares" not in units and "shares" not in units:
            continue
        kind = "instant" if all(not x.get("start") for x in units.get(cur, units.get("shares", []))[:5]) else "flow"
        s = _series(units, kind, cur=cur)
        recent = [e for e in s["A"] if e in a_ends[-5:]]
        if not recent:
            continue
        extra.append({"tag": tag, "label": c.get("label") or tag, "kind": kind, "A": {e: s["A"][e] for e in a_ends if e in s["A"]},
                      "Q": {e: s["Q"][e] for e in q_ends if e in s["Q"]}})
    out["all_tags"] = sorted(extra, key=lambda x: x["label"])[:600]
    out["filings_url"] = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={f.get('cik')}&type={'10-K' if standard == 'US GAAP' else '20-F'}"
    if f.get("source") == "ESEF":
        out["source"] = "ESEF"
        out["lei"] = f.get("lei")
        latest = (f.get("filings") or [{}])[0]
        out["filings_url"] = latest.get("viewer") or f"https://filings.xbrl.org/?entity={f.get('lei')}"
        out["note"] = (f"Source: annual reports in ESEF (the format EU and UK listed companies file with their national regulator), "
                       f"from filings.xbrl.org; reporting in {cur}. Annual figures only (European filers don't tag quarterly results); "
                       "standard IFRS lines only, so items a company tags under its own names show as missing.")
    elif str(f.get("source", "")).startswith("SEC (dual"):
        out["source"] = "SEC"
        out["note"] = (f"This listing's company also files with SEC (US ticker {f.get('sec_ticker')}): figures are from those filings, "
                       f"{standard}, reporting in {cur}.")
    elif standard == "IFRS":
        out["note"] = (f"IFRS filer reporting in {cur}: annual figures from the 20-F; foreign filers do not tag quarterly "
                       "results in XBRL, so quarters are thin or missing. Line names follow IFRS (e.g. finance costs for interest).")
    return out


def reporting_currency(taxonomy: dict[str, Any]) -> str:
    """The currency most of the company's money facts are in (USD for US filers, CHF for On Holding)."""
    count: dict[str, int] = {}
    for c in taxonomy.values():
        for u, xs in c.get("units", {}).items():
            if re.fullmatch(r"[A-Z]{3}", u):
                count[u] = count.get(u, 0) + len(xs)
    return max(count, key=count.get) if count else "USD"


def _derive(lines: dict[str, dict[str, dict[str, float]]], out: dict[str, Any]) -> None:
    def put(key: str, label: str, fn, stmt: str, indent: int = 0, bold: bool = True, after: str | None = None) -> None:
        s = {"A": {}, "Q": {}}
        for k in ("A", "Q"):
            for e in set().union(*[lines.get(x, {}).get(k, {}).keys() for x in ("rev", "ebit", "pretax", "cfo", "assets")]):
                try:
                    v = fn(k, e)
                except (KeyError, TypeError):
                    v = None
                if v is not None:
                    s[k][e] = v
        if key in lines:  # the company files this line in some periods: fill only the periods it skipped
            for k in ("A", "Q"):
                for e, v in s[k].items():
                    lines[key][k].setdefault(e, v)
            return
        if not s["A"] and not s["Q"]:
            return
        lines[key] = s
        row = {"key": key, "label": label + " (derived)", "tag": "derived", "indent": indent, "bold": bold, "A": s["A"], "Q": s["Q"]}
        rows = out["statements"][stmt]
        pos = next((i + 1 for i, r in enumerate(rows) if r["key"] == after), len(rows))
        rows.insert(pos, row)

    g = lambda key, k, e: lines[key][k][e]  # noqa: E731
    put("gp", "Gross profit", lambda k, e: g("rev", k, e) - g("cogs", k, e), "income", after="cogs")
    put("ebit", "Operating income", lambda k, e: g("pretax", k, e) + abs(lines.get("intexp", {}).get(k, {}).get(e) or 0), "income", after="opex")
    put("ebitda", "EBITDA", lambda k, e: g("ebit", k, e) + (lines.get("da") or lines["cf_da"])[k][e], "income", after="ebit")
    put("fcf", "Free cash flow", lambda k, e: g("cfo", k, e) - abs(g("capex", k, e)), "cashflow", after="capex")
    put("debt", "Total debt", lambda k, e: (lines.get("ltd", {}).get(k, {}).get(e) or 0) + (lines.get("std", {}).get(k, {}).get(e) or 0) or None, "balance", after="ltd")
    put("liab", "Total liabilities", lambda k, e: g("assets", k, e) - g("equity", k, e), "balance", after="oplease")
    put("netdebt", "Net debt", lambda k, e: g("debt", k, e) - (lines.get("cash", {}).get(k, {}).get(e) or 0) - (lines.get("sti", {}).get(k, {}).get(e) or 0), "balance", after="debt")


def ratios(L: dict[str, dict[str, dict[str, float]]], a_ends: list[str], q_ends: list[str]) -> dict[str, Any]:
    """Ratio groups by period. Returns {group: [{label, fmt, A: {end: v}, Q: {...}}]}."""
    def v(key: str, k: str, e: str) -> float | None:
        return L.get(key, {}).get(k, {}).get(e)

    def prev(ends: list[str], e: str, back: int) -> str | None:
        i = ends.index(e)
        return ends[i - back] if i - back >= 0 else None

    def div(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    def ttm(key: str, e: str) -> float | None:
        if e not in q_ends:
            return None
        i = q_ends.index(e)
        if i < 3:
            return None
        vals = [v(key, "Q", x) for x in q_ends[i - 3:i + 1]]
        return sum(vals) if all(x is not None for x in vals) else None

    def flow(key: str, k: str, e: str) -> float | None:
        return v(key, "A", e) if k == "A" else ttm(key, e)

    def avg(key: str, k: str, e: str) -> float | None:
        ends = a_ends if k == "A" else q_ends
        p = prev(ends, e, 1 if k == "A" else 4)
        a, b = v(key, k, e), (v(key, k, p) if p else None)
        return (a + b) / 2 if a is not None and b is not None else a

    specs: list[tuple[str, str, str, Any]] = [
        ("Profitability", "Gross margin", "pct", lambda k, e: div(flow("gp", k, e), flow("rev", k, e))),
        ("Profitability", "EBITDA margin", "pct", lambda k, e: div(flow("ebitda", k, e), flow("rev", k, e))),
        ("Profitability", "Operating margin", "pct", lambda k, e: div(flow("ebit", k, e), flow("rev", k, e))),
        ("Profitability", "Pretax margin", "pct", lambda k, e: div(flow("pretax", k, e), flow("rev", k, e))),
        ("Profitability", "Net margin", "pct", lambda k, e: div(flow("ni", k, e), flow("rev", k, e))),
        ("Profitability", "FCF margin", "pct", lambda k, e: div(flow("fcf", k, e), flow("rev", k, e))),
        ("Profitability", "R&D / revenue", "pct", lambda k, e: div(flow("rnd", k, e), flow("rev", k, e))),
        ("Profitability", "SBC / revenue", "pct", lambda k, e: div(flow("sbc", k, e), flow("rev", k, e))),
        ("Profitability", "Effective tax rate", "pct", lambda k, e: div(flow("tax", k, e), flow("pretax", k, e))),
        ("Returns", "Return on equity", "pct", lambda k, e: div(flow("ni", k, e), avg("equity", k, e))),
        ("Returns", "Return on assets", "pct", lambda k, e: div(flow("ni", k, e), avg("assets", k, e))),
        ("Returns", "ROIC (after-tax operating income / debt + equity - cash)", "pct",
         lambda k, e: div((flow("ebit", k, e) or 0) * (1 - min(max(div(flow("tax", k, e), flow("pretax", k, e)) or 0.21, 0), 0.4)) if flow("ebit", k, e) is not None else None,
                          (v("debt", k, e) or 0) + (v("equity", k, e) or 0) - (v("cash", k, e) or 0) or None)),
        ("Returns", "Cash ROIC (FCF / invested capital)", "pct",
         lambda k, e: div(flow("fcf", k, e), (v("debt", k, e) or 0) + (v("equity", k, e) or 0) - (v("cash", k, e) or 0) or None)),
        ("Leverage", "Net debt / EBITDA", "x", lambda k, e: div(v("netdebt", k, e), flow("ebitda", k, e))),
        ("Leverage", "Debt / equity", "x", lambda k, e: div(v("debt", k, e), v("equity", k, e))),
        ("Leverage", "Interest coverage (EBIT / interest)", "x", lambda k, e: div(flow("ebit", k, e), abs(flow("intexp", k, e) or 0) or None)),
        ("Leverage", "Liabilities / assets", "pct", lambda k, e: div(v("liab", k, e), v("assets", k, e))),
        ("Liquidity", "Current ratio", "x", lambda k, e: div(v("ca", k, e), v("cl", k, e))),
        ("Liquidity", "Quick ratio", "x", lambda k, e: div((v("ca", k, e) or 0) - (v("inv", k, e) or 0), v("cl", k, e)) if v("ca", k, e) is not None else None),
        ("Liquidity", "Cash ratio", "x", lambda k, e: div((v("cash", k, e) or 0) + (v("sti", k, e) or 0), v("cl", k, e)) if v("cash", k, e) is not None else None),
        ("Efficiency", "Asset turnover", "x", lambda k, e: div(flow("rev", k, e), avg("assets", k, e))),
        ("Efficiency", "Days sales outstanding", "d", lambda k, e: div((v("ar", k, e) or 0) * 365, flow("rev", k, e)) if v("ar", k, e) is not None else None),
        ("Efficiency", "Days inventory", "d", lambda k, e: div((v("inv", k, e) or 0) * 365, flow("cogs", k, e)) if v("inv", k, e) is not None else None),
        ("Efficiency", "Days payables", "d", lambda k, e: div((v("ap", k, e) or 0) * 365, flow("cogs", k, e)) if v("ap", k, e) is not None else None),
        ("Efficiency", "Capex / revenue", "pct", lambda k, e: div(abs(flow("capex", k, e) or 0) or None, flow("rev", k, e))),
        ("Efficiency", "Capex / D&A", "x", lambda k, e: div(abs(flow("capex", k, e) or 0) or None, flow("cf_da", k, e) or flow("da", k, e))),
        ("Efficiency", "FCF conversion (FCF / net income)", "x", lambda k, e: div(flow("fcf", k, e), flow("ni", k, e))),
        ("Per share", "Diluted EPS", "n", lambda k, e: flow("eps", k, e) if k == "A" else ttm("eps", e)),
        ("Per share", "FCF / share", "n", lambda k, e: div(flow("fcf", k, e), v("shares", k, e) or (v("shares", "Q", e) if k == "Q" else None))),
        ("Per share", "Book value / share", "n", lambda k, e: div(v("equity", k, e), v("shout", k, e) or v("shares", k, e))),
        ("Per share", "Revenue / share", "n", lambda k, e: div(flow("rev", k, e), v("shares", k, e))),
    ]
    growth = [("Revenue", "rev"), ("EBITDA", "ebitda"), ("Operating income", "ebit"), ("Net income", "ni"), ("Diluted EPS", "eps"),
              ("Free cash flow", "fcf"), ("Diluted shares", "shares")]
    out: dict[str, list[dict[str, Any]]] = {}
    for grp, label, fmt, fn in specs:
        row = {"label": label, "fmt": fmt, "A": {}, "Q": {}}
        for k, ends in (("A", a_ends), ("Q", q_ends)):
            for e in ends:
                try:
                    x = fn(k, e)
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    x = None
                if x is not None:
                    row[k][e] = x
        if row["A"] or row["Q"]:
            out.setdefault(grp, []).append(row)
    for label, key in growth:
        row = {"label": f"{label} growth (YoY)", "fmt": "pct", "A": {}, "Q": {}}
        for k, ends in (("A", a_ends), ("Q", q_ends)):
            for e in ends:
                p = prev(ends, e, 1 if k == "A" else 4)
                a, b = (v(key, k, e), v(key, k, p)) if p else (None, None)
                if a is not None and b not in (None, 0) and b > 0:
                    row[k][e] = a / b - 1
        if row["A"] or row["Q"]:
            out.setdefault("Growth", []).append(row)
    return out


def valuation(st: dict[str, Any], prices: Any, share_scale: float = 1.0) -> list[dict[str, Any]]:
    """Multiples at each period end, using the closing price on or just before that date.
    prices: a pandas Series of daily closes indexed by date (unadjusted for dividends is fine here)."""
    if prices is None or len(prices) == 0 or not st.get("ok"):
        return []
    rows = {r["key"]: r for s in st["statements"].values() for r in s}
    a_ends, q_ends = st["periods"]["A"], st["periods"]["Q"]

    def px_at(e: str) -> float | None:
        s = prices.loc[:e]
        return float(s.iloc[-1]) if len(s) else None

    def val(key: str, k: str, e: str) -> float | None:
        r = rows.get(key)
        return r[k].get(e) if r else None

    def ttm(key: str, e: str) -> float | None:
        if e not in q_ends or q_ends.index(e) < 3:
            return None
        i = q_ends.index(e)
        xs = [val(key, "Q", x) for x in q_ends[i - 3:i + 1]]
        return sum(xs) if all(x is not None for x in xs) else None

    def flow(key: str, k: str, e: str) -> float | None:
        return val(key, "A", e) if k == "A" else ttm(key, e)

    def shares(k: str, e: str) -> float | None:
        s = val("shout", k, e) or val("shares", k, e) or (val("shares", "Q", e) if k == "A" else None)
        if not s and val("ni", k, e) and val("eps", k, e):  # no share count tagged (some IFRS filers): net income / EPS
            s = val("ni", k, e) / val("eps", k, e) if val("ni", k, e) / val("eps", k, e) > 0 else None
        return s * share_scale if s else None

    out = {}
    specs = [
        ("Price at period end", "n", lambda k, e, p: p),
        ("Market cap", "big", lambda k, e, p: p * shares(k, e) if shares(k, e) else None),
        ("Enterprise value", "big", lambda k, e, p: (p * shares(k, e) + (val("debt", k, e) or 0) - (val("cash", k, e) or 0)) if shares(k, e) else None),
        ("P/E", "x", lambda k, e, p: (p / flow("eps", k, e)) if (flow("eps", k, e) or 0) > 0 else None),
        ("EV / EBITDA", "x", lambda k, e, p: ((p * shares(k, e) + (val("debt", k, e) or 0) - (val("cash", k, e) or 0)) / flow("ebitda", k, e))
         if shares(k, e) and (flow("ebitda", k, e) or 0) > 0 else None),
        ("EV / revenue", "x", lambda k, e, p: ((p * shares(k, e) + (val("debt", k, e) or 0) - (val("cash", k, e) or 0)) / flow("rev", k, e))
         if shares(k, e) and flow("rev", k, e) else None),
        ("P / sales", "x", lambda k, e, p: (p * shares(k, e) / flow("rev", k, e)) if shares(k, e) and flow("rev", k, e) else None),
        ("P / book", "x", lambda k, e, p: (p * shares(k, e) / val("equity", k, e)) if shares(k, e) and (val("equity", k, e) or 0) > 0 else None),
        ("P / FCF", "x", lambda k, e, p: (p * shares(k, e) / flow("fcf", k, e)) if shares(k, e) and (flow("fcf", k, e) or 0) > 0 else None),
        ("FCF yield", "pct", lambda k, e, p: (flow("fcf", k, e) / (p * shares(k, e))) if shares(k, e) and flow("fcf", k, e) is not None else None),
        ("Dividend yield", "pct", lambda k, e, p: (val("div_ps", k, e) / p) if k == "A" and val("div_ps", k, e) else None),
    ]
    rows_out = []
    for label, fmt, fn in specs:
        r = {"label": label, "fmt": fmt, "A": {}, "Q": {}}
        for k, ends in (("A", a_ends), ("Q", q_ends)):
            for e in ends:
                p = px_at(e)
                if p is None:
                    continue
                try:
                    x = fn(k, e, p)
                except (TypeError, ZeroDivisionError):
                    x = None
                if x is not None:
                    r[k][e] = x
        if r["A"] or r["Q"]:
            rows_out.append(r)
    return rows_out
