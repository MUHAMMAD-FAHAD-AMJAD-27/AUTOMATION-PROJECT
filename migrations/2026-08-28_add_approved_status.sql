-- Migration: add 'approved' to discovered_channels.status
-- ========================================================
-- STATUS: ALREADY APPLIED to the live Neon DB (verified 2026-08-29 — the live
-- discovered_channels_status_check constraint includes 'approved'). This file is
-- kept as a HISTORICAL record of the schema change, NOT a pending action. It is
-- idempotent (DROP CONSTRAINT IF EXISTS + re-ADD), so re-running is harmless, but
-- there is nothing to do here. Telegram auto-join remains dormant regardless.
--
-- Adds the human-review tier for the Telegram auto-join gate. Discovery writes
-- rows as 'new'; a human promotes chosen rows to 'approved' (run.py
-- approve-channel); _join_candidates joins only 'approved' rows, and only when
-- TG_AUTO_JOIN is on.
--
-- The live Neon DB was created with the old CHECK constraint
-- (status IN ('new','joined','failed','dropped')), so approve-channel would be
-- rejected until this runs. Auto-discovery is dormant (no TG creds yet), so
-- there is no rush — but this MUST be applied before enabling TG_AUTO_JOIN.
--
-- Safe + reversible: it only widens the allowed value set. No data is touched.
-- Run ONCE against the live DB, e.g. from a Heroku one-off dyno:
--     heroku run "python -c \"import psycopg,os; \
--       psycopg.connect(os.environ['DATABASE_URL']).cursor().execute(open('migrations/2026-08-28_add_approved_status.sql').read())\""
-- or simply paste the two statements into any SQL console pointed at the DB.

ALTER TABLE discovered_channels DROP CONSTRAINT IF EXISTS discovered_channels_status_check;
ALTER TABLE discovered_channels
    ADD CONSTRAINT discovered_channels_status_check
    CHECK (status IN ('new','approved','joined','failed','dropped'));
