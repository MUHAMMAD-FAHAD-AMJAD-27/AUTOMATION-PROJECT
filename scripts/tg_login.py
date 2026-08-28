#!/usr/bin/env python3
"""
scripts/tg_login.py — one-time interactive Telegram login -> StringSession
==========================================================================
Run this ONCE, interactively, to mint a portable StringSession for the burner
account. Paste the printed string into the Heroku config var TG_SESSION_STRING;
after that the always-on scheduler authenticates with zero interaction and
survives Heroku's ephemeral filesystem (no .session file to lose on restart).

Why this exists
---------------
`client.start()` on the unattended Heroku dyno can never answer Telegram's
login-code prompt. So we do the interactive step once, here, and hand the
resulting session to the dyno as an env var instead of a file.

Prerequisites (env vars — a local .env is fine):
    TG_API_ID, TG_API_HASH   (https://my.telegram.org/apps)
    TG_PHONE                  (the burner account's number, e.g. +12025550123)
    TG_PASSWORD               (only if the account has 2FA enabled)

Usage:
    python scripts/tg_login.py

You will be prompted for the login code Telegram sends to that account (and the
2FA password if set). On success it prints the StringSession to stdout.

SECURITY: the printed StringSession is a full-account credential — treat it
exactly like a password. Do NOT commit it, paste it into chat, or log it. Set
it only as the Heroku config var TG_SESSION_STRING. Anyone holding it controls
the account; if it leaks, revoke it from Telegram > Settings > Devices.

This script performs a live login when YOU run it. It is intentionally not
imported or invoked anywhere else in the codebase.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

load_dotenv(override=True)


def main() -> int:
    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    phone = os.environ.get("TG_PHONE", "").strip()
    if not (api_id and api_hash and phone):
        print(
            "ERROR: set TG_API_ID, TG_API_HASH and TG_PHONE (env or .env) first.",
            file=sys.stderr,
        )
        return 1

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        client.start(  # interactive: prompts for the login code (+ 2FA if set)
            phone=phone,
            password=lambda: os.environ.get("TG_PASSWORD", "") or input("2FA password: "),
        )
        me = client.get_me()
        session_string = client.session.save()

    print("\n" + "=" * 70)
    print(f"Logged in as: {getattr(me, 'username', None) or me.id}")
    print("Copy the line below into Heroku config var TG_SESSION_STRING")
    print("(treat it like a password — do NOT commit or share it):")
    print("=" * 70)
    print(session_string)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
