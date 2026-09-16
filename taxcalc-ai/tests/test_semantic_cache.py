# taxcalc-ai/tests/test_semantic_cache.py
"""Semantic cache: near-duplicate collision, tenant separation, and epoch invalidation.

The ``redis_client`` fixture lives in ``conftest.py`` because the pipeline test wants the same
container. Testcontainers Redis rather than fakeredis. Two of the three properties are properties of
Redis: that ``INCR`` on a missing key initialises it to 1 (which is what makes the first epoch
bump work without an initialisation step), and that ``SET ... EX`` stores a TTL the key actually
honours. A fake would agree with whatever this code assumed.

The tenant-separation test is the important one in this file. A semantic cache keyed on the
embedding alone is a cross-tenant data leak with a hit-rate graph in front of it, and it is a
leak that *improves* the metric it would be noticed by.
"""

from __future__ import annotations

import json

import numpy as np
import redis
from numpy.typing import NDArray

from taxcalc_ai.cache import (
    QUANTISATION_FACTOR,
    bump_epoch,
    cache_lookup,
    cache_store,
    get_epoch,
)


def _vector(seed: float) -> NDArray[np.float32]:
    """A deterministic unit-length vector, offset by ``seed``.

    Not random: the whole point of the near-duplicate test is that two vectors differing by
    less than the quantisation step land in the same bucket, which needs the difference to be
    chosen rather than sampled.
    """
    vec = np.zeros(384, dtype=np.float32)
    vec[0] = 1.0
    vec[1] = np.float32(seed)
    return vec / np.linalg.norm(vec).astype(np.float32)


def _answer(tenant_id: str, text: str = "cached answer") -> dict[str, object]:
    """A cached-answer payload whose citations carry ``tenant_id``.

    The tenant on each citation is what the defence-in-depth check in ``cache_lookup`` reads;
    an answer stored without it is a permanent miss rather than a leak.
    """
    return {
        "text": text,
        "citations": [
            {
                "chunk_id": "chunk-doc-001-p0",
                "chunk_text": "some context",
                "score": 0.9,
                "tenant_id": tenant_id,
            }
        ],
        "rerank_timed_out": False,
    }


def test_two_near_duplicate_query_vectors_hit_the_same_cache_key(
    redis_client: redis.Redis,
) -> None:
    """Vectors differing by less than the quantisation step collide; a distinct one does not.

    This is what makes the cache *semantic* rather than an exact-match cache in front of an
    embedding: "what is the single standard deduction" and "standard deduction for single
    filers?" embed to nearby vectors, and a cache that missed on that difference would never
    hit at all.
    """
    tenant = "tenant-nearest"
    stored = _answer(tenant)

    # 1/(2 * factor) is half the quantisation step, so both round to the same int32 bucket.
    below_step = 1.0 / (2.0 * QUANTISATION_FACTOR)
    cache_store(redis_client, _vector(0.0), tenant, stored)

    hit = cache_lookup(redis_client, _vector(below_step), tenant)
    assert hit is not None
    assert hit["text"] == "cached answer"

    # And a genuinely different query does NOT hit: a cache that returns something for
    # everything is worse than no cache, because it is confidently wrong rather than slow.
    assert cache_lookup(redis_client, _vector(0.9), tenant) is None


def test_the_same_near_duplicate_does_not_hit_another_tenants_entry(
    redis_client: redis.Redis,
) -> None:
    """Tenant B asking tenant A's question gets a miss, at both layers of defence.

    Asserted twice on purpose. The first assertion covers the key: ``tenant_id`` is a key
    component, so B addresses a different slot. The second bypasses the key entirely - it
    writes A's answer directly into B's slot - and proves the citation check still refuses to
    serve it. That second layer is redundant while the key is correct, which is exactly why it
    is worth having: it is what survives someone refactoring the key format.
    """
    query = _vector(0.25)
    cache_store(redis_client, query, "tenant-a", _answer("tenant-a", "tenant A's answer"))

    assert cache_lookup(redis_client, query, "tenant-a") is not None
    assert cache_lookup(redis_client, query, "tenant-b") is None

    # Layer two: plant tenant A's payload under tenant B's own key, defeating the key check.
    from taxcalc_ai.cache import _bucket_key

    planted_key = _bucket_key(query, "tenant-b", get_epoch(redis_client, "tenant-b"))
    redis_client.set(planted_key, json.dumps(_answer("tenant-a", "tenant A's answer")))

    # The key now matches, and the lookup still refuses: the cited chunk belongs to tenant A.
    assert cache_lookup(redis_client, query, "tenant-b") is None
    # The entry is still physically there - the check reports a miss rather than deleting
    # someone else's data on a read path.
    assert redis_client.get(planted_key) is not None


def test_bumping_the_epoch_makes_the_prior_key_unreachable(
    redis_client: redis.Redis,
) -> None:
    """``bump_epoch`` invalidates a tenant's whole cache in one write, and only that tenant's.

    One ``INCR`` rather than a keyspace scan: ``KEYS`` blocks the server and ``SCAN`` races the
    writes it is chasing. After the bump the old keys are simply unaddressable and age out on
    their own TTL.
    """
    query = _vector(0.4)
    cache_store(redis_client, query, "tenant-a", _answer("tenant-a"))
    cache_store(redis_client, query, "tenant-c", _answer("tenant-c"))
    assert cache_lookup(redis_client, query, "tenant-a") is not None

    before = get_epoch(redis_client, "tenant-a")
    after = bump_epoch(redis_client, "tenant-a")

    assert after == before + 1
    assert get_epoch(redis_client, "tenant-a") == after
    assert cache_lookup(redis_client, query, "tenant-a") is None
    # Scoped to the tenant that was bumped: an ingest for one tenant must not throw away
    # another tenant's cache.
    assert cache_lookup(redis_client, query, "tenant-c") is not None

    # A tenant that has never been bumped reads epoch 0 rather than raising, so the first
    # request of a brand-new tenant works.
    assert get_epoch(redis_client, "tenant-never-seen") == 0
    # And INCR initialises a missing counter to 1, which is what lets the first bump work
    # without a separate initialisation step.
    assert bump_epoch(redis_client, "tenant-never-seen") == 1


def test_a_stored_answer_carries_a_ttl_as_a_backstop_for_the_epoch(
    redis_client: redis.Redis,
) -> None:
    """Entries expire even if nobody ever bumps the epoch.

    The TTL is not an optimisation: if an ingest lands and the bump task fails, the epoch never
    moves and every stale entry would otherwise be served forever.
    """
    query = _vector(0.55)
    cache_store(redis_client, query, "tenant-ttl", _answer("tenant-ttl"), ttl_seconds=120)

    from taxcalc_ai.cache import _bucket_key

    ttl = redis_client.ttl(_bucket_key(query, "tenant-ttl", get_epoch(redis_client, "tenant-ttl")))
    assert isinstance(ttl, int)
    assert 0 < ttl <= 120
