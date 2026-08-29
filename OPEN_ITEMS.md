# Open Items — Developer-Freebies Intelligence Pipeline

**Last updated:** 2026-08-29

> **Keep this file current.** It is the standing reference that survives context loss
> between sessions. When an item resolves, move it to **§4 Closed** with its commit
> reference — do not delete it, and do not let this file drift the way the Redis add-on
> plan drifted in DEPLOYMENT.md. Someone reading only this file (no chat history) should
> understand exactly what is outstanding, why, and what unblocks each item.

Items are grouped by **what is actually keeping them shut**, because that determines who
acts next:

- **§1 Paused by decision** — working code, deliberately not activated. Do **not** touch
  or suggest resuming unless the owner reopens it.
- **§2 Blocked on a manual step** — code is done; waiting on a human interactive action
  that can't be automated headlessly.
- **§3 Deferred / low priority** — feasible, no blocker, but deliberately not being done
  now. Each carries the decision on record so it isn't relitigated.
- **§4 Closed this session** — resolved, with commit references, for traceability.

---

## §1 — Paused by decision (do not touch without the owner raising it)

### 1.1 Telegram Tier 2 (full MTProto engine)
- **Status:** Fully coded and tested. Never activated. **PAUSED by decision — not abandoned.**
- **Blocker (concrete):** `my.telegram.org` app-creation endpoint rejected submissions
  across multiple VPNs / phone numbers / browsers — a persistent anti-abuse block on the
  account-creation side, **not** a code defect. Without an approved `TG_API_ID` /
  `TG_API_HASH` there is no MTProto session to open.
- **What would unblock it:** a successful `my.telegram.org` app registration (manual,
  owner-side), then populating the `TG_*` config vars. Auto-join also requires the
  `2026-08-28_add_approved_status.sql` constraint (already applied — see §4.2) and
  `TG_AUTO_JOIN=1`.
- **Standing rule:** all Tier 2 code stays exactly as-is. Do not remove, simplify, or
  "clean up" anything Telegram-related. Tier 1 (public webview mirrors) remains live and
  is unaffected.

---

## §2 — Blocked on a manual step (waiting on a human interactive action)

### 2.1 Social stealth adapter (Twitter/X + Instagram)
- **Status:** Parser + tests complete (part of the 132-passing suite), committed in
  `1fa98d3`. The literal `TODO` that left captured GraphQL JSON unparsed is resolved:
  `parse_twitter_payloads` / `parse_instagram_payloads` now turn captures into
  `raw_items`, and `run_twitter` / `run_instagram` wrap capture→parse→persist with
  graceful `_health(ok=False)` degradation.
- **ACTIVATION IN PROGRESS (owner decision 2026-08-29).** Reversed from dormant — the owner
  wants this live. Sequence: `.gitignore` prerequisite closed (done, this session) → owner
  does the one-time manual browser login (pending, owner-only) → validate the parser against
  the first real capture → only then wire into a scheduler slot.
- **Not wired into `scheduler.py` — by design.** With no identity bootstrapped it would
  only log a failed browser launch every slot. It stays out of the 27-slot loop until an
  identity exists.
- **Blocker (concrete):** ~~needs a hand-logged-in browser identity~~ **RESOLVED 2026-08-29 —
  both identities exist and are verified real.** `identities/twitter-main.state.json`
  (5004 B, `auth_token` httpOnly len 40 + `ct0` len 160) and `identities/ig-ro.state.json`
  (4154 B, 12 cookies including `sessionid` httpOnly len 77 + `ds_user_id` len 11). The IG
  bootstrap needed two attempts: Instagram's `/auth_platform/no_challenge/` device
  verification does **not** push the phone approval to the waiting desktop tab, so the tab
  must be **reloaded** after approving. `patchright install chromium` is also required
  (build 1234) — the earlier "degraded to a logged launch failure" was that missing binary,
  not code.
- **Proxy (`PROXY_URL`): DECLINED 2026-08-29 — do not revisit as pending.** No paid
  services will be used for this project; the IPRoyal static-residential plan is cancelled
  on **budget** grounds, not technical ones. Direct login from the owner's home connection
  is accepted instead. Accepted tradeoff: with no shared egress IP between the login and the
  Heroku fetch, X/Instagram may occasionally force a re-login; the remedy is re-running the
  manual `--login` step. No functionality is lost — `PROXY_URL` is optional throughout
  (`_proxy_config` returns `None` when unset → `new_context(proxy=None)`, Playwright's
  default). The fixed fingerprint (UA / viewport / locale / timezone, shared by `login()`
  and `SocialFetcher.fetch` via `StealthIdentity.context_options`) remains the **only**
  consistency measure, by decision — no compensating code was added.
