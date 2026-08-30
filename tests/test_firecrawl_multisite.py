"""Firecrawl multi-site expansion — Item 2 (2026-08-29).

Hermetic: no Postgres, no network, no Firecrawl. Adds aicredits.dev and
dealify.com alongside resourify.com under the SAME credit-guarded pattern
(once/day gate + per-source URL-diff dedup + LLM-extract only on unseen). The
gate and dedup themselves are locked in test_firecrawl_adapter.py; this file
locks the multi-site wiring:

  * TARGET_SITES carries the three hubs with their per-site path_fragment and
    author handle.
  * _map_site keeps only a site's individual-offer URLs (its path_fragment),
    dropping category/collection/blog noise before the paid scrape.
  * _result_to_payload stamps the per-site handle (not a hardcoded host).
  * run_firecrawl visits every site and threads each site's own handle through
    — so offers are attributed to the site they came from.
"""
from __future__ import annotations

import asyncio
import json

import adapters.firecrawl_adapter as fc


# --------------------------------------------------------------------------- #
# TARGET_SITES configuration
# --------------------------------------------------------------------------- #
def test_target_sites_cover_the_three_hubs():
    by_handle = {s.handle: s for s in fc.TARGET_SITES}
    assert set(by_handle) == {"resourify.com", "aicredits.dev", "dealify.com"}
    assert by_handle["resourify.com"].path_fragment == "/resources/"
    assert by_handle["aicredits.dev"].path_fragment == "/submissions/"
    assert by_handle["dealify.com"].path_fragment == "/products/"
    # base_url and handle stay consistent (source row = firecrawl:<handle>).
    for s in fc.TARGET_SITES:
        assert s.handle in s.base_url


# --------------------------------------------------------------------------- #
# _map_site — path_fragment filter drops non-offer URLs
# --------------------------------------------------------------------------- #
class _FakeMapResult:
    def __init__(self, links):
        self.links = links


class _FakeApp:
    def __init__(self, links):
        self._links = links
        self.mapped: list[str] = []

    def map_url(self, base_url, include_subdomains=False):
        self.mapped.append(base_url)
        return _FakeMapResult(self._links)


def test_map_site_keeps_only_offer_pages_for_dealify():
    site = fc.SiteConfig("https://dealify.com", "/products/", "dealify.com")
    app = _FakeApp([
        "https://dealify.com/products/acme-lifetime",   # keep
        "https://dealify.com/products/beta-tool",        # keep
        "https://dealify.com/collections/developer",     # drop (index)
        "https://dealify.com/blog/how-to",               # drop (blog)
        "https://dealify.com/",                          # drop (home)
    ])
    out = fc._map_site(app, site, None)
    assert out == [
        "https://dealify.com/products/acme-lifetime",
        "https://dealify.com/products/beta-tool",
    ]


def test_map_site_uses_each_sites_own_fragment():
    site = fc.SiteConfig("https://aicredits.dev", "/submissions/", "aicredits.dev")
    app = _FakeApp([
        "https://aicredits.dev/submissions/31-gcp-300",  # keep
        "https://aicredits.dev/submissions",             # drop (index, no trailing slash)
        "https://aicredits.dev/about",                   # drop
    ])
    assert fc._map_site(app, site, None) == ["https://aicredits.dev/submissions/31-gcp-300"]


def test_map_site_returns_empty_on_map_failure():
    class _BoomApp:
        def map_url(self, *a, **k):
            raise RuntimeError("firecrawl down")

    site = fc.SiteConfig("https://dealify.com", "/products/", "dealify.com")
    assert fc._map_site(_BoomApp(), site, None) == []


# --------------------------------------------------------------------------- #
# _result_to_payload — per-site handle attribution
# --------------------------------------------------------------------------- #
def test_payload_stamps_the_given_handle():
    extract = {"is_offer": True, "title": "Free $300 GCP credits",
               "description": "d", "direct_url": "https://cloud.google.com/free"}
    p = fc._result_to_payload("https://aicredits.dev/submissions/31-gcp", extract, "aicredits.dev")
    assert p is not None
    assert p["author_handle"] == "aicredits.dev"
    assert p["extra"]["source_url"] == "https://aicredits.dev/submissions/31-gcp"


