from datetime import date, datetime, timezone

import pandas as pd

from pmdesk import newsrange


def test_short_name_keeps_distinctive_names():
    assert newsrange.short_name("Toast, Inc.") == "Toast"
    assert newsrange.short_name("On Holding AG") == "On Holding"
    assert newsrange.short_name("Apple Inc.") == "Apple"


def test_relevant_drops_common_word_noise():
    rel = lambda s, t="TOST", n="Toast, Inc.": newsrange.relevant(s, t, n)  # noqa: E731
    assert rel("Why Toast (TOST) Shares Are Plunging Today")
    assert rel("Toast stock slides despite higher guidance")
    assert not rel("Try this french toast recipe for Mother's Day")
    assert not rel("Cinnamon Toast Crunch + Pancakes = This Must-Buy New Boxed Mix")
    assert not rel("Russia keen on holding cultural festival", "ONON", "On Holding AG")
    assert rel("On Holding (ONON) Could Be 32% Undervalued", "ONON", "On Holding AG")
    assert not rel("ONON Dec 2026 30.000 put (ONON261204P00030000) stock price, news, quote", "ONON", "On Holding AG")


def test_windows_cover_range_without_gaps():
    w = newsrange._windows(date(2026, 1, 1), date(2026, 3, 15))
    assert w[0][0] == date(2026, 1, 1) and w[-1][1] == date(2026, 3, 15)
    for (a, b), (c, _) in zip(w, w[1:]):
        assert (c - b).days == 1


def test_move_stats_vs_spy_and_volume():
    idx = pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"])
    px = pd.DataFrame({"Close": [30, 30, 30, 29.38, 25.05], "Volume": [100, 100, 100, 100, 400]}, index=idx)
    spy = pd.DataFrame({"Close": [500, 500, 500, 500, 505]}, index=idx)
    m = newsrange.move_stats(px, spy, date(2026, 5, 8), date(2026, 5, 8))
    assert m["from"] == "2026-05-07" and m["to"] == "2026-05-08"
    assert round(m["ret"], 4) == round(25.05 / 29.38 - 1, 4)
    assert round(m["spy"], 4) == 0.01
    assert m["vol_x"] == 4.0


def test_company_merges_searches_and_recent(monkeypatch):
    t0 = datetime(2026, 11, 20, tzinfo=timezone.utc)
    def fake(t, name, a, b):
        return [{"title": "Toast (TOST) beats", "url": "u", "summary": "", "time": t0, "source": "A"},
                {"title": "Toast (TOST) beats", "url": "u2", "summary": "", "time": t0, "source": "B"}]
    monkeypatch.setattr(newsrange, "search", fake)
    recent = [{"time": "2026-11-23T10:00:00", "title": "Toast names new CFO", "summary": "", "source": "Your digest", "url": None, "kind": "digest"},
              {"time": "2025-01-01T10:00:00", "title": "Old story", "summary": "", "source": "Yahoo", "url": None, "kind": "news"}]
    out = newsrange.company("TOST", "Toast, Inc.", 60, recent, today=date(2026, 11, 24))
    titles = [x["title"] for x in out["items"]]
    assert titles == ["Toast names new CFO", "Toast (TOST) beats"]  # merged duplicates, out-of-range recent dropped
    assert out["ok"] and out["days"] == 60
