"""
seq_auth.py -- pure auth helpers for the sequencer (no FastAPI import).

`configured_writers` is a byte-for-byte copy of the body of
review_api._configured_writers (contract §1.3 item 1); review_api.py is
do-not-touch, so tests/test_seq_auth.py compares the two function bodies
via `ast` to catch drift.

Dev identity (contract §1.3 item 7): with auth OFF the writer is the
X-MH2-User header when it is present and valid, else "local".  With auth ON
the header is ignored.
"""
from __future__ import annotations

import os
import re
import secrets

DEV_HEADER = "X-MH2-User"
DEV_NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


def configured_writers() -> dict[str, str]:
    """Parse MH2_AUTH_USERS ("user:pass,user:pass,..."). Empty/unset means
    no writers are configured, which is read below as "auth disabled" --
    deliberate, so every existing test and local `uvicorn --reload` run
    keeps working with zero setup. Only the deployed container sets this."""
    raw = os.environ.get("MH2_AUTH_USERS", "")
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        user, _, pw = pair.partition(":")
        if user and pw:
            users[user] = pw
    return users


def auth_enabled() -> bool:
    return bool(configured_writers())


def check_basic(username: str | None, password: str | None) -> str | None:
    """Auth on: the username if it matches (constant-time), else None.
    Auth off: 'local'."""
    users = configured_writers()
    if not users:
        return "local"
    if username is None or password is None:
        return None
    expected = users.get(username)
    if expected is None:
        return None
    ok = secrets.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))
    return username if ok else None


def resolve_writer(basic_username: str | None, dev_header: str | None) -> tuple[str, str]:
    """(user, source). Auth on: (basic_username, 'basic') -- the caller has
    already verified it. Auth off: (dev_header, 'dev_header') if it matches
    DEV_NAME_RE, else ('local', 'default')."""
    if auth_enabled():
        return (basic_username or "", "basic")
    if dev_header is not None and DEV_NAME_RE.fullmatch(dev_header):
        return (dev_header, "dev_header")
    return ("local", "default")
