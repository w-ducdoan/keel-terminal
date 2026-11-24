from pmdesk import heatmap as hm


def test_build_sp500_and_book(monkeypatch):
    monkeypatch.setattr(hm, "members", lambda: [{"t": "AAA", "name": "Aaa", "sector": "Tech", "industry": "Chips"},
                                                {"t": "BRK.B", "name": "Berkshire", "sector": "Financials", "industry": "Insurance"},
                                                {"t": "ZZZ", "name": "No quote", "sector": "Tech", "industry": "Chips"}])
    monkeypatch.setattr(hm, "quotes", lambda: {"AAA": {"mcap": 3e12, "chg": -0.07, "name": "Aaa Inc"},
                                               "BRK-B": {"mcap": 1e12, "chg": 0.01, "name": "Berkshire"}})
    out = hm.build("sp500", "1D", {})
    got = {x["t"]: x for x in out["items"]}
    assert set(got) == {"AAA", "BRK.B"}                     # BRK.B found under Yahoo's BRK-B; no quote = left out
    assert got["AAA"]["chg"] == -0.07 and out["breadth"] == {"up": 1, "down": 1, "weighted": (3e12 * -0.07 + 1e12 * 0.01) / 4e12}
    snap = {"positions": [{"ticker": "AAA", "company": "Aaa", "sector": "Tech", "bucket": "AI", "value": 250.0, "day_pct": 0.02}]}
    b = hm.build("book", "1D", snap)
    assert b["items"][0]["size"] == 250.0 and b["items"][0]["industry"] == "AI" and b["items"][0]["chg"] == 0.02