- **Prerequisite before bootstrap — SECURITY: CLOSED 2026-08-29.** The root `.gitignore`
  now covers `identities/` **and** `*.state.json` (committed this session), so a live
  session-cookie file cannot be committed by accident. This also makes the DEPLOYMENT.md §4
  checklist line ("`.gitignore` covers `*.state.json`") accurate — it was aspirational before.
- **Tech debt — X half CLOSED 2026-08-29, Instagram half still open.** The parsers were
  originally validated against **constructed fixtures** modeled on the documented GraphQL
  shapes, not a real payload. That is now resolved for X: `parse_twitter_payloads` was run
  against three live captures from `x.com/search?q=%23freecourse&f=live` (20 tweet nodes,
  19 parsed items). The depth-agnostic walker needed **no** path changes for tweet
  extraction, but the real capture exposed two defects the fixtures could not:
  - **Author extraction was 100% broken** (20/20 items → `twitter:unknown`). X moved
    `screen_name` off the user result's `legacy` object into a sibling `core`; the live
    user result has no `legacy` key at all. Fixed in `f93988b` — any nested node under the
    tweet's own `core` is now accepted, so both shapes work. Re-verified live: 20/20
    authors resolved, 0 unknown.
  - **A ~2000-char keyword-stuffed spam URL** would have reached the LLM prefilter and
    extraction. Fixed in `d26e3d8` with a measured structural floor (`MAX_URL_LENGTH=300`,
    `MAX_URL_SLASHES=12`); across the 43 real URLs, length maxed at 137 and slashes at 5,
    so zero false positives and >2x headroom.
  **Instagram half CLOSED 2026-08-29 (`cc5f687`).** The live
  `instagram.com/graphql/query` media node had drifted off the documented
  profile-feed shape exactly as X had: it uses `code` (not `shortcode`), a
  `caption` **dict** with `.text` (nullable) (not `edge_media_to_caption`),
  `taken_at` (not `taken_at_timestamp`), and `like_count`/`comment_count` (not
  `edge_liked_by`/`edge_media_to_comment`). Against the old keys the parser matched
  **0 link-bearing items on every handle**. `parse_instagram_payloads` now accepts
  both shapes (id `code|shortcode`; caption dict/str/legacy-edges with a `null`
  guard; `taken_at|taken_at_timestamp`; `like_count|edge_liked_by` +
  `comment_count|edge_media_to_comment`), gating a media node on an id **plus** a
  media marker so a stray `code` can't be misread as a post. Fixtures updated to
  the live shape with the legacy shape retained under a parametrized back-compat
  test; full suite 172 passed. Validated live (dry-run, no DB write) across all six
  handles: **81 link-bearing items** — opportunitydesk 20, opportunitiesforyouth
  23, opportunitiesforafricans 24, afterschoolafrica 10, deeplearningai 4, **mlhacks
  0**. URL quality: 99 URLs, max length 122 / max 5 slashes (structural floor
  300/12, >2x headroom, 0 flagged). ~67/99 URLs are tinyurl/wp.me/opd.to/bit.ly
  shorteners → confirms central redirect resolution is load-bearing for this source
  (parser adds none by design). **Two honest limitations on record:** (1) mlhacks
  yielded 0 — its captions carry no inline URL and the profile *feed* GraphQL query
  does **not** carry the user `external_url` node, so **bio-link extraction never
  fires from these captures** (0 `bio_link` items across all six handles); Group B
  accounts that keep links only in bio will be low-yield until a profile-header
  query is captured. (2) deeplearningai's 4 came from inline `DeepLearning.AI`
  mentions, not a bio link.
- **Silent-logged-out-save defect: CLOSED 2026-08-29 (`26345bb`).** `login()` used to write
  `storage_state` unconditionally, so the first IG bootstrap saved a 1643-byte identity
  holding only 6 pre-auth cookies after the operator pressed Enter on a challenge screen.
  `LOGIN_TARGETS` now carries the auth cookie per platform (`auth_token` / `sessionid`) and
  `login()` refuses to save without it, naming the cookie and telling the operator to
  approve on the other device and **reload** before pressing Enter.
