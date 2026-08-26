"""Unit tests for crawler.normalizer (pure functions, no network)."""
from __future__ import annotations

from crawler.normalizer import (
    clean_text,
    clean_url,
    extract_urls,
    normalize_raw_item,
    normalize_title,
)

# --------------------------------------------------------------------------- #
# clean_url
# --------------------------------------------------------------------------- #
def test_clean_url_strips_utm_and_fbclid():
    url = "https://Example.com/deal?utm_source=telegram&utm_medium=social&fbclid=abc123&page=2"
    assert clean_url(url) == "https://example.com/deal?page=2"


def test_clean_url_removes_fragment_trailing_slash_and_www():
    assert clean_url("https://www.example.com/free-tier/#section") == "https://example.com/free-tier"


def test_clean_url_keeps_essential_params():
    assert clean_url("https://example.com/p?id=42&ref=evil") == "https://example.com/p?id=42"


def test_clean_url_preserves_path_case_and_normalizes_default_port():
    assert clean_url("http://example.com:80/AWS") == "http://example.com/AWS"


# --------------------------------------------------------------------------- #
# clean_text
# --------------------------------------------------------------------------- #
def test_clean_text_strips_engagement_bait():
    raw = "FREE $300 CREDITS!!! 🔥🔥🔥 Like & share our channel! @promo_bot Join us @t.me/spam"
    cleaned = clean_text(raw)
    assert "Like & share" not in cleaned
    assert "@promo_bot" not in cleaned
    assert "t.me/spam" not in cleaned
    assert "!!!" not in cleaned
    assert "🔥🔥🔥" not in cleaned  # emoji run collapsed


def test_clean_text_keeps_core_content():
    cleaned = clean_text("Google Cloud gives $300 free credits for new users. Sign up with any Gmail.")
    assert "$300 free credits" in cleaned


def test_clean_text_handles_html():
    raw = '<p>Get a <a href="https://aws.example/promo">free VPS</a> for 12 months.</p><script>evil()</script>'
    cleaned = clean_text(raw)
    assert "free VPS" in cleaned
    assert "evil()" not in cleaned
    assert "<script>" not in cleaned


def test_clean_text_truncates_to_max_len():
    cleaned = clean_text("x" * 5000, max_len=100)
    assert len(cleaned) <= 100


# --------------------------------------------------------------------------- #
# extract_urls
# --------------------------------------------------------------------------- #
def test_extract_urls_dedupes_and_strips_trailing_punct():
    text = "Check https://a.com/x. and https://a.com/x plus http://b.com/y)"
    urls = extract_urls(text)
    assert urls == ["https://a.com/x", "http://b.com/y"]


def test_extract_urls_with_extras():
    assert extract_urls("no urls here", extra=["https://c.com"]) == ["https://c.com"]


# --------------------------------------------------------------------------- #
# normalize_title
# --------------------------------------------------------------------------- #
def test_normalize_title_tones_down_shouting():
    assert normalize_title("FREE AMAZING STUDENT PACK DEAL!!!") == \
        "Free amazing student pack deal!"


def test_normalize_title_strips_leading_emoji_and_bullets():
    # only SHOUT-case gets lowercased; Title Case passes through untouched
    assert normalize_title("🔥 🔥 New Cloud Deal") == "New Cloud Deal"


def test_normalize_title_capitalizes_first_letter():
    assert normalize_title("new heroku credits") == "New heroku credits"


# --------------------------------------------------------------------------- #
# normalize_raw_item
# --------------------------------------------------------------------------- #
def _raw_row(payload: dict) -> dict:
    return {
        "id": 1, "source_id": 2, "external_id": "42", "raw_payload": payload,
        "source_name": "telegram:test", "source_kind": "telegram",
    }


def test_normalize_raw_item_full_shape():
    row = _raw_row({
        "text": "Free $100 credit at https://example.com/x?utm_source=tg — sign up! Share!!",
        "urls": ["https://example.com/x?utm_source=tg"],
        "author_handle": "@dealhunter",
        "published_at": "2026-08-24T12:00:00+00:00",
        "engagement": {"views": 1000},
    })
    item = normalize_raw_item(row)
    assert item.raw_item_id == 1
    assert item.source_name == "telegram:test"
    assert item.text  # non-empty cleaned text
    assert any("example.com" in u for u in item.urls)
    assert item.author_handle == "@dealhunter"
    assert item.engagement == {"views": 1000}


def test_normalize_raw_item_empty_payload():
    item = normalize_raw_item(_raw_row({}))
    assert item.text == ""
    assert item.urls == []