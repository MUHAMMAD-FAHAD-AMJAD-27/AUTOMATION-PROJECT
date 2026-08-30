"""Item 5 — fetch fairness.

fetch_unprocessed used to be a pure newest-first slice, which starved the aged
tail whenever more than `limit` raw_items were unprocessed. It now splits each
batch between an oldest-first fairness portion (drains the tail deterministically)
and a newest-first portion (keeps fresh arrivals flowing). These tests lock the
split arithmetic and the env override without touching Postgres.
"""
from __future__ import annotations

import crawler.pipeline as pipeline


class _CaptureCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.store["sql"] = " ".join(str(sql).split())
        self.store["params"] = params

    def fetchall(self):
        return []


class _CaptureConn:
    def __init__(self):
        self.store: dict = {}

    def cursor(self, *a, **k):
        return _CaptureCursor(self.store)


def test_default_fraction_is_half(monkeypatch):
    monkeypatch.delenv("FETCH_FAIRNESS_OLDEST_FRACTION", raising=False)
    assert pipeline._fairness_oldest_fraction() == 0.5


def test_fraction_env_override_and_clamp(monkeypatch):
    monkeypatch.setenv("FETCH_FAIRNESS_OLDEST_FRACTION", "0.25")
    assert pipeline._fairness_oldest_fraction() == 0.25
    monkeypatch.setenv("FETCH_FAIRNESS_OLDEST_FRACTION", "5")     # clamps to 1.0
    assert pipeline._fairness_oldest_fraction() == 1.0
    monkeypatch.setenv("FETCH_FAIRNESS_OLDEST_FRACTION", "-3")    # clamps to 0.0
    assert pipeline._fairness_oldest_fraction() == 0.0
    monkeypatch.setenv("FETCH_FAIRNESS_OLDEST_FRACTION", "junk")  # invalid → default
    assert pipeline._fairness_oldest_fraction() == 0.5


def test_split_sums_to_limit_and_unions(monkeypatch):
    monkeypatch.delenv("FETCH_FAIRNESS_OLDEST_FRACTION", raising=False)
    conn = _CaptureConn()
    pipeline.fetch_unprocessed(conn, source=None, limit=30)
    sql, params = conn.store["sql"], conn.store["params"]
    assert "UNION" in sql
    assert "ORDER BY fetched_at ASC" in sql
    assert "ORDER BY fetched_at DESC" in sql
    assert params["oldest_n"] == 15
    assert params["newest_n"] == 15
    assert params["oldest_n"] + params["newest_n"] == 30


def test_zero_fraction_reserves_nothing_for_oldest(monkeypatch):
    monkeypatch.setenv("FETCH_FAIRNESS_OLDEST_FRACTION", "0")
    conn = _CaptureConn()
    pipeline.fetch_unprocessed(conn, source=None, limit=30)
    params = conn.store["params"]
    assert params["oldest_n"] == 0
    assert params["newest_n"] == 30


def test_source_filter_threaded(monkeypatch):
    monkeypatch.delenv("FETCH_FAIRNESS_OLDEST_FRACTION", raising=False)
    conn = _CaptureConn()
    pipeline.fetch_unprocessed(conn, source="telegram:default", limit=10)
    sql, params = conn.store["sql"], conn.store["params"]
    assert "s.name = %(source)s" in sql
    assert params["source"] == "telegram:default"