- **Instagram handles: SUPPLIED 2026-08-29 (owner-approved).** `DEFAULT_INSTAGRAM_HANDLES` at
  `adapters/social_stealth.py:73` was deliberately empty; the owner approved a six-handle list
  after the research pass: `opportunitydesk`, `opportunitiesforyouth`, `opportunitiesforafricans`,
  `afterschoolafrica` (Group A — full URL typed in caption), `deeplearningai`, `mlhacks`
  (Group B — bio link only, captions link-free). Caveat on record: all four Group A accounts
  are global scholarship/grant aggregators (dev-tool relevance is a minority slice) and three
  of the four hide destinations behind bit.ly/tinyurl/wp.me shorteners, so redirect-following
  matters. A one-off dry-run capture is authorized; **still not wired into `scheduler.py`.**
  **UPDATE 2026-08-29:** the authorized dry-run ran (see the Instagram-half tech-debt entry
  above, `cc5f687`) — 81 items across five handles, shorteners confirmed dominant (~67/99).
  Redirect-following is handled centrally by `URLCanonicalizer` at pipeline Stage A, so no
  per-parser fix was needed. **Still not wired into `scheduler.py` — separate approval required.**

- **What would unblock it:** (a) ~~add `*.state.json` to `.gitignore`~~ **DONE 2026-08-29**;
  (b) ~~manually log in once and save `identities/<name>.state.json`~~ **DONE 2026-08-29 —
  both platforms, verified by cookie inventory**; (c) ~~validate the parser against one real
  capture~~ **DONE for both** — X (`f93988b`, `d26e3d8`) and Instagram (`cc5f687`, 81 live
  items across five of six handles); (d) only then decide whether to wire `run_twitter` /
  `run_instagram` into a scheduler slot — **still requires separate explicit owner approval,
  not granted.**

---

## §3 — Deferred / low priority (feasible, deliberately not now)

### 3.1 Dashboard deployment (Next.js ops dashboard)
- **Status:** Fully built (`dashboard/`), not deployed. **Deferred by decision** after
  the item-3 research pass (2026-08-29). Runs fine locally (`npm run dev` against Neon).
- **What it is (evidence):** a real Next 14 app, not a shell — reads the live Neon DB via
  a server-only `pg` Pool (`dashboard/lib/db.ts`), and **also writes** (`ingestManualDeal`
  in `dashboard/lib/queries.ts` inserts `raw_items` under a `manual:dashboard` source).
  Auth is a single shared `DASHBOARD_API_KEY` (edge middleware + write-route double-check).
  All pages/routes are `force-dynamic`, so `next build` does **not** touch the DB.
- **Prerequisite if ever activated (concrete):** there is **no root `package.json`**, and
  the Node buildpack detects an app by a root `package.json`. The dashboard lives in the
  `dashboard/` subfolder (`web: npm --prefix dashboard run start` in the Procfile). Either
  deployment path (same-app multi-buildpack, or a second app) needs a root `package.json`
  or a `git subtree push --prefix dashboard` flow first. Neither is a one-liner.
- **Decision on record:** if activated, deploy as a **separate Heroku app**
  (`freebies-dashboard`), **never** bolted onto the scheduler app. Same-app multi-buildpack
  couples the Node build into the scheduler's slug compile: a broken dashboard build would
  block new scheduler *deploys* (it would not crash the already-running scheduler dyno, but
  it would stop shipping fixes to it). A second app removes that coupling. Cost either way
  is ~$7/mo for one more always-on dyno, drawn from the same account-level $290.28 credits.
- **Open security tension (must be resolved before any live deploy, not before):** the one
  shared `DASHBOARD_API_KEY` gates both read and the `ingestManualDeal` **write** path,
  and DEPLOYMENT.md §4 lists "read-only DB role for the dashboard" as an **unchecked** box.
  A read-only role and a manual-write path are in direct tension — deploying publicly means
  deciding deliberately: read-only role + separately-scoped write, or drop the ingest route
  from the deployed build. Resolve at deploy time, not now.
- **What would unblock it:** an actual need for remote/laptop-free monitoring. At that
  point: add root `package.json` (or subtree flow) → create `freebies-dashboard` app with
  `heroku/nodejs` → set `DATABASE_URL` + `DASHBOARD_API_KEY` → resolve the read/write auth
  tension → scale one web dyno.

