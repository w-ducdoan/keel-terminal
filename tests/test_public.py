"""Public edition: market routes only, no writes, the page carries the flag."""
from fastapi.testclient import TestClient

from pmdesk import public


def test_allowlist():
    ok = ["/api/state", "/api/series", "/api/lookup", "/api/lookup/more", "/api/news", "/api/why/TOST", "/api/cnrange/TOST",
          "/api/equity/TOST/card", "/api/equity/TOST/fa", "/api/equity/TOST/splc", "/api/13f/fund/1067983", "/api/heatmap", "/", "/static/js/core.js"]
    for p in ok:
        assert public.allowed("GET", p), p
    no = ["/api/journal", "/api/reconcile", "/api/performance", "/api/attribution", "/api/tracking", "/api/whatif",
          "/api/equity/TOST/rh", "/api/equity/TOST/cockpit", "/api/equity/TOST/ern", "/api/themes", "/api/brief", "/api/crowding"]
    for p in no:
        assert not public.allowed("GET", p), p
    for p in ["/api/refresh", "/api/open", "/api/13f/funds", "/api/rv/TOST/peers", "/api/state"]:
        assert not public.allowed("POST", p), p


def test_inject_sets_flag_and_script():
    html = ('<title>Keel · Portfolio Terminal</title>\n  <script src="/static/js/core.js?v=9"></script>\n'
            '  <script src="/static/js/shell.js?v=9"></script>\n</body>')
    out = public.inject(html)
    assert "Market Terminal" in out and "window.KEEL_PUBLIC = true" in out and "public.js?v=9" in out
    assert out.index("KEEL_PUBLIC") < out.index("core.js") < out.index("public.js")


def test_public_app_refuses_private_routes(monkeypatch):
    monkeypatch.setenv("KEEL_PUBLIC", "1")
    from pmdesk.server import create_app
    c = TestClient(create_app(start_loop=False))
    assert c.post("/api/refresh").status_code == 404
    assert c.get("/api/journal").status_code == 404
    assert c.post("/api/open", params={"path": "x"}).status_code == 404
    assert "KEEL_PUBLIC" in c.get("/").text
    s = c.get("/api/state").json()
    assert s.get("public") is True and not s.get("positions")
