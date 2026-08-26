# Ingestion Adapter Specifications — Phase 2

Locked sources: **Telegram (Telethon)**, **Twitter/X**, **Instagram**, **Facebook**. (YouTube dropped.)

---

## 0. Adapter Contract

Every adapter emits the same normalized shape, stored verbatim in `raw_items.raw_payload`:

```json
{
  "external_id": "platform-native id (msg_id / tweet_id / post_id)",
  "text": "plain text of the post",
  "urls": ["https://..."],            // entities + regex-extracted, pre-canonicalization
  "author_handle": "channel/handle",
  "published_at": "ISO-8601",
  "engagement": {"views": 0, "likes": 0, "forwards": 0},
  "extra": {}                          // adapter-specific (media, reply parents, ...)
}
```

Downstream, the normalizer turns these into offers: canonicalize URLs → strip tracking
params → LLM-verify → exact + semantic dedup → `offers` row.

**Rate & health policy (enforced for every source):**

| Source | Requests per minute | Daily cap | Backoff | Breaker threshold |
|---|---|---|---|---|
| Telegram | ~0.5–1 (MTProto) | 200 msgs/channel | FloodWait-aware | 5 consecutive errors |
| Twitter/X | 1–2 / identity | 50 fetches | 6–15s random | 3 tarpits → disable 24h |
| Instagram | 0.1–0.2 / profile | 15 profile views | 6–15s random | 3 tarpits → disable 24h |
| Facebook | n/a (no scraper) | — | — | — |

---

## 1. Telegram (via Telethon) — *Primary, highest signal*

**Why MTProto and not Bot API:** bots cannot read arbitrary public channels; a user-account
client (Telethon, MTProto) can subscribe and pull history.

### 1.1 Channel monitoring (incremental)

- Each channel has a watermark row in `channel_cursors(last_message_id)`.
- Per sync: `GetHistoryRequest(min_id=watermark, limit=50)` → process oldest→newest →
  bump watermark **only after the batch is fully persisted** (crash-safe resume).
- Window: on the always-on `worker` dyno, a 5-minute cadence loop; in batch mode
  (Heroku Scheduler 3×/day), one `sync_once()` pass per run — same helper, no dyno needed 24/7.

### 1.2 Auto-join discovery

- `SearchRequest` over a term list (`free credits`, `student pack`, `free tier`, `coupon`,
  `developer discount`), scored by relevance → insert `discovered_channels` rows (status
  `new`). A review loop joins the top candidates (`JoinChannelRequest`) and creates their
  cursor rows. Keep discovery at most hourly to stay under flood limits.
- Governance: channels with >50k members or a "giveaway-bot" pattern get demoted in score;
  dropped channels are marked `dropped`, never re-joined automatically.

### 1.3 Flood discipline (non-negotiable)

- One session/identity per account; sequential requests; 1 request per ~1–2s average;
  capacity-limited backfill (50 msgs/channel/run); cap any `FloodWaitError` sleep at 15 min
  then resume. A banned account is a **service incident** — never risk it for backfill speed.

### 1.4 Ops notes

- `api_id/api_hash` from <https://my.telegram.org/apps>; first run needs a one-time
  interactive login (phone + 2FA) to create the session file.
- Session files persist on the worker (local disk / mounted volume); on Heroku one-off
  dynos use ephemeral FS, so **prefer the always-on worker dyno for Telegram** — that's why
  it's the single always-on process in the Procfile.
- Hosting outside your ISP's region (Heroku `--region us`) sidesteps local Telegram blocks.

---

## 2. Twitter/X — *High signal, high maintenance*

**Reality check:** X has no free public API and requires login for most web views. The
stealth route is the only programmatic option; expect breakage a few times a year.

### 2.1 Strategy

- **Patchright** stealth browser, identity `twitter-main` (see `adapters/social_stealth.py`).
- Search URLs: `https://x.com/search?q=<terms>&f=live` for `free credits`, `#freecourse`,
  `student pack`, `free tier giveaway` — capture the **XHR `SearchTimeline` JSON**
  (`page.on("response")`), not the DOM. GraphQL JSON is far more stable than selectors.
- Extract per tweet: `id_str`, `full_text`, `entities.urls`, `created_at`, `user.screen_name`.

### 2.2 Session bootstrap (one time, manual)

1. Run a **headed** Patchright/Playwright session for the identity.
2. Log in manually (real account you can afford to lose — this is a grey area).
3. Save `storage_state.json`; from then on run headless, reusing the state; refresh
   cookies each run and persist back.

### 2.3 Guardrails

- 1–2 fetches/min/identity; a tarpit (login wall/`px-captcha`) → screenshot, stop the
  identity for 24h, alert. Never auto-solve CAPTCHAs at volume.
- No writes (no likes/replies/follows) from the scraper identity.
- Read-only identity separation: a flagged scrape identity must not touch your main account.

---

## 3. Instagram — *Medium signal, ToS-restricted*

**Policy:** Instagram's platform terms prohibit scraping without permission. Keep it to
**public profile grids of a small curated list** of deal/coupon accounts, ~15 profile views
per day, read-only, no comments/DMs.

### 3.1 Strategy

- Patchright identity `ig-ro`; URL = `https://www.instagram.com/<handle>/`.
- Capture the embedded `graphql` JSON (profile posts); link-bearing posts (`link`, `bio
  link`, caption URLs) → raw items. Bio links are usually the real offer; track
  `profile.bio_links` deltas separately (cheap diffing → "new link in bio" events).
- Do not attempt the private-API/graph endpoints that require auth tokens beyond the
  normal web session; when the parsed JSON shape changes, fix the jq-style path (budget
  ~1–2 days of maintenance per IG front-end overhaul).

---

## 4. Facebook — *Do not scrape*

Facebook is effectively closed: no session-free view, aggressive anti-bot, heavy legal
exposure for scraping public groups. **Recommendation: exclude Facebook from the scraper
fleet entirely.**

Alternative that keeps the source "in scope":

- Maintain a **manual/curated feed**: you (or a Telegram/Reddit mirror) paste public FB
  group posts via a tiny `manual` source insert (2-line CLI, `INSERT INTO raw_items ...`),
  or subscribe to the handful of FB pages that mirror to Telegram and let the Telegram
  adapter pick them up. Same pipeline, zero legal surface.

---

## 5. Source registration checklist (SQL)

```sql
INSERT INTO sources (name, kind, config) VALUES
  ('telegram:default',   'telegram', '{"delta_pull":true,"search_terms":["free credits","student pack","free tier"]}'),
  ('twitter:devrel',     'twitter',  '{"terms":["free credits","#freecourse","student pack"],"identity":"twitter-main"}'),
  ('instagram:dealposts','instagram','{"handles":["dealhunter","devfree_stuff"],"identity":"ig-ro"}'),
  ('manual:facebook',    'manual',   '{}');
```

Adapter → raw_items → normalizer → LLM verifier → `offers` → dispatcher. The remaining
pieces to build in Phase 2 are exactly: the normalizer module and the verifier module
(LLM structured output into the `Offer` JSON schema from the architecture doc).