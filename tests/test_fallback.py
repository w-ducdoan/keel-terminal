"""SEC-based fallback (public edition when Yahoo refuses): ratios, filters, statements, the screen."""
import pandas as pd

from pmdesk import fallback, screener


def test_sic_sector():
    assert fallback.sic_sector(7372) == "Technology"
    assert fallback.sic_sector(2834) == "Healthcare"
    assert fallback.sic_sector(4911) == "Utilities"
    assert fallback.sic_sector(6022) == "Financial Services"
    assert fallback.sic_sector(None) is None


def test_metrics_basic_and_no_debt():
    f = {"rev": 1000.0, "rev_p": 800.0, "gp": 400.0, "ebit": 150.0, "ebit_p": 100.0, "da": 50.0, "da_p": 40.0, "ni": 100.0, "ni_p": 80.0,
         "eps": 2.0, "eps_p": 1.6, "cfo": 180.0, "cfo_p": 150.0, "capex": -60.0, "capex_p": -50.0,
         "cash": 300.0, "ca": 600.0, "cl": 300.0, "inv": 100.0, "equity": 800.0, "assets": 1500.0}
    m = fallback.metrics(f, 2000.0, {"last": 20.0, "chg52": -0.3, "beta": 1.1})
    assert m["pe"] == 20 and round(m["revg"], 1) == 25.0 and m["ebitdam"] == 20.0
    assert m["nd_ebitda"] == -1.5 and m["int_cov"] == 999.0  # no debt lines on a real balance sheet = no debt
    assert m["cur"] == 2.0 and round(m["quick"], 2) == 1.67 and m["chg52"] == -30.0
    f2 = {**f, "ltd": 500.0}  # debt but no interest tag: estimated at 5%
    assert fallback.metrics(f2, 2000.0, {})["int_cov"] == 200.0 / 25.0


def test_passes_and_screen(monkeypatch):
    rows = [{"t": "AAA", "name": "A", "sector": "Industrials", "industry": "x", "exchange": "NYSE", "fy": "2025",
             "px": {"last": 10.0, "chg_pct": 0.01, "chg52": -0.4, "hi52": 20.0, "lo52": 9.0},
             "m": {"mcap": 5e9, "beta": 1.0, "nd_ebitda": 1.0, "int_cov": 10.0, "cur": 2.0, "roe": 20.0, "chg52": -40.0, "ebitdam": 25.0}},
            {"t": "BBB", "name": "B", "sector": "Technology", "industry": "y", "exchange": "Nasdaq", "fy": "2025",
             "px": {"last": 5.0, "chg_pct": -0.02, "chg52": 0.1, "hi52": 6.0, "lo52": 3.0},
             "m": {"mcap": 1e9, "beta": 2.5, "nd_ebitda": 0.5, "int_cov": 20.0, "cur": 3.0, "roe": 30.0, "chg52": 10.0, "ebitdam": 30.0}}]
    monkeypatch.setattr(fallback, "table", lambda: rows)
    spec = screener.PRESETS["Quality under a cloud"]
    q, n = fallback.screen(screener._rows(spec), "chg52", True)
    assert n == 1 and q[0]["symbol"] == "AAA" and q[0]["marketCap"] == 5e9 and q[0]["_fb"]["roe"] == 20.0
    q, n = fallback.screen([{"sector": ["Technology"]}], "mcap", False)
    assert [x["symbol"] for x in q] == ["BBB"]
    assert fallback.passes({"mcap": 5e8}, None, {"field": "mcap", "op": "btwn", "value": [3e8, 1e9]})
    assert not fallback.passes({"mcap": None}, None, {"field": "mcap", "op": "gte", "value": 1})


def test_from_statements_converts_currency(monkeypatch):
    monkeypatch.setattr(fallback, "fx_usd", lambda cur: 1.25 if cur == "CHF" else 1.0)
    st = {"ok": True, "currency": "CHF", "standard": "IFRS", "periods": {"A": ["2024-12-31", "2025-12-31"]},
          "statements": {"is": [{"key": "rev", "A": {"2024-12-31": 80.0, "2025-12-31": 100.0}},
                                {"key": "shares", "A": {"2025-12-31": 10.0}}]}}
    f = fallback.from_statements(st)
    assert f["rev"] == 125.0 and f["rev_p"] == 100.0 and f["shares"] == 10.0 and f["currency"] == "CHF"


def test_price_stats():
    idx = pd.date_range("2025-01-01", periods=100, freq="B")
    c = pd.Series(range(100, 200), index=idx, dtype=float)
    st = fallback.price_stats(c, pd.Series(1000.0, index=idx), c * 2)
    assert st["last"] == 199 and st["hi52"] == 199 and st["lo52"] == 100 and round(st["chg52"], 2) == 0.99 and st["avgvol"] == 1000
    assert st["beta"] > 0
