"""EU / UK annual reports (ESEF) -> SEC companyfacts shape; ticker -> LEI matching."""
from pmdesk import esef, market, sec


def test_suffix_and_yahoo_symbol():
    assert esef.is_eu("SHEL.L") and esef.is_eu("MC.PA") and esef.is_eu("NOVO-B.CO")
    assert not esef.is_eu("AAPL") and not esef.is_eu("BRK.B") and not esef.is_eu("6594.T")
    assert market.yahoo_symbol("SHEL.L") == "SHEL.L" and market.yahoo_symbol("BRK.B") == "BRK-B"


def test_name_variants_and_match():
    v = esef.name_variants("LVMH Moët Hennessy - Louis Vuitton, Société Européenne")
    assert "LVMH Moet Hennessy" in v
    cands = [{"lei": "US1", "name": "LVMH MOET HENNESSY LOUIS VUITTON INC.", "country": "US"},
             {"lei": "FR1", "name": "LVMH MOET HENNESSY LOUIS VUITTON", "country": "FR"}]
    assert esef.match_lei("LVMH Moët Hennessy - Louis Vuitton, Société Européenne", "FR", cands) == "FR1"
    assert esef.match_lei("Shell plc", "GB", [{"lei": "X", "name": "SHELLY LTD", "country": "US"}]) is None
    # a Jersey-registered London listing is fine
    assert esef.match_lei("Glencore plc", "GB", [{"lei": "JE1", "name": "GLENCORE PLC", "country": "JE"}]) == "JE1"


def test_extract_periods_units_and_dimensions():
    doc = {"facts": {
        "a": {"value": "100", "dimensions": {"concept": "ifrs-full:Revenue", "entity": "x", "unit": "iso4217:EUR",
                                             "period": "2025-01-01T00:00:00/2026-01-01T00:00:00"}},
        "b": {"value": "40", "dimensions": {"concept": "ifrs-full:Equity", "entity": "x", "unit": "iso4217:EUR", "period": "2026-01-01T00:00:00"}},
        "c": {"value": "7", "dimensions": {"concept": "ifrs-full:Revenue", "entity": "x", "unit": "iso4217:EUR",
                                           "period": "2025-01-01T00:00:00/2026-01-01T00:00:00", "ifrs-full:SegmentsAxis": "seg"}},
        "d": {"value": "1.5", "dimensions": {"concept": "ifrs-full:DilutedEarningsLossPerShare", "entity": "x",
                                             "unit": "iso4217:EUR/xbrli:shares", "period": "2025-01-01T00:00:00/2026-01-01T00:00:00"}},
        "e": {"value": "9", "dimensions": {"concept": "acme:OwnTag", "entity": "x", "unit": "iso4217:EUR", "period": "2026-01-01T00:00:00"}},
    }}
    ex = esef.extract(doc, "esef-1", "2026-03-01")
    rev = ex["Revenue"]["EUR"]
    assert rev == [{"start": "2025-01-01", "end": "2025-12-31", "val": 100.0, "accn": "esef-1", "fy": 2025, "fp": "FY", "form": "20-F", "filed": "2026-03-01"}]
    assert ex["Equity"]["EUR"][0]["end"] == "2025-12-31" and "start" not in ex["Equity"]["EUR"][0]
    assert ex["DilutedEarningsLossPerShare"]["EUR/shares"][0]["val"] == 1.5
    assert "OwnTag" not in ex  # company-specific tags are left out


def test_merge_newer_wins_and_statements_read_it(monkeypatch):
    new = {"Revenue": {"EUR": [{"start": "2024-01-01", "end": "2024-12-31", "val": 90.0, "fp": "FY", "form": "20-F", "filed": "2026-03-01"},
                               {"start": "2025-01-01", "end": "2025-12-31", "val": 100.0, "fp": "FY", "form": "20-F", "filed": "2026-03-01"}]},
           "Assets": {"EUR": [{"end": "2025-12-31", "val": 500.0, "fp": "FY", "form": "20-F", "filed": "2026-03-01"}]}}
    old = {"Revenue": {"EUR": [{"start": "2024-01-01", "end": "2024-12-31", "val": 88.0, "fp": "FY", "form": "20-F", "filed": "2025-03-01"}]}}
    m = esef.merge([new, old])
    assert [r["val"] for r in m["Revenue"]["units"]["EUR"]] == [90.0, 100.0]  # the restated 2024 figure wins
    monkeypatch.setattr(sec, "facts", lambda t: None)
    monkeypatch.setattr(esef, "facts", lambda t: {"entityName": "Acme", "facts": {"ifrs-full": m}, "source": "ESEF", "lei": "L1", "filings": []})
    st = sec.statements("ACME.PA")
    rows = {r["key"]: r for s in st["statements"].values() for r in s}
    assert st["ok"] and st["source"] == "ESEF" and st["currency"] == "EUR"
    assert rows["rev"]["A"]["2025-12-31"] == 100.0 and rows["assets"]["A"]["2025-12-31"] == 500.0
