# ============================================================================
# Heroku Procfile — Developer Freebies Aggregation System
# Deploy:  heroku create --region us   (us region keeps Telegram reachable
#                                     past any local ISP blocks)
#          heroku addons:create heroku-postgresql:mini
#          heroku addons:create heroku-redis:mini
#          heroku config:set DISCORD_WEBHOOK_URL=... TG_API_ID=... TG_API_HASH=...
#          git push heroku main
#
# Process types (Eco dyno pool = 1000 hrs/month, $5/mo):
#   web    -> Next.js dashboard (Phase 4 only; omit until then to save hours)
#   worker -> always-on async ingestion loop (Telegram realtime monitor)
#
# One-off jobs (add via the FREE Heroku Scheduler add-on, NOT as dynos):
#   python -m crawler.pipeline route=ingest        (3x daily)
#   python discord_dispatcher.py --limit 10         (before/after each ingest)
# ============================================================================
web: npm --prefix dashboard run start
worker: python -m crawler.worker