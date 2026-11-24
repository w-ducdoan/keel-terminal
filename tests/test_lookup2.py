from pmdesk import lookup2 as lk


def test_index_proxies_map_licensed_indexes_to_funds():
    for q in ("barclays aggregate", "Bloomberg Agg index", "us agg"):
        syms = [x["symbol"] for x in lk.index_proxies(q)]
        assert syms[:2] == ["AGG", "BND"], q
    r2k = lk.index_proxies("russell 2000")
    assert r2k[0] == {"symbol": "^RUT", "index": "Russell 2000", "proxy": False} and r2k[1]["proxy"]
    assert lk.index_proxies("toast") == []


def test_short_title():
    assert lk.short_title("Consumer Price Index for All Urban Consumers: All Items Less Food and Energy in U.S. City Average") == "CPI: Core (ex food & energy)"
    assert lk.short_title("Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, Quoted on an Investment Basis") == "Treasury yield, 10-Year"


def test_short_series_kinds(monkeypatch):
    monkeypatch.setattr(lk, "short_history", lambda t: [{"date": "2026-10-15", "short": 36_000_000, "dtc": 4.6, "adv": 7_900_000},
                                                        {"date": "2026-10-30", "short": 34_700_000, "dtc": 4.5, "adv": 7_700_000}])
    assert list(lk.short_series("TOST", "short", None)) == [36_000_000, 34_700_000]
    assert list(lk.short_series("TOST", "dtc", None)) == [4.6, 4.5]
    pct = lk.short_series("TOST", "pct", 487_743_690)
    assert abs(pct.iloc[-1] - 34_700_000 / 487_743_690) < 1e-12
