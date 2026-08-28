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


# --------------------------------------------------------------------------- #
# Auto-join approval gate
# --------------------------------------------------------------------------- #
import asyncio


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("0", False), ("", False), ("off", False),
     ("1", True), ("true", True), ("YES", True), ("on", True)],
)
def test_auto_join_enabled_truth_table(_clean_env, value, expected):
    if value is not None:
        _clean_env.setenv("TG_AUTO_JOIN", value)
    assert tg._auto_join_enabled() is expected


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self.cur = _FakeCursor(rows)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingClient:
    def __init__(self):
        self.joined: list[str] = []

    def __call__(self, request):  # request is patched to be the bare username
        self.joined.append(request)

        async def _noop():
            return None

        return _noop()


def test_join_candidates_skips_when_disabled(_clean_env, monkeypatch):
    _clean_env.setenv("TG_AUTO_JOIN", "0")
    # _source_id must never be reached when the gate is off.
    monkeypatch.setattr(tg, "_source_id", lambda name: pytest.fail("gate leaked: _source_id called"))
    client = _RecordingClient()
    asyncio.run(tg._join_candidates(client, "telegram:default"))
    assert client.joined == []


def test_join_candidates_joins_only_approved_when_enabled(_clean_env, monkeypatch):
    _clean_env.setenv("TG_AUTO_JOIN", "1")
    monkeypatch.setattr(tg, "_source_id", lambda name: 1)
    fake = _FakeConn([{"channel_username": "approvedchan"}])
    monkeypatch.setattr(tg, "connect", lambda: fake)
    monkeypatch.setattr(tg, "JoinChannelRequest", lambda username: username)
    client = _RecordingClient()
    asyncio.run(tg._join_candidates(client, "telegram:default"))
    # joined exactly the approved candidate, and the SELECT filtered on 'approved'
    assert client.joined == ["approvedchan"]
    assert any("status = 'approved'" in q for q in fake.cur.queries)
    assert not any("status = 'new'" in q for q in fake.cur.queries)