### 3.2 Live secrets exposed via `heroku releases:info` — rotation deferred by owner
- **Status:** On 2026-08-29, `heroku releases:info -a freebies-hunter` was run and printed the
  full v50 config-var set into the session transcript: the Neon `DATABASE_URL` (with password),
  `LLM_API_KEY` (Groq), `LLM_FALLBACK_1_API_KEY` (OpenRouter), `LLM_FALLBACK_2_API_KEY`,
  `FIRECRAWL_API_KEY`, and all 10 Discord webhook URLs. Nothing was transmitted anywhere; the
  values sit in a local transcript file only.
- **Decision on record (owner, 2026-08-29):** **rotation DEFERRED, not skipped.** Do not raise
  again unless the owner brings it up (same discipline as §1 paused items).
- **What would unblock / require it:** revisit **before any wider team access or public
  deployment** — at that point rotate all six credential groups (webhooks cheapest, Neon
  password highest-value). The mistake itself is corrected going forward: use
  `heroku releases -n N` (release list only), never `releases:info` (dumps all config vars).

### 3.3 Dashboard npm dependency vulnerabilities (next@14.2.35)
- **Status:** `npm audit` in `dashboard/` reports **2 high-severity advisories** — `next`
  (9.3.4-canary … 16.3.0-preview.10) and its bundled `postcss` (<=8.5.22). Classes: DoS via
  Server Components / Image Optimizer, cache poisoning, XSS in App Router, SSRF in Server
  Actions/rewrites; postcss path traversal + XSS in CSS stringify.
- **Decision on record (owner, 2026-08-29):** **known issue, not urgent now** — the dashboard
  runs local-only, so the network-facing attack surface is not exposed.
- **What would unblock / require it:** the only fix is `npm audit fix --force`, which installs
  `next@16.3.3` — a **breaking major-version upgrade** (App Router API changes). Resolve
  **before any live dashboard deployment** (ties into §3.1); until then, do not run the forced
  upgrade against the working local build.

---

## §4 — Closed this session (resolved, with commit references)

### 4.1 Documentation drift: stale Redis add-on plan
- **Closed — commit `b63a875`.** DEPLOYMENT.md no longer describes a `heroku-redis:mini`
  add-on that was never built. Replaced with the actual in-process reality: `max_workers=1`
  serialization (zero concurrent LLM calls), dispatcher self-pacing under Discord's
  30 msg/min, per-adapter sleeps, and sha256 + app-side cosine dedup in Postgres. Architecture
  diagram, add-on table, config vars, deploy command, and the Heroku-vs-AWS table were all
  reconciled to "zero add-ons / ~$7 Basic dyno" so the doc no longer self-contradicts.

### 4.2 Migration `2026-08-28_add_approved_status.sql` mislabeled as pending
- **Closed — commit `b63a875`.** Added a `STATUS: ALREADY APPLIED` header (verified
  2026-08-29 against the live `discovered_channels_status_check` constraint, which includes
  `'approved'`). Marked historical/idempotent — nothing to run. SQL unchanged.

### 4.3 Social stealth parser TODO
- **Closed (feature) — commit `1fa98d3`.** The parse-into-`raw_items` step is implemented
  and tested. Residual follow-ups (identity bootstrap, real-payload validation, `.gitignore`
  gap) are tracked as the open item **§2.1** — the *feature* is done, the *activation* is not.

### 4.4 Firecrawl credit blowout — full-catalog re-scrape every slot
- **INCIDENT (2026-08-29).** The Firecrawl dashboard showed **20,961 credits consumed in 7
  days** (near-zero for weeks prior), concentrated in the last 1–2 days, driving the account
  to **−1 credit**. This is preserved as a worked incident so the failure mode is not repeated.
- **Root cause (three compounding defects, all in `adapters/firecrawl_adapter.py` + its call
  site):**
  1. **Ungated per-slot call.** `scheduler.py` invoked `_run_firecrawl(category)` on **every one
     of the 27 slots** (~every 53 min, ~27×/day), unlike github/openrouter which gate to one
     slot. Firecrawl's `map` ignores the category filter, so all 27 runs scraped the same site.
  2. **Fail-open freshness filter.** `_filter_fresh` kept only pages whose JSON-LD
     `dateModified` was within 48h, but resourify pages carry no matching `dateModified` and the
     fallback was *"include to be safe"* (+ include-on-error). It logged `Freshness filter:
     125 → 125` every run — it filtered nothing.
  3. **Paid extract format, no dedup.** Each run scraped all 125 URLs with `formats=["extract"]`
     (LLM extraction, billed well above a plain scrape), re-reading the same unchanged catalog.
