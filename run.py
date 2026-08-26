#!/usr/bin/env python3
"""
PowerShell-compatible launcher for the crawler package.

Why this exists: the AutoClaw-bundled (embedded) Python runs in isolated mode,
which ignores both the current directory and PYTHONPATH — including for child
processes. This launcher therefore imports the targets IN-PROCESS after
injecting the project root into sys.path.

Usage (from anywhere):
    python run.py pipeline --dry-run
    python run.py pipeline --limit 25
    python run.py pipeline --source telegram:default --no-dispatch
    python run.py dispatcher --dry-run
    python run.py dispatcher --limit 10
    python run.py worker
    python run.py test
    python run.py ingest-github --dry-run
    python run.py ingest-reddit --dry-run
    python run.py ingest-hn --dry-run
    python run.py ingest-mirrors --dry-run
    python run.py ingest-mirrors --seed     # insert default source rows
    python run.py ingest-deep-web --dry-run
    python run.py ingest-deep-web --category ai_apis
    python run.py ingest-deep-web --engine serper

With a standard python.org / venv Python you can equally use:
    python -m crawler.pipeline --dry-run
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # adapters/sessions/identities resolve relative to the project


def run_pipeline(argv: list[str]) -> int:
    from crawler.pipeline import main

    sys.argv = ["crawler.pipeline", *argv]
    return main()


def run_dispatcher(argv: list[str]) -> int:
    import discord_dispatcher

    sys.argv = ["discord_dispatcher", *argv]
    return discord_dispatcher.main()


def run_worker(argv: list[str]) -> int:
    import asyncio

    from crawler.worker import main as worker_main

    if argv:
        print("worker takes no arguments", file=sys.stderr)
        return 2
    asyncio.run(worker_main())
    return 0


def run_test(argv: list[str]) -> int:
    import pytest

    return pytest.main(["-v", *argv])


def run_ingest_github(argv: list[str]) -> int:
    from adapters.github_adapter import main

    sys.argv = ["adapters.github_adapter", *argv]
    main()
    return 0


def run_ingest_reddit(argv: list[str]) -> int:
    from adapters.reddit_adapter import main

    sys.argv = ["adapters.reddit_adapter", *argv]
    main()
    return 0


def run_ingest_hn(argv: list[str]) -> int:
    from adapters.hn_adapter import main

    sys.argv = ["adapters.hn_adapter", *argv]
    main()
    return 0


def run_ingest_mirrors(argv: list[str]) -> int:
    from adapters.creator_mirrors import main

    sys.argv = ["adapters.creator_mirrors", *argv]
    main()
    return 0


def run_ingest_deep_web(argv: list[str]) -> int:
    from adapters.deep_web_adapter import main

    sys.argv = ["adapters.deep_web_adapter", *argv]
    main()
    return 0


def run_ingest_firecrawl(argv: list[str]) -> int:
    from adapters.firecrawl_adapter import main

    sys.argv = ["adapters.firecrawl_adapter", *argv]
    main()
    return 0


def run_scheduler(argv: list[str]) -> int:
    import scheduler as _scheduler

    sys.argv = ["scheduler", *argv]
    _scheduler.main()
    return 0


COMMANDS = {
    "pipeline":           run_pipeline,
    "dispatcher":         run_dispatcher,
    "worker":             run_worker,
    "test":               run_test,
    "ingest-github":      run_ingest_github,
    "ingest-reddit":      run_ingest_reddit,
    "ingest-hn":          run_ingest_hn,
    "ingest-mirrors":     run_ingest_mirrors,
    "ingest-deep-web":    run_ingest_deep_web,
    "ingest-firecrawl":   run_ingest_firecrawl,
    "scheduler":          run_scheduler,
}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, extra = args[0], args[1:]
    if command not in COMMANDS:
        print(f"Unknown command: {command!r}\nValid: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    return COMMANDS[command](extra)


if __name__ == "__main__":
    raise SystemExit(main())