"""One-time Robinhood sign-in. Run in your own terminal:

    .venv\\Scripts\\python -m pmdesk.login

You type your username, password and (if asked) your authenticator code.
The password can be saved to Windows Credential Manager so next time you
only confirm it. The session token is stored in .secrets/ (git-ignored).
"""
from __future__ import annotations

import getpass
import sys

import keyring
import robin_stocks.robinhood as rh

from .broker import PICKLE_NAME, PICKLE_PATH
from .config import SECRETS_DIR

SERVICE = "pm-desk-robinhood"


def main() -> int:
    SECRETS_DIR.mkdir(exist_ok=True)
    saved_user = keyring.get_password(SERVICE, "username")
    prompt = f"Robinhood username [{saved_user}]: " if saved_user else "Robinhood username: "
    username = input(prompt).strip() or (saved_user or "")
    if not username:
        print("No username given.")
        return 1

    password = keyring.get_password(SERVICE, username)
    if password:
        use = input("Use the password saved in Windows Credential Manager? [Y/n]: ").strip().lower()
        if use == "n":
            password = None
    if not password:
        password = getpass.getpass("Robinhood password: ")

    mfa = input("Authenticator code (press Enter if you use app or SMS approval): ").strip() or None

    if PICKLE_PATH.exists():
        PICKLE_PATH.unlink()  # force a fresh sign-in instead of reusing a dead token

    result = rh.login(username=username, password=password, mfa_code=mfa, expiresIn=86400 * 7,
                      store_session=True, pickle_path=str(SECRETS_DIR), pickle_name=PICKLE_NAME)
    if not result or not PICKLE_PATH.exists():
        print("Sign-in failed. Nothing was saved.")
        return 1

    keyring.set_password(SERVICE, "username", username)
    save = input("Save the password in Windows Credential Manager for next time? [y/N]: ").strip().lower()
    if save == "y":
        keyring.set_password(SERVICE, username, password)
    print("Signed in. Keel will pick up the session within a minute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
