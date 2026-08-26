# ============================================================================
# Heroku Procfile — Developer Freebies Aggregation System
# Deploy:  heroku create --region us   (us region keeps Telegram reachable
#                                     past any local ISP blocks)
#          heroku addons:create heroku-redis:mini
#          heroku config:set DISCORD_WEBHOOK_URL=... TG_API_ID=... TG_API_HASH=...
#          git push heroku main
#
# Process types (Eco dyno pool = 1000 hrs/month, $5/mo):
#   web       -> Next.js dashboard (Phase 4 only; omit until then to save hours)
#   worker    -> Telegram realtime monitor + 6h batch ingest (NO pipeline/dispatch)
#   scheduler -> 27-slot orchestrator: per-category ingest + pipeline + dispatch.
#                This is the ONLY process that turns raw_items into sent offers.
#
# NOTE: one always-on dyno ≈ 744 hrs/month against a 1000-hr Eco pool, so only
#       ONE always-on process runs. `scheduler` supersedes `worker`: it runs every
#       ingest adapter AND the pipeline AND dispatch, whereas `worker` only ingests.
#       `worker` is commented out (not deleted) so it stays recoverable — re-enable
#       it only if the Telegram realtime monitor is given working credentials.
#
# One-off jobs (add via the FREE Heroku Scheduler add-on, NOT as dynos):
#   python discord_dispatcher.py --limit 10         (before/after each ingest)
# ============================================================================
web: npm --prefix dashboard run start
# worker: python -m crawler.worker
scheduler: python scheduler.py