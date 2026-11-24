"""Run Keel: `.venv\\Scripts\\python -m pmdesk` then open http://localhost:6870"""
import logging
import os
import socket
import sys

import uvicorn

from .config import DATA_DIR, load_config
from .server import create_app

LOG_FILE = DATA_DIR / "desk.log"


def _log_to_file_if_windowless() -> None:
    """pythonw (used by the auto-start task) has no console, so send output to data/desk.log.
    The log rolls over to desk.log.1 once it passes 5 MB."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    DATA_DIR.mkdir(exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
        LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the whole process
    sys.stdout = sys.stderr = f


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    _log_to_file_if_windowless()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    for noisy in ("httpx", "httpx2", "httpcore", "mcp"):  # one line per HTTP request otherwise
        logging.getLogger(noisy).setLevel(logging.WARNING)
    port = int(os.environ.get("PORT") or load_config().settings.get("port", 6870))
    host = os.environ.get("KEEL_HOST", "127.0.0.1")
    if _port_busy(port):
        # Already running (the Claude preview or another start): exit cleanly, not as a crash.
        logging.info("Keel is already running on port %s; nothing to start.", port)
        return
    logging.info("Starting Keel on http://localhost:%s", port)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
