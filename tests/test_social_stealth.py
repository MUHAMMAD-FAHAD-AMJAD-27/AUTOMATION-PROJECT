"""Hermetic tests for adapters.social_stealth JSON parsers.

No network, no DB, no browser. These lock the depth-agnostic tree walker against
the exact GraphQL shapes X (SearchTimeline/UserTweets) and Instagram (profile
graphql) emit today, so a future front-end reshuffle fails loudly here instead of
silently ingesting zero items in production — the same failure mode the literal
TODO left this adapter in.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import adapters.social_stealth as ss
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


# --------------------------------------------------------------------------- #
# login() — one-time headed identity bootstrap
# --------------------------------------------------------------------------- #
def test_login_targets_map_to_runner_identity_names():
    # The login identity names MUST match the ones the headless runners construct
    # (run_twitter -> "twitter-main", run_instagram -> "ig-ro"), or the login
    # writes a file the fetch never reads.
    assert ss.LOGIN_TARGETS["twitter"][0] == "twitter-main"
    assert ss.LOGIN_TARGETS["instagram"][0] == "ig-ro"
    assert ss.LOGIN_TARGETS["twitter"][1].startswith("https://")
    assert ss.LOGIN_TARGETS["instagram"][1].startswith("https://")
    # Third element = the cookie that only exists on a genuinely authenticated
    # session. Pre-auth cookies (csrftoken/mid/ig_did/guest_id) are set by merely
    # loading the login page, so they cannot be used as the gate.
    assert ss.LOGIN_TARGETS["twitter"][2] == "auth_token"
    assert ss.LOGIN_TARGETS["instagram"][2] == "sessionid"


# Cookie inventories taken from the two REAL bootstraps (values redacted, lengths
# preserved): a valid X session carries auth_token+ct0, a valid IG session carries
# sessionid+ds_user_id. The IG failure mode that motivated the gate is the
# `_IG_PREAUTH_COOKIES` set below — 6 cookies, plausible-looking, no session.
_LOGGED_IN_COOKIES = [
    {"name": "auth_token", "value": "a" * 40, "domain": ".x.com", "httpOnly": True},
    {"name": "ct0", "value": "c" * 160, "domain": ".x.com", "httpOnly": False},
    {"name": "sessionid", "value": "s" * 77, "domain": ".instagram.com", "httpOnly": True},
    {"name": "ds_user_id", "value": "1" * 11, "domain": ".instagram.com", "httpOnly": False},
]
_IG_PREAUTH_COOKIES = [
    {"name": n, "value": "v", "domain": ".instagram.com"}
    for n in ("csrftoken", "datr", "dpr", "ig_did", "mid", "wd")
]


def _mock_playwright_chain(cookies=None):
    """Build an async_playwright() stand-in whose chain records its calls.

    ``cookies`` is what ``context.cookies()`` returns — defaults to a fully
    logged-in inventory so the happy path saves. Pass a pre-auth list to exercise
    the refusal gate."""
    page = AsyncMock()
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.cookies = AsyncMock(
        return_value=_LOGGED_IN_COOKIES if cookies is None else cookies
    )
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)
    p_obj = MagicMock()
    p_obj.chromium = chromium
    apw_cm = MagicMock()
    apw_cm.__aenter__ = AsyncMock(return_value=p_obj)
    apw_cm.__aexit__ = AsyncMock(return_value=False)
    return apw_cm, chromium, browser, context


def test_login_uses_headed_browser_and_reuses_fetch_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    apw_cm, chromium, browser, context = _mock_playwright_chain()

    with patch.object(ss, "async_playwright", return_value=apw_cm), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("twitter"))

    # Headed, not headless — this is the interactive bootstrap.
    chromium.launch.assert_awaited_once()
    assert chromium.launch.call_args.kwargs.get("headless") is False

    # Context built from the SAME fingerprint the headless fetch uses, and with
    # no storage_state yet (no identity file exists at bootstrap time).
    ctx_kwargs = browser.new_context.call_args.kwargs
    assert ctx_kwargs["viewport"] == {"width": 1366, "height": 768}
    assert ctx_kwargs["timezone_id"] == "America/New_York"
    assert ctx_kwargs["locale"] == "en-US"
    assert ctx_kwargs["user_agent"].startswith("Mozilla/5.0")
    assert ctx_kwargs["storage_state"] is None

    # Identity persisted to identities/twitter-main.state.json.
    context.storage_state.assert_awaited_once()
    saved = context.storage_state.call_args.kwargs["path"]
    assert saved.endswith("twitter-main.state.json")
    assert str(tmp_path) in saved


def test_login_saves_instagram_identity_to_correct_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    apw_cm, _chromium, _browser, context = _mock_playwright_chain()

    with patch.object(ss, "async_playwright", return_value=apw_cm), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("instagram"))

    saved = context.storage_state.call_args.kwargs["path"]
    assert saved.endswith("ig-ro.state.json")


# --------------------------------------------------------------------------- #
# Login session verification (the auth-cookie gate)
# --------------------------------------------------------------------------- #
# Regression: the first real Instagram bootstrap hit the "log in on another
# device" challenge. The desktop tab never picks that approval up on its own, so
# the operator pressed Enter on a still-logged-out browser and login() silently
# wrote a 1643-byte identity holding only 6 pre-auth cookies. The failure then
# surfaced minutes later as an empty headless fetch, far from its cause. These
# tests pin the gate that turns that into a loud refusal at bootstrap time.
def test_login_refuses_to_save_when_auth_cookie_absent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    apw_cm, _chromium, browser, context = _mock_playwright_chain(
        cookies=_IG_PREAUTH_COOKIES
    )

    with caplog.at_level(logging.ERROR), \
         patch.object(ss, "async_playwright", return_value=apw_cm), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("instagram"))

    # Nothing written — a plausible-looking but sessionless identity file is worse
    # than no file, because the fetch cannot tell the difference.
    context.storage_state.assert_not_awaited()
    assert not list(tmp_path.iterdir())
    # And the operator is told what to do about it, not just that it failed.
    msg = caplog.text
    assert "sessionid" in msg
    assert "RELOAD" in msg
    # The browser still closes: the gate returns, it does not leak a window.
    browser.close.assert_awaited_once()


def test_login_refuses_when_auth_cookie_present_but_empty(tmp_path, monkeypatch):
    """A name-only match is not proof — a cleared session can leave the cookie
    name behind with an empty value."""
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    apw_cm, _chromium, _browser, context = _mock_playwright_chain(
        cookies=[{"name": "auth_token", "value": "", "domain": ".x.com"}]
    )

    with patch.object(ss, "async_playwright", return_value=apw_cm), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("twitter"))

    context.storage_state.assert_not_awaited()


def test_login_gate_checks_the_platform_specific_cookie(tmp_path, monkeypatch):
    """An X session cookie must not satisfy an Instagram login (and vice versa) —
    the gate is per-platform, not "any auth-looking cookie"."""
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    only_x = [{"name": "auth_token", "value": "a" * 40, "domain": ".x.com"}]

    apw_cm, _c, _b, context = _mock_playwright_chain(cookies=only_x)
    with patch.object(ss, "async_playwright", return_value=apw_cm), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("instagram"))
    context.storage_state.assert_not_awaited()

    # Same cookie list, twitter platform -> saves.
    apw_cm2, _c2, _b2, context2 = _mock_playwright_chain(cookies=only_x)
    with patch.object(ss, "async_playwright", return_value=apw_cm2), \
         patch.object(ss.asyncio, "to_thread", new=AsyncMock(return_value="")):
        asyncio.run(ss.login("twitter"))
    context2.storage_state.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Proxy credential split (_proxy_config)
# --------------------------------------------------------------------------- #
# Chromium silently DROPS credentials embedded in the proxy server URL, so
# `{"server": "http://user:pass@host:port"}` either goes out unproxied or dies on
# a 407. Playwright requires server = scheme://host:port with username/password
# as separate keys. These lock that split.
def test_proxy_config_none_when_unset():
    """With no proxy configured the whole feature stays inert — new_context()
    expects proxy=None, not a partially-built dict."""
    assert ss._proxy_config(None) is None
    assert ss._proxy_config("") is None


def test_proxy_config_splits_credentials_out_of_server():
    cfg = ss._proxy_config("http://user123:secretpw@geo.iproyal.com:12321")
    assert cfg == {
        "server": "http://geo.iproyal.com:12321",   # no creds left in server
        "username": "user123",
        "password": "secretpw",
    }
    assert "@" not in cfg["server"]


def test_proxy_config_percent_decodes_the_password():
    """A password containing @ or : must be percent-encoded in the env var; the
    split has to restore the literal value or auth fails."""
    cfg = ss._proxy_config("http://u:p%40ss%3Aword@1.2.3.4:8080")
    assert cfg["password"] == "p@ss:word"
    assert cfg["server"] == "http://1.2.3.4:8080"


def test_proxy_config_omits_auth_keys_when_there_are_no_credentials():
    """IP-whitelisted proxies have no user/pass — passing empty strings would be
    treated as a real (failing) credential pair."""
    cfg = ss._proxy_config("http://geo.iproyal.com:12321")
    assert cfg == {"server": "http://geo.iproyal.com:12321"}


def test_proxy_config_defaults_missing_scheme_and_keeps_portless_host():
    assert ss._proxy_config("//host.example:9000") == {"server": "http://host.example:9000"}
    assert ss._proxy_config("http://host.example") == {"server": "http://host.example"}


def test_proxy_config_passes_through_a_non_url_value_untouched():
    """A bare host:port isn't URL-shaped (urlsplit finds no hostname). Hand the
    operator's literal value to Playwright rather than mangling it."""
    assert ss._proxy_config("geo.iproyal.com:12321") == {"server": "geo.iproyal.com:12321"}