- **Timing proof it was NOT the v51 deploy (`8a81926`):** `firecrawl_adapter.py` was unchanged
  since the 08-26 baseline `754b447` (only a log-wording tweak `220696e` on 08-28) and is
  **absent from the v50→v51 diff** (`4c37a8ea..8a81926`, which touched pipeline/verifier/
  dashboard/dispatcher/tests). The spike aligns with Firecrawl first *executing in production*
  ~08-28 (config vars set v46/v47, first firecrawl-containing deploy v48/v49 on 08-28 ~10:41).
  Credits hit zero ~08:56 UTC on 08-29, ~1h **before** the 09:56 v51 restart — the deploy was
  downstream of the exhaustion, not its cause. No `/crawl` (recursive), no retry-storm.
- **No suspicious doc / hardcoded key in the repo** — a whole-repo grep found only the
  legitimate `FIRECRAWL_API_KEY=fc-...` placeholder in the adapter's own error string. The
  "firecrawl skill/onboarding guide with a hardcoded key" seen in a separate browser-Claude
  chat is **not** present here and was not acted on.
- **Emergency pause (v52, 2026-08-29):** `heroku config:unset FIRECRAWL_API_KEY`. Verified on
  the next slot (11:33 UTC) — the adapter logged `FIRECRAWL_API_KEY not set` and made **zero**
  API calls (no `Mapping ...` line), vs the 10:40 slot which still called `map_url`.
- **Durable fix — commit `93e10bf` (deployed 2026-08-29, see below):**
  1. **Frequency gate.** `_run_firecrawl(category, run_number)` runs on exactly **one slot/day**
     (`all_deals`, run 1), mirroring the github/openrouter gating idiom.
  2. **URL-diff dedup.** `_filter_fresh` replaced by `_filter_unseen`, which drops URLs already
     in `raw_items` for this source within **`RESCRAPE_AFTER_DAYS = 14`**. Fails *closed*
     against stored state instead of *open* against page metadata.
  3. **Extract cost control** falls out of (2): the paid `extract` format lives only in
     `_batch_scrape`, which now only receives the unseen set; already-seen pages never re-extract.
  Hermetic tests added in `tests/test_firecrawl_adapter.py` (gate fires once/day, dedup
  drop/pass, dry-run skip, query scoping); full suite **178 passed**.
- **Before → after (page-scrapes/day):** **~3,375 → ~12–15 (>99% cut).** Cold start scrapes 125
  once; days 2–14 scrape ~0 (unchanged catalog); steady state ~9/day refresh (125÷14) + ~3/day
  non-offer pages (pages with `is_offer=false` write no `raw_item`, so they re-scrape daily —
  quantified, negligible) + any new slugs. The relative cut holds regardless of the exact
  per-page credit rate because both multipliers (27× frequency, full-catalog-every-time) are gone.
- **Deploy + re-enable (2026-08-29):** commit `93e10bf` pushed to `origin/main` and deployed via
  the Heroku dashboard "Deploy Branch"; `FIRECRAWL_API_KEY` re-set afterward. **Owner adds credits
  only after deploy is confirmed** — the daily gated run resumes real scrapes once both the key and
  a balance exist. Standing lesson: any new paid adapter must be gated + deduped **before** it
  first runs in production, and use `heroku releases -n N` (never `releases:info`) when inspecting.

---

## Appendix — DEPLOYMENT.md §4 security-checklist status (evidence, 2026-08-29)

The checklist boxes are all rendered unticked in the doc; here is their real state so the
genuinely-open ones are visible:

| Checklist line | Real state |
|---|---|
| Secrets in config vars; `.gitignore` covers `.env`, `*.state.json`, `sessions/` | **Satisfied (2026-08-29).** `.env`, `sessions/`, `*.session`, **`identities/` + `*.state.json`** all covered. |
| `DATABASE_URL` server-only; read-only DB role for the dashboard | **Open.** Server-only is satisfied; read-only role is not — see §3.1 tension. |
| Telegram session account is disposable | **N/A while §1.1 is paused** (no session exists). |
| Discord webhook treated as a secret | **Satisfied.** Commit `188b874` + `scheduler.py:47` set httpx to WARNING so webhook URLs never hit the log stream. |
| Scheduler commands pinned to repo, no shell strings | **Satisfied.** `scheduler.py` uses direct imports + function calls; no shell command strings. |
| AWS fallback: Budgets alarm + no NAT/EIP/extra instances | **N/A.** AWS fallback is not deployed (Heroku is live). |
