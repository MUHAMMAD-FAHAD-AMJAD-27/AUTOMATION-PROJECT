-- Migration: add 'notable_repo' to offers.category
-- =================================================
-- STATUS: PENDING — NOT yet applied to the live Neon DB (as of 2026-08-29).
-- Part of Item 4b (the non-deal "notable_repo" verifier lane). MUST be applied
-- BEFORE the GitHub-Trending scheduler wiring (Item 4a) is deployed, otherwise
-- any offer the repo lane classifies as category='notable_repo' is rejected by
-- the old CHECK constraint and the write fails.
--
-- The live Neon DB was created with the old category CHECK list (14 values, no
-- 'notable_repo'), so write_offer() would raise a constraint violation on the
-- first trending repo. This migration only WIDENS the allowed set — no existing
-- row is touched, no data migrated.
--
-- Safe + reversible + idempotent (DROP IF EXISTS + re-ADD). Run ONCE against the
-- live DB, e.g. from a Heroku one-off dyno:
--     heroku run "python -c \"import psycopg,os; \
--       psycopg.connect(os.environ['DATABASE_URL']).cursor().execute(open('migrations/2026-08-29_add_notable_repo_category.sql').read())\""
-- or paste the statements into any SQL console pointed at the DB.
--
-- Source of truth for the category list is crawler/categories.py (CATEGORY_DEFS);
-- schema.sql mirrors it and tests/test_categories.py asserts the two stay in sync.

ALTER TABLE offers DROP CONSTRAINT IF EXISTS offers_category_check;
ALTER TABLE offers
    ADD CONSTRAINT offers_category_check
    CHECK (category IN ('cloud','llm','hosting','domain','tools','student','course','coupon',
                        'ai_tools','coding_agents',
                        'open_source_repo','llm_api_drop','student_pack','saas_deal',
                        'notable_repo',
                        'other'));
