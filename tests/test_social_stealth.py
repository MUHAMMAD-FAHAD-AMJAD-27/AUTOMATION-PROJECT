"""Hermetic tests for adapters.social_stealth JSON parsers.

No network, no DB, no browser. These lock the depth-agnostic tree walker against
the exact GraphQL shapes X (SearchTimeline/UserTweets) and Instagram (profile
graphql) emit today, so a future front-end reshuffle fails loudly here instead of
silently ingesting zero items in production — the same failure mode the literal
TODO left this adapter in.
"""
from __future__ import annotations

from adapters.social_stealth import (
    _iter_nodes,
    _parse_iso_or_twitter_date,
    parse_instagram_payloads,
    parse_twitter_payloads,
)

# --------------------------------------------------------------------------- #
# Realistic capture fixtures (trimmed to the fields the parser reads)
# --------------------------------------------------------------------------- #
# X SearchTimeline nests each tweet under
#   data.search...timeline.instructions[].entries[].content.itemContent.tweet_results.result
# with legacy.full_text + core.user_results...legacy.screen_name.
_TWITTER_CAPTURE = {
    "url": "https://x.com/i/api/graphql/abc/SearchTimeline",
    "body": {
        "data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": [
            {"type": "TimelineAddEntries", "entries": [
                {"content": {"itemContent": {"tweet_results": {"result": {
                    "rest_id": "1001",
                    "core": {"user_results": {"result": {"legacy": {"screen_name": "devdeals"}}}},
                    "legacy": {
                        "id_str": "1001",
                        "full_text": "Free $100 cloud credits for students https://t.co/x https://cloud.example.com/students",
                        "created_at": "Wed Aug 27 12:00:00 +0000 2026",
                        "favorite_count": 42,
                        "retweet_count": 7,
                        "reply_count": 3,
                        "lang": "en",
                        "entities": {"urls": [
                            {"url": "https://t.co/x",
                             "expanded_url": "https://cloud.example.com/students"},
                        ]},
                    },
                }}}}},
                # A tweet with NO link -> must be dropped (not actionable).
                {"content": {"itemContent": {"tweet_results": {"result": {
                    "rest_id": "1002",
                    "legacy": {
                        "id_str": "1002",
                        "full_text": "just shipping code today, no links",
                        "created_at": "Wed Aug 27 13:00:00 +0000 2026",
                        "entities": {"urls": []},
                    },
                }}}}},
            ]},
        ]}}}},
    },
}


_INSTAGRAM_CAPTURE = {
    "url": "https://www.instagram.com/graphql/query",
    "body": {"data": {"user": {
        "username": "devfreebies",
        "biography": "Daily dev deals -> https://links.example.com/all",
        "external_url": "https://links.example.com/all",
        "edge_owner_to_timeline_media": {"edges": [
            {"node": {
                "shortcode": "Cabc123",
                "taken_at_timestamp": 1756296000,
                "edge_media_to_caption": {"edges": [
                    {"node": {"text": "Free Notion Pro for students grab it https://notion.example.com/edu"}},
                ]},
                "edge_liked_by": {"count": 210},
                "edge_media_to_comment": {"count": 12},
            }},
            # A post with no caption URL -> dropped.
            {"node": {
                "shortcode": "Cdef456",
                "taken_at_timestamp": 1756299600,
                "edge_media_to_caption": {"edges": [
                    {"node": {"text": "cute office setup pic"}},
                ]},
                "edge_liked_by": {"count": 5},
                "edge_media_to_comment": {"count": 0},
            }},
        ]},
    }}},
}


# --------------------------------------------------------------------------- #
# _iter_nodes
# --------------------------------------------------------------------------- #
def test_iter_nodes_walks_nested_dicts_and_lists():
    tree = {"a": 1, "b": [{"c": 2}, {"d": {"e": 3}}]}
    dicts = list(_iter_nodes(tree))
    assert {"c": 2} in dicts
    assert {"e": 3} in dicts
    assert any("a" in d for d in dicts)  # root included


# --------------------------------------------------------------------------- #
# _parse_iso_or_twitter_date
# --------------------------------------------------------------------------- #
def test_twitter_date_converts_to_iso_utc():
    out = _parse_iso_or_twitter_date("Wed Aug 27 12:00:00 +0000 2026")
    assert out.startswith("2026-08-27T12:00:00")


def test_iso_date_passes_through_unchanged():
    assert _parse_iso_or_twitter_date("2026-08-27T12:00:00+00:00") == "2026-08-27T12:00:00+00:00"


def test_none_date_stays_none():
    assert _parse_iso_or_twitter_date(None) is None


# --------------------------------------------------------------------------- #
# parse_twitter_payloads
# --------------------------------------------------------------------------- #
def test_twitter_parses_link_bearing_tweet():
    out = parse_twitter_payloads([_TWITTER_CAPTURE])
    assert len(out) == 1  # the link-less tweet is dropped
    item = out[0]
    assert item["external_id"] == "twitter:1001"
    assert item["author_handle"] == "twitter:devdeals"
    assert "https://cloud.example.com/students" in item["urls"]
    assert item["engagement"]["likes"] == 42
    assert item["extra"]["platform"] == "twitter"
    assert item["published_at"].startswith("2026-08-27T12:00:00")


def test_twitter_dedupes_repeated_captures():
    # Same capture twice (pagination overlap) -> one item.
    out = parse_twitter_payloads([_TWITTER_CAPTURE, _TWITTER_CAPTURE])
    assert len(out) == 1


def test_twitter_empty_capture_yields_nothing():
    assert parse_twitter_payloads([]) == []
    assert parse_twitter_payloads([{"url": "x", "body": {}}]) == []


# --------------------------------------------------------------------------- #
# parse_instagram_payloads
# --------------------------------------------------------------------------- #
def test_instagram_parses_media_and_bio_link():
    out = parse_instagram_payloads([_INSTAGRAM_CAPTURE], "devfreebies")
    ids = {i["external_id"] for i in out}
    # bio link pseudo-item + the one link-bearing media post; caption-less post dropped
    assert "instagram:bio:devfreebies" in ids
    assert "instagram:Cabc123" in ids
    assert "instagram:Cdef456" not in ids

    media = next(i for i in out if i["external_id"] == "instagram:Cabc123")
    assert "https://notion.example.com/edu" in media["urls"]
    assert media["author_handle"] == "instagram:devfreebies"
    assert media["engagement"]["likes"] == 210
    assert media["extra"]["kind"] == "media"

    bio = next(i for i in out if i["external_id"] == "instagram:bio:devfreebies")
    assert "https://links.example.com/all" in bio["urls"]
    assert bio["extra"]["kind"] == "bio_link"


def test_instagram_empty_capture_yields_nothing():
    assert parse_instagram_payloads([], "whoever") == []
