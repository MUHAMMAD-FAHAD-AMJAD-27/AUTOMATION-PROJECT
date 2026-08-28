"""Hermetic tests for adapters.creator_mirrors parsing helpers.

No network, no DB. These lock the GitHub /trending HTML selector (which drifts
whenever GitHub reshuffles its markup) and the RSS/Atom parser against the exact
shapes the live sources emit today, so a future markup change fails loudly here
instead of silently yielding zero items in production.
"""
from __future__ import annotations

from adapters.creator_mirrors import _parse_github_trending_html, _parse_rss

# Current GitHub trending markup (Aug 2026): the repo name lives in an
# <h2 class="h3 lh-condensed"> and the <a> carries data-hydro-* attributes
# BEFORE href, with svg + owner/repo spans as inner text.
_TRENDING_HTML = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a data-hydro-click="{&quot;x&quot;:1}" data-hydro-click-hmac="deadbeef"
       href="/zedeus/nitter" data-view-component="true" class="Link">
       <svg aria-hidden="true"></svg><span class="text-normal">zedeus /</span> nitter</a>
  </h2>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/JetBrains/go-modern-guidelines">t</a></h2>
</article>
"""


def test_parse_github_trending_extracts_slugs():
    items = _parse_github_trending_html(_TRENDING_HTML)
    urls = [i["url"] for i in items]
    assert "https://github.com/zedeus/nitter" in urls
    assert "https://github.com/JetBrains/go-modern-guidelines" in urls
    # title falls back to the slug; every item's url ends with that slug
    assert all(i["url"].endswith(i["title"]) and "/" in i["title"] for i in items)


def test_parse_github_trending_ignores_non_heading_links():
    # Sponsor/login/nav anchors are NOT inside an lh-condensed heading -> skipped.
    junk = '<a href="/sponsors/zedeus">x</a><a href="/login">y</a>'
    assert _parse_github_trending_html(junk) == []


def test_parse_github_trending_caps_at_25():
    many = "".join(
        f'<h2 class="h3 lh-condensed"><a href="/o{i}/r{i}">t</a></h2>' for i in range(40)
    )
    assert len(_parse_github_trending_html(many)) == 25


def test_parse_rss_reads_items():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Free thing</title><link>https://example.com/a</link>
            <description>a deal</description><pubDate>Wed, 27 Aug 2026 00:00:00 GMT</pubDate></item>
      <item><title>No link relative</title><link>/relative</link></item>
    </channel></rss>"""
    out = _parse_rss(xml)
    assert len(out) == 1  # the /relative item is dropped (not http)
    assert out[0]["url"] == "https://example.com/a"
