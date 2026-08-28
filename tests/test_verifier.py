"""Unit tests for crawler.verifier (schema validation + local logic only)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from crawler.verifier import Offer, Requirements, cosine, sha256_hex


# --------------------------------------------------------------------------- #
# Offer schema
# --------------------------------------------------------------------------- #
def _valid_offer(**overrides) -> dict:
    base = {
        "is_offer": True,
        "title": "Free $300 Google Cloud credits",
        "url": "https://cloud.google.com/free",
        "category": "cloud",
        "offer_type": "credit",
        "value": 300,
        "currency": "USD",
        "expires_at": None,
        "requirements": {"geography": ["US"], "enrollment": [], "steps": ["Create account"]},
        "confidence": 0.9,
        "reasons": ["explicit credit amount"],
    }
    base.update(overrides)
    return base


def test_offer_accepts_valid_payload():
    offer = Offer.model_validate(_valid_offer())
    assert offer.is_offer is True
    assert offer.value == 300
    assert offer.currency == "USD"
    assert offer.requirements.geography == ["US"]


def test_offer_normalizes_category_and_type():
    offer = Offer.model_validate(_valid_offer(category="SAAS", offer_type="VIP"))
    assert offer.category == "other"
    assert offer.offer_type == "other"


def test_offer_currency_uppercased():
    offer = Offer.model_validate(_valid_offer(currency="usd"))
    assert offer.currency == "USD"


def test_offer_title_cleaned_and_min_length_enforced():
    offer = Offer.model_validate(_valid_offer(title="  FREE CLOUD CREDITS FOR STUDENTS  "))
    assert offer.title == "Free cloud credits for students"
    with pytest.raises(ValidationError):
        Offer.model_validate(_valid_offer(title="ab"))


def test_offer_confidence_bounds():
    with pytest.raises(ValidationError):
        Offer.model_validate(_valid_offer(confidence=1.5))
    with pytest.raises(ValidationError):
        Offer.model_validate(_valid_offer(confidence=-0.1))


def test_offer_negative_value_rejected():
    with pytest.raises(ValidationError):
        Offer.model_validate(_valid_offer(value=-5))


def test_offer_expires_at_parses_iso():
    offer = Offer.model_validate(_valid_offer(expires_at="2026-12-31T23:59:59Z"))
    assert offer.expires_at is not None
    assert offer.expires_at.year == 2026


def test_offer_title_shout_case_lowered_by_validator():
    """The validator calls normalize_title — SHOUTING becomes sentence case."""
    offer = Offer.model_validate(_valid_offer(title="AWS EDUCATE STUDENT PACK"))
    assert offer.title == "Aws educate student pack"


def test_requirements_defaults():
    req = Requirements()
    assert req.geography == [] and req.enrollment == [] and req.steps == []


# --------------------------------------------------------------------------- #
# Idea 3: evergreen / verification-gated classification
# --------------------------------------------------------------------------- #
def test_offer_evergreen_verification_default_off():
    """New classification fields default to a safe, non-gated state."""
    offer = Offer.model_validate(_valid_offer())
    assert offer.is_evergreen is False
    assert offer.verification is None


def test_offer_evergreen_verification_roundtrip():
    offer = Offer.model_validate(
        _valid_offer(is_evergreen=True, verification="student")
    )
    assert offer.is_evergreen is True
    assert offer.verification == "student"


# --------------------------------------------------------------------------- #
# Math helpers
# --------------------------------------------------------------------------- #
def test_cosine_identical_vectors():
    v = [1.0, 2.0, 3.0]
    assert math.isclose(cosine(v, v), 1.0)


def test_cosine_orthogonal_vectors():
    assert math.isclose(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)


def test_cosine_empty_or_mismatched():
    assert cosine([], []) == 0.0
    assert cosine([1.0], [1.0, 2.0]) == 0.0


def test_sha256_hex_stable():
    assert sha256_hex("https://example.com/x") == sha256_hex("https://example.com/x")
    assert len(sha256_hex("abc")) == 64


# --------------------------------------------------------------------------- #
# Timestamp sanity (guards tz-naive comparisons downstream)
# --------------------------------------------------------------------------- #
def test_expires_at_tz_aware_when_provided_with_z():
    offer = Offer.model_validate(_valid_offer(expires_at="2026-01-01T00:00:00Z"))
    assert offer.expires_at.tzinfo is not None