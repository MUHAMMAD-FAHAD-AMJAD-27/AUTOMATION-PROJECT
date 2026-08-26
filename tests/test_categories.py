"""
tests/test_categories.py — guard the single-source-of-truth taxonomy.

crawler/categories.py is THE definition. Postgres (schema.sql) and the
dashboard (dashboard/lib/categories.ts) mirror it because they can't import
Python. These tests fail loudly if any mirror drifts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crawler.categories import (
    CATEGORIES,
    CATEGORY_BY_KEY,
    OFFER_TYPES,
    coerce_category,
    coerce_offer_type,
    is_valid_category,
)

ROOT = Path(__file__).resolve().parent.parent


def _parse_schema_check(column: str) -> list[str]:
    """Extract the value list from a `CHECK (<column> IN ('a','b',...))` clause."""
    sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    # Match: <column> ... CHECK (<column> IN ( '...' , '...' ))
    pattern = rf"CHECK\s*\(\s*{re.escape(column)}\s+IN\s*\((.*?)\)\s*\)"
    m = re.search(pattern, sql, re.DOTALL)
    assert m, f"could not find CHECK constraint for {column} in schema.sql"
    return re.findall(r"'([^']+)'", m.group(1))


def test_schema_category_check_matches_python():
    """schema.sql's offers.category CHECK list must equal CATEGORIES (any order)."""
    schema_values = _parse_schema_check("category")
    assert set(schema_values) == set(CATEGORIES), (
        f"schema.sql category CHECK is out of sync with crawler/categories.py.\n"
        f"only in schema: {set(schema_values) - set(CATEGORIES)}\n"
        f"only in python: {set(CATEGORIES) - set(schema_values)}"
    )


def test_schema_offer_type_check_matches_python():
    schema_values = _parse_schema_check("offer_type")
    assert set(schema_values) == set(OFFER_TYPES), (
        f"schema.sql offer_type CHECK is out of sync with crawler/categories.py.\n"
        f"only in schema: {set(schema_values) - set(OFFER_TYPES)}\n"
        f"only in python: {set(OFFER_TYPES) - set(schema_values)}"
    )


def test_categories_unique_and_have_other():
    assert len(CATEGORIES) == len(set(CATEGORIES)), "duplicate category keys"
    assert "other" in CATEGORY_BY_KEY, "'other' fallback category must exist"
    assert "other" in OFFER_TYPES, "'other' fallback offer_type must exist"


@pytest.mark.parametrize("value", list(CATEGORIES))
def test_coerce_category_passes_known_values(value):
    assert coerce_category(value) == value
    assert is_valid_category(value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  CLOUD ", "cloud"),
        ("Saas_Deal", "saas_deal"),
        ("nonsense", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_coerce_category_normalizes_and_falls_back(raw, expected):
    assert coerce_category(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CREDIT", "credit"),
        (" giveaway ", "giveaway"),
        ("bogus", "other"),
        (None, "other"),
    ],
)
def test_coerce_offer_type(raw, expected):
    assert coerce_offer_type(raw) == expected


def test_dashboard_ts_in_sync():
    """The generated dashboard TS must match the current Python taxonomy."""
    ts_path = ROOT / "dashboard" / "lib" / "categories.ts"
    if not ts_path.exists():
        pytest.skip("dashboard/lib/categories.ts not generated")
    ts = ts_path.read_text(encoding="utf-8")
    for key in CATEGORIES:
        assert f'"{key}"' in ts, f"category {key!r} missing from generated categories.ts"