def test_payload_is_none_for_non_offer_pages():
    assert fc._result_to_payload("https://dealify.com/products/x",
                                 {"is_offer": False, "title": "About us"},
                                 "dealify.com") is None


# --------------------------------------------------------------------------- #
# run_firecrawl — visits every site and threads its handle through
# --------------------------------------------------------------------------- #
def test_run_firecrawl_visits_all_sites_and_attributes_per_site(monkeypatch, capsys):
    monkeypatch.setattr(fc, "_get_client", lambda: object())

    # Each site maps to exactly one of its own offer URLs.
    def _fake_map(app, site, category_filter):
        return [f"{site.base_url}{site.path_fragment}slug"]

    monkeypatch.setattr(fc, "_map_site", _fake_map)

    def _fake_batch(app, urls):
        return [{"url": u, "extract": {"is_offer": True, "title": "A real deal title"},
                 "metadata": {"sourceURL": u}} for u in urls]

    monkeypatch.setattr(fc, "_batch_scrape", _fake_batch)

    handles: list[str] = []
    orig = fc._result_to_payload

    def _spy(url, extract, handle):
        handles.append(handle)
        return orig(url, extract, handle)

    monkeypatch.setattr(fc, "_result_to_payload", _spy)

    total = asyncio.run(fc.run_firecrawl(dry_run=True))

    # one offer per configured site, each attributed to its own handle
    assert total == len(fc.TARGET_SITES)
    assert sorted(handles) == sorted(s.handle for s in fc.TARGET_SITES)

    # dry-run printed one payload per site, each carrying its own handle
    printed = [json.loads(b) for b in _iter_json_objects(capsys.readouterr().out)]
    assert {p["author_handle"] for p in printed} == {s.handle for s in fc.TARGET_SITES}


def _iter_json_objects(text: str):
    """Yield each top-level pretty-printed JSON object from concatenated stdout."""
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \n\r\t":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        yield json.dumps(obj)
        idx = end


# --------------------------------------------------------------------------- #
# Item 4 — per-site credit guard (rescrape window + per-run URL cap)
# --------------------------------------------------------------------------- #
def test_dealify_has_reduced_refresh_and_cap():
    dealify = {s.handle: s for s in fc.TARGET_SITES}["dealify.com"]
    # dealify is the biggest, slowest-changing catalog → monthly refresh, small cap.
    assert dealify.rescrape_after_days == 30
    assert dealify.max_urls_per_run == 40
    # the high-yield default site keeps the standard cadence + default cap.
    resourify = {s.handle: s for s in fc.TARGET_SITES}["resourify.com"]
    assert resourify.rescrape_after_days == fc.RESCRAPE_AFTER_DAYS
    assert resourify.max_urls_per_run == fc.DEFAULT_MAX_URLS_PER_RUN


def test_run_firecrawl_caps_urls_per_run(monkeypatch):
    """A large unseen catalog is scraped a bounded slice at a time, so a
    cold-start cannot spike credits (dealify's 767-page burst)."""
    monkeypatch.setattr(fc, "_get_client", lambda: object())
    site = fc.SiteConfig("https://dealify.com", "/products/", "dealify.com",
                         rescrape_after_days=30, max_urls_per_run=40)
    monkeypatch.setattr(fc, "TARGET_SITES", [site])

    # Map returns 100 offer URLs; dry-run keeps them all unseen (no source_id).
    monkeypatch.setattr(fc, "_map_site",
                        lambda app, s, cf: [f"{s.base_url}{s.path_fragment}p{i}" for i in range(100)])

    scraped: list[list[str]] = []

    def _fake_batch(app, urls):
        scraped.append(list(urls))
        return []

    monkeypatch.setattr(fc, "_batch_scrape", _fake_batch)

    asyncio.run(fc.run_firecrawl(dry_run=True))
    assert scraped, "batch_scrape should have been called"
    assert len(scraped[0]) == 40, f"expected cap of 40, scraped {len(scraped[0])}"

