"""Basic non-crash tests for crawler.status (Phase 21 read-only health check).

These are hermetic: no Postgres and no network are touched. A FakeCursor
returns schema-shaped rows keyed by a distinctive substring of each SELECT, so
gather()/render()/main() are exercised end-to-end against the exact row shapes
the real schema produces — proving the report code doesn't crash (no KeyError,
no format error) on realistic data, including the empty-DB edge case.

The provider list and distribution flag are stubbed so the test never reads the
operator's real .env, and the report never has a chance to print a real key.
"""
from __future__ import annotations

import crawler.status as status
from crawler.verifier import _Provider

# Canned rows keyed by a substring unique to each query gather() issues.
_FAKE_ROWS: dict[str, object] = {
    "count(*) FILTER (WHERE is_active)": {"total": 42, "active": 40},
    "GROUP BY verification_status": [
        {"status": "verified", "n": 30}, {"status": "live", "n": 10},
    ],
    "FROM dispatches GROUP BY status": [
        {"status": "sent", "n": 12}, {"status": "pending", "n": 3},
        {"status": "failed", "n": 1},
    ],
    "WHERE status = 'failed'": [
        {"id": 7, "offer_id": 99, "attempts": 3, "last_error": "429 rate limited\ndetail"},
    ],
    "FROM raw_items r JOIN sources": {"total": 500, "rejected": 120, "unprocessed": 8},
    "WHERE status = 'success'": {"id": 29, "flow_key": "pipeline", "ts": "2026-08-27T13:47:07+00:00"},
    "FROM runs GROUP BY status": [
        {"status": "success", "n": 20}, {"status": "partial", "n": 9},
    ],
    "FROM runs ORDER BY id DESC": [
        {"id": 29, "flow_key": "pipeline", "status": "success",
         "started_at": "2026-08-27T13:40:00+00:00", "finished_at": "2026-08-27T13:47:07+00:00"},
    ],
}


class _FakeCursor:
    def __init__(self) -> None:
        self._pending = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        for needle, rows in _FAKE_ROWS.items():
            if needle in sql:
                self._pending = rows
                return
        self._pending = None

    def fetchone(self):
        return self._pending if isinstance(self._pending, dict) else None

    def fetchall(self):
        return self._pending if isinstance(self._pending, list) else []


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_providers(monkeypatch, providers) -> None:
    monkeypatch.setattr(status, "_load_providers", lambda: providers)
    monkeypatch.setattr(status, "_distribution_enabled", lambda: False)


def test_gather_and_render_populated(monkeypatch):
    _stub_providers(monkeypatch, [
        _Provider(api_key="gsk_SECRET", base_url="https://api.groq.com/openai/v1", model="llama-3.3-70b"),
        _Provider(api_key="sk-or-SECRET", base_url="https://openrouter.ai/api/v1", model="free-model"),
    ])
    data = status.gather(_FakeConn())
    report = status.render(data)

    # Core numbers surface correctly.
    assert "total:  42" in report and "active: 40" in report
    assert "sent: 12" in report and "pending: 3" in report and "failed: 1" in report
    assert "unprocessed (pipeline would pick up): 8" in report
    assert "run #29" in report
    # Providers show base_url + model but NEVER the api_key.
    assert "https://api.groq.com/openai/v1" in report and "llama-3.3-70b" in report
    assert "gsk_SECRET" not in report and "sk-or-SECRET" not in report
    assert "LLM_DISTRIBUTION_ENABLED: OFF" in report


def test_render_empty_db(monkeypatch):
    """No offers, no runs, no providers — must still render without crashing."""
    _stub_providers(monkeypatch, [])
    empty = {
        "offers": {}, "offers_by_status": [], "dispatches": [],
        "dispatch_failures": [], "raw_items": {}, "last_success": {},
        "runs_by_status": [], "recent_runs": [],
    }
    report = status.render(empty)
    assert "total:  0" in report
    assert "last success: (none recorded)" in report
    assert "none configured" in report


def test_main_prints(monkeypatch, capsys):
    _stub_providers(monkeypatch, [])
    monkeypatch.setattr(status, "connect", lambda: _FakeConn())
    rc = status.main([])
    assert rc == 0
    assert "SYSTEM STATUS" in capsys.readouterr().out
