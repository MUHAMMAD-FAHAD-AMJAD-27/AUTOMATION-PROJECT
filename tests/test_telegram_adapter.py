"""Hermetic tests for adapters.telegram_adapter session handling.

No network, no DB, no real Telegram login. These lock the dual-mode session
backend added for Heroku: TG_SESSION_STRING -> in-memory StringSession
(survives the ephemeral filesystem), else a file-based session under
TG_SESSION_DIR (local dev). TelegramClient is stubbed so nothing connects.
"""
from __future__ import annotations

import pytest

import adapters.telegram_adapter as tg

_TG_KEYS = ("TG_API_ID", "TG_API_HASH", "TG_PHONE", "TG_SESSION_STRING", "TG_SESSION_DIR")


@pytest.fixture
def _clean_env(monkeypatch):
    for key in _TG_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class _StubClient:
    """Records the session argument instead of opening a real client."""

    last_session = None

    def __init__(self, session, api_id, api_hash):
        type(self).last_session = session
        self.api_id = api_id
        self.api_hash = api_hash


def test_build_client_prefers_string_session(monkeypatch):
    monkeypatch.setattr(tg, "TelegramClient", _StubClient)
    captured = {}
    sentinel = object()

    def _fake_string_session(value):
        captured["value"] = value
        return sentinel

    monkeypatch.setattr(tg, "StringSession", _fake_string_session)
    creds = tg.TelegramCredentials(
        api_id=1, api_hash="h", phone="+100", session_dir="./sessions",
        session_string="1BVtsOJ4Buseffe",  # non-empty -> StringSession branch
    )
    tg._build_client(creds)
    assert _StubClient.last_session is sentinel
    assert captured["value"] == "1BVtsOJ4Buseffe"


def test_build_client_falls_back_to_file_session(monkeypatch, tmp_path):
    monkeypatch.setattr(tg, "TelegramClient", _StubClient)
    creds = tg.TelegramCredentials(
        api_id=1, api_hash="h", phone="+100", session_dir=str(tmp_path), session_string=None
    )
    tg._build_client(creds)
    assert _StubClient.last_session == f"{tmp_path}/ingest"
    assert tmp_path.exists()  # file-session path is created on demand


def test_load_credentials_reads_session_string(_clean_env):
    _clean_env.setenv("TG_API_ID", "123")
    _clean_env.setenv("TG_API_HASH", "abc")
    _clean_env.setenv("TG_PHONE", "+100")
    _clean_env.setenv("TG_SESSION_STRING", "some-session-string")
    creds = tg._load_credentials()
    assert creds is not None
    assert creds.session_string == "some-session-string"


def test_load_credentials_blank_session_string_is_none(_clean_env):
    _clean_env.setenv("TG_API_ID", "123")
    _clean_env.setenv("TG_API_HASH", "abc")
    _clean_env.setenv("TG_PHONE", "+100")
    _clean_env.setenv("TG_SESSION_STRING", "   ")  # blank overrides -> treat as unset
    creds = tg._load_credentials()
    assert creds is not None
    assert creds.session_string is None


def test_load_credentials_none_when_required_missing(_clean_env):
    _clean_env.setenv("TG_API_ID", "123")  # api_hash + phone still missing
    assert tg._load_credentials() is None