def test_proxy_config_logs_error_for_authenticated_socks(caplog):
    """Chromium supports SOCKS5 only without auth, and the runtime failure looks
    nothing like a scheme problem — so it has to be called out here."""
    with caplog.at_level(logging.ERROR):
        cfg = ss._proxy_config("socks5://user:pass@1.2.3.4:1080")
    assert cfg["server"] == "socks5://1.2.3.4:1080"
    assert cfg["username"] == "user"
    assert "cannot authenticate a" in caplog.text.lower()


def test_proxy_config_socks_without_credentials_is_silent(caplog):
    with caplog.at_level(logging.ERROR):
        cfg = ss._proxy_config("socks5://1.2.3.4:1080")
    assert cfg == {"server": "socks5://1.2.3.4:1080"}
    assert caplog.text == ""


def test_context_options_uses_the_split_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.setenv("PROXY_URL", "http://user123:secretpw@geo.iproyal.com:12321")
    opts = ss.StealthIdentity("twitter-main").context_options
    assert opts["proxy"] == {
        "server": "http://geo.iproyal.com:12321",
        "username": "user123",
        "password": "secretpw",
    }


def test_context_options_proxy_is_none_without_proxy_url(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "IDENTITY_DIR", tmp_path)
    monkeypatch.delenv("PROXY_URL", raising=False)
    assert ss.StealthIdentity("twitter-main").context_options["proxy"] is None
