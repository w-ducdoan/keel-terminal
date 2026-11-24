"""One-time Robinhood sign-in for the desk (official MCP server, OAuth).

Run in your own terminal:  .venv\\Scripts\\python -m pmdesk.rh_login

Your browser opens Robinhood's sign-in page. You sign in and approve there; Robinhood sends
the browser back to http://127.0.0.1:6879/callback, which this script catches. The desk saves
the tokens in .secrets/rh_mcp.json and never sees your password.
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

PAGE = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
        b"<html><body style='font-family:Consolas;background:#000;color:#ffa028;padding:40px'>"
        b"<h2>Keel is connected to Robinhood.</h2><p style='color:#ccc'>You can close this tab "
        b"and go back to the terminal.</p></body></html>")


async def wait_for_callback(port: int, timeout: float = 300):
    """Serve one request on 127.0.0.1:<port>/callback and return its code, state and iss."""
    from mcp.shared.auth import AuthorizationCodeResult

    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readline()).decode("latin-1")
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        parts = line.split(" ")
        url = urlparse(parts[1] if len(parts) > 1 else "/")
        if url.path != "/callback":
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        else:
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            writer.write(PAGE)
            if not got.done():
                if "error" in q:
                    got.set_exception(RuntimeError(f"Robinhood returned an error: {q.get('error')} "
                                                   f"{q.get('error_description', '')}"))
                else:
                    got.set_result(AuthorizationCodeResult(code=q.get("code", ""), state=q.get("state"),
                                                           iss=q.get("iss")))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    try:
        return await asyncio.wait_for(got, timeout)
    finally:
        await asyncio.sleep(1)  # let the browser's follow-up request (favicon) finish first
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, _ctx: None)  # a late connection on close is harmless
        server.close()


def main() -> None:
    from .config import load_config
    from .rh_mcp import TOKEN_FILE, call_many

    suffix = str((load_config().settings.get("robinhood") or {}).get("account_suffix", ""))
    print("Connecting Keel to Robinhood (read-only).")
    (data,) = call_many([("get_accounts", {})], interactive=True)
    print(f"\nSigned in. Session saved to {TOKEN_FILE}")
    for a in data.get("accounts", []):
        num = str(a.get("account_number", ""))
        mark = "  <- the desk reads this one" if suffix and num.endswith(suffix) else ""
        print(f"  ****{num[-4:]}  {a.get('brokerage_account_type')}  {a.get('type')}{mark}")
    if not suffix:
        print("\nSet robinhood.account_suffix in config/settings.yaml to pick the account.")


if __name__ == "__main__":
    main()
