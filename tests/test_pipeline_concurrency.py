"""Stage-B concurrency tests for crawler.pipeline (Distribution Step 3).

Exercise the flag-gated extraction concurrency wired into _run_stage_b():
  (a) flag-off  -> extraction and the write stage stay sequentially interleaved
                   (extract chunk i, write chunk i, THEN extract chunk i+1)
  (b) flag-on   -> N batches extracted concurrently, results still written in
                   chunk order with each chunk's (chunk, offers) pairing intact
  (c) flag-on   -> one batch raising does not corrupt the others (gather isolates
                   the failure; only that chunk is dead-lettered)
  (d) flag-on   -> the Semaphore actually bounds in-flight batches to the
                   LLM_MAX_CONCURRENT_BATCHES cap

The DB and LLM are faked out entirely — mark_raw_item_attempt / write_offer /
VerificationVerdict are monkeypatched to record calls, so no Postgres or network
is touched. Coroutines are driven with asyncio.run() (no pytest-asyncio needed).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import crawler.pipeline as pipeline
from crawler.pipeline import _extract_chunks_concurrent, _run_stage_b
from crawler.verifier import NO_VERDICT

_SENTINEL_TITLE = "https://ex/{}"  # encodes the row id so we can assert pairing


def _survivor(row_id: int):
    """One Stage-A survivor tuple: (row, normalized, primary, live)."""
    primary = SimpleNamespace(canonical=_SENTINEL_TITLE.format(row_id),
                              final=_SENTINEL_TITLE.format(row_id))
    live = SimpleNamespace(ok=False, status="live")
    return ({"id": row_id}, SimpleNamespace(), primary, live)


class _FakeDedup:
    def check(self, url):
        return SimpleNamespace(is_dup=False)

    def check_title_hash(self, title, category):
        return SimpleNamespace(is_dup=False)

    def check_semantic(self, emb):
        return SimpleNamespace(is_dup=False)


class _FakeExtractor:
    """extract_batch returns one offer per item (title=primary.canonical), or a
    per-chunk override (raise / NO_VERDICT / None). embed() returns [] so the
    semantic-dedup branch is skipped and the write path stays minimal."""

    def __init__(self, events=None, overrides=None, delay=0.0, counter=None):
        self.events = events            # optional shared trace list
        self.overrides = overrides or {}  # canonical-of-first-item -> action
        self.delay = delay
        self.counter = counter          # optional shared {cur, peak} dict

    async def embed(self, texts):
        return []

    async def extract_batch(self, items, mode="deal"):
        key = items[0][1].canonical  # primary.canonical of the first item
        if self.events is not None:
            self.events.append(("extract", key))
        if self.counter is not None:
            self.counter["cur"] += 1
            self.counter["peak"] = max(self.counter["peak"], self.counter["cur"])
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.counter is not None:
            self.counter["cur"] -= 1

        action = self.overrides.get(key)
        if action == "raise":
            raise RuntimeError(f"provider outage for {key}")
        if action == "no_verdict":
            return [NO_VERDICT] * len(items)
        if action == "reject":
            return [None] * len(items)
        return [
            SimpleNamespace(title=primary.canonical, description="",
                            url=primary.final, confidence=0.5, category="other")
            for _, primary in items
        ]


def _patch_write_layer(monkeypatch):
    """Fake out the DB write layer; return (writes, marks) recording lists."""
    writes: list[str] = []
    marks: list[tuple] = []
    monkeypatch.setattr(pipeline, "write_offer",
                        lambda conn, verdict, normalized: (writes.append(verdict.offer.title) or 1))
    monkeypatch.setattr(pipeline, "mark_raw_item_attempt",
                        lambda conn, rid, reason, permanent: marks.append((rid, reason, permanent)))
    monkeypatch.setattr(pipeline, "VerificationVerdict",
                        lambda offer, live, dup, primary: SimpleNamespace(offer=offer))
    return writes, marks


def _set_flag(monkeypatch, on: bool):
    if on:
        monkeypatch.setenv("LLM_DISTRIBUTION_ENABLED", "true")
    else:
        monkeypatch.delenv("LLM_DISTRIBUTION_ENABLED", raising=False)


def _fresh_stats():
    return {"llm_rejected": 0, "llm_unavailable": 0, "dup": 0,
            "offers_written": 0, "errors": 0}


# --------------------------------------------------------------------------- #
# (a) flag-off: extraction + write stay sequentially interleaved
# --------------------------------------------------------------------------- #
def test_flag_off_sequential_interleave(monkeypatch):
    _set_flag(monkeypatch, on=False)
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 2)
    writes, _marks = _patch_write_layer(monkeypatch)
    events: list[tuple] = []
    # Record writes into the same trace so we can see interleaving order.
    monkeypatch.setattr(pipeline, "write_offer",
                        lambda conn, verdict, normalized: (
                            writes.append(verdict.offer.title),
                            events.append(("write", verdict.offer.title)), 1)[-1])

    survivors = [_survivor(k) for k in range(4)]  # 2 chunks of 2
    ext = _FakeExtractor(events=events)
    stats = _fresh_stats()

    asyncio.run(_run_stage_b(None, _FakeDedup(), ext, survivors, stats, dry_run=False))

    # Chunk 0 is extracted, both its items written, and ONLY THEN is chunk 1
    # extracted — proving extract->write stays interleaved and sequential.
    assert events == [
        ("extract", "https://ex/0"),
        ("write", "https://ex/0"),
        ("write", "https://ex/1"),
        ("extract", "https://ex/2"),
        ("write", "https://ex/2"),
        ("write", "https://ex/3"),
    ]
    assert stats["offers_written"] == 4 and stats["errors"] == 0


# --------------------------------------------------------------------------- #
# (b) flag-on: N batches concurrent, still written in chunk order + paired
# --------------------------------------------------------------------------- #
def test_flag_on_concurrent_all_succeed_ordered(monkeypatch):
    _set_flag(monkeypatch, on=True)
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 2)
    monkeypatch.setenv("LLM_MAX_CONCURRENT_BATCHES", "4")
    writes, _marks = _patch_write_layer(monkeypatch)

    survivors = [_survivor(k) for k in range(6)]  # 3 chunks of 2
    # Earlier chunks extract SLOWER than later ones; if writes were tied to
    # extraction-completion order they'd come out scrambled. They don't, because
    # the write stage iterates zip(chunks, results) in chunk order.
    class _Staggered(_FakeExtractor):
        async def extract_batch(self, items, mode="deal"):
            first_id = int(items[0][1].canonical.rsplit("/", 1)[1])
            await asyncio.sleep(0.03 - first_id * 0.004)  # chunk 0 slowest
            return await super().extract_batch(items, mode=mode)

    ext = _Staggered()
    stats = _fresh_stats()

    asyncio.run(_run_stage_b(None, _FakeDedup(), ext, survivors, stats, dry_run=False))

    assert writes == [_SENTINEL_TITLE.format(k) for k in range(6)]  # strict order
    assert stats["offers_written"] == 6 and stats["errors"] == 0


# --------------------------------------------------------------------------- #
# (c) flag-on: one batch raising is isolated — other batches still succeed
# --------------------------------------------------------------------------- #
def test_flag_on_one_batch_fails_others_unaffected(monkeypatch):
    _set_flag(monkeypatch, on=True)
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 2)
    monkeypatch.setenv("LLM_MAX_CONCURRENT_BATCHES", "4")
    writes, marks = _patch_write_layer(monkeypatch)

    survivors = [_survivor(k) for k in range(6)]  # chunks: [0,1] [2,3] [4,5]
    ext = _FakeExtractor(overrides={"https://ex/2": "raise"})  # middle chunk blows up
    stats = _fresh_stats()

    asyncio.run(_run_stage_b(None, _FakeDedup(), ext, survivors, stats, dry_run=False))

    # The two healthy chunks wrote all four of their offers, in order.
    assert writes == ["https://ex/0", "https://ex/1", "https://ex/4", "https://ex/5"]
    assert stats["offers_written"] == 4
    # Only the failed chunk's rows were dead-lettered, as a retryable batch_error.
    assert marks == [(2, "batch_error:RuntimeError", False),
                     (3, "batch_error:RuntimeError", False)]
    assert stats["errors"] == 2


# --------------------------------------------------------------------------- #
# (d) flag-on: the Semaphore bounds concurrent in-flight batches to the cap
# --------------------------------------------------------------------------- #
def test_flag_on_semaphore_caps_inflight(monkeypatch):
    _set_flag(monkeypatch, on=True)
    monkeypatch.setenv("LLM_MAX_CONCURRENT_BATCHES", "2")

    counter = {"cur": 0, "peak": 0}
    ext = _FakeExtractor(delay=0.02, counter=counter)
    chunks = [[_survivor(k)] for k in range(5)]  # 5 single-item chunks

    results = asyncio.run(_extract_chunks_concurrent(ext, chunks))

    assert len(results) == 5                 # all chunks processed, order preserved
    assert counter["peak"] == 2              # reached the cap ...
    assert counter["peak"] <= 2              # ... and never exceeded it

