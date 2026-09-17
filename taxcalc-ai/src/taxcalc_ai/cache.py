# taxcalc-ai/src/taxcalc_ai/cache.py
"""Redis semantic cache keyed by ``(tenant_id, cache-epoch, quantised embedding)``.

An exact-string cache is nearly useless in front of a RAG pipeline: "what is the single
standard deduction" and "standard deduction for single filers?" are the same question and would
miss each other every time. A *semantic* cache keys on the query's embedding instead, so
near-duplicate phrasings collide and the second one costs a Redis GET rather than a retrieval,
a rerank and a generation.

**The key carries ``tenant_id``, and this is the part that is easy to get wrong and expensive to
get wrong.** A cache keyed on the embedding alone is a cross-tenant data leak with a Redis
hit-rate graph in front of it: tenant B asks a question tenant A already asked, the embeddings
collide, and B is served A's answer - including A's cited chunk text. The tenant is therefore
part of the key, not a field inside the value, so the two tenants cannot address the same slot.

**And a defence in depth on top of that, because the key alone is one typo from a leak.**
:func:`cache_lookup` re-checks, on every hit, that every citation in the stored answer carries
the requesting tenant, and treats a mismatch as a miss. That check is redundant when the key is
right, which is exactly why it is worth having: it is the assertion that survives a refactor of
the key format. The one failure mode this project treats as unacceptable does not get a single
line of defence.

**Quantisation is what makes "semantic" work with an exact-match key store.** Redis GET is exact
by nature. Rounding each dimension to 2 decimal places (``round(v * 100)``) makes vectors that
differ only slightly hash identically, so near-duplicates land in the same bucket. That is a
deliberate trade rather than a free lunch: it is a *bucketing* scheme, so two questions can
collide without being paraphrases, and the coarser the rounding the more often that happens.
The alternative - a vector index with a cosine threshold - is more precise and needs a second
ANN structure to maintain; this is the cheap version, and the rounding factor is the knob.

**The cache-epoch is how invalidation happens in one atomic write.** The corpus changes when the
Airflow ingest runs, and every cached answer built from the old corpus is then potentially
stale. Deleting the matching keys means scanning the keyspace - ``KEYS`` blocks the server,
``SCAN`` is a loop that races the writes it is chasing. Instead the epoch is a per-tenant
counter embedded in the key: :func:`bump_epoch` is a single ``INCR``, after which every
previously-written key for that tenant is simply unreachable and expires on its own TTL. One
round trip invalidates a tenant's entire cache, and it cannot half-succeed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Final

import numpy as np
import redis
from langsmith import traceable
from numpy.typing import NDArray

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.cache")

#: Quantisation factor. Each dimension is rounded to ``round(v * 100)``, i.e. 2 decimal places -
#: coarse enough that paraphrases collide, fine enough that unrelated questions mostly do not.
#: Raising it narrows the buckets (fewer false hits, fewer true hits); lowering it widens them.
QUANTISATION_FACTOR: Final[int] = 100

#: Characters of the SHA-256 digest kept in the key. 16 hex chars is 64 bits: enough that an
#: accidental collision inside one tenant's epoch is not a practical concern, short enough that
#: a key is readable in ``redis-cli --scan`` output during an incident.
HASH_PREFIX_LENGTH: Final[int] = 16

#: Default TTL on a cached answer, in seconds. One hour: long enough to absorb the repeat
#: traffic a semantic cache exists for, short enough that a corpus change nobody bumped the
#: epoch for cannot serve stale context indefinitely.
DEFAULT_TTL_SECONDS: Final[int] = 3600

#: Key holding a tenant's cache epoch. Separate from the answer keys so ``INCR`` on it cannot
#: touch them.
_EPOCH_KEY_TEMPLATE: Final[str] = "taxcalc_ai:cache-epoch:{tenant_id}"

#: The answer key. ``tenant_id`` is a key component, NOT a field in the value - see the module
#: docstring.
_SEMANTIC_KEY_TEMPLATE: Final[str] = "taxcalc_ai:sem:{tenant_id}:e{epoch}:{digest}"

#: Field in a cached answer holding its citations. Each citation carries its own ``tenant_id``,
#: which is what the defence-in-depth check reads.
CITATIONS_FIELD: Final[str] = "citations"

#: Field in a citation holding the owning tenant.
CITATION_TENANT_FIELD: Final[str] = "tenant_id"


def _bucket_key(query_vec: NDArray[np.float32], tenant_id: str, epoch: int) -> str:
    """Build the semantic cache key for one query vector under one tenant and epoch.

    ``.tobytes()`` on an ``int32`` array rather than hashing a formatted string: the byte
    representation is unambiguous and has no locale- or repr-dependent formatting to drift
    between versions of NumPy. ``int32`` is pinned explicitly because the default integer width
    differs between platforms, and a key built on a Linux CI runner must address the same slot
    as one built on a developer's machine.

    :param query_vec: The query embedding.
    :param tenant_id: The requesting tenant - a key component.
    :param epoch: The tenant's current cache epoch from :func:`get_epoch`.
    :returns: The Redis key.
    """
    quantised = np.round(query_vec * QUANTISATION_FACTOR).astype(np.int32).tobytes()
    digest = hashlib.sha256(quantised).hexdigest()[:HASH_PREFIX_LENGTH]
    return _SEMANTIC_KEY_TEMPLATE.format(tenant_id=tenant_id, epoch=epoch, digest=digest)


def get_epoch(r: redis.Redis, tenant_id: str) -> int:
    """Read a tenant's current cache epoch.

    :param r: A Redis client.
    :param tenant_id: The tenant.
    :returns: The epoch, or 0 when the counter has never been set. Zero rather than an error:
        a tenant that has never had an ingest run has a perfectly valid empty cache, and
        raising here would make the first request of every new tenant fail.
    """
    raw = r.get(_EPOCH_KEY_TEMPLATE.format(tenant_id=tenant_id))
    if raw is None:
        return 0
    # Redis returns bytes; int() accepts both bytes and str, so this covers a client configured
    # with decode_responses either way.
    return int(raw)


def bump_epoch(r: redis.Redis, tenant_id: str) -> int:
    """Invalidate a tenant's entire semantic cache in one atomic write.

    Called from the Airflow ingest DAG's final task, per tenant, once the new chunks are
    committed. See the module docstring for why this beats scanning and deleting.

    :param r: A Redis client.
    :param tenant_id: The tenant whose cache to invalidate.
    :returns: The new epoch. ``INCR`` on a missing key initialises it to 1, so the first bump
        moves a tenant from epoch 0 to epoch 1 without a separate initialisation step.
    """
    new_epoch = int(r.incr(_EPOCH_KEY_TEMPLATE.format(tenant_id=tenant_id)))
    _LOG.info(
        "cache.epoch.bumped",
        extra={"event": "cache.epoch.bumped", "tenant_id": tenant_id, "epoch": new_epoch},
    )
    return new_epoch


@traceable(run_type="chain", name="taxcalc_ai.cache_lookup")
def cache_lookup(
    r: redis.Redis,
    query_vec: NDArray[np.float32],
    tenant_id: str,
) -> dict[str, object] | None:
    """Look up a cached answer, treating a tenant mismatch in any citation as a miss.

    :param r: A Redis client.
    :param query_vec: The query embedding.
    :param tenant_id: The requesting tenant.
    :returns: The cached answer, or ``None`` on a miss. A stored answer whose citations do not
        all belong to ``tenant_id`` is reported as a miss rather than as an error: the request
        then proceeds to a real retrieval and gets a correct answer, while the log line records
        that the check fired. Raising would turn a defence into an outage, and the defence is
        supposed to be unreachable.
    """
    epoch = get_epoch(r, tenant_id)
    raw = r.get(_bucket_key(query_vec, tenant_id, epoch))
    if raw is None:
        return None

    answer: dict[str, object] = json.loads(raw)

    # Defence in depth. Redundant while the key is right, which is the point - see the module
    # docstring. `isinstance` rather than a bare index because the value came out of Redis and
    # may have been written by an older version of this code.
    citations = answer.get(CITATIONS_FIELD, [])
    if isinstance(citations, list):
        for citation in citations:
            if isinstance(citation, dict) and citation.get(CITATION_TENANT_FIELD) != tenant_id:
                _LOG.warning(
                    "cache.tenant_mismatch",
                    extra={
                        "event": "cache.tenant_mismatch",
                        "requesting_tenant": tenant_id,
                        "citation_tenant": citation.get(CITATION_TENANT_FIELD),
                        "epoch": epoch,
                    },
                )
                return None

    _LOG.info(
        "cache.hit",
        extra={"event": "cache.hit", "tenant_id": tenant_id, "epoch": epoch},
    )
    return answer


def cache_store(
    r: redis.Redis,
    query_vec: NDArray[np.float32],
    tenant_id: str,
    answer: dict[str, object],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Store an answer under this tenant's current epoch with a TTL.

    The TTL is not an optimisation, it is the backstop for the epoch: if an ingest lands and
    nobody bumps the epoch - a DAG task that failed after the upsert, a manual load - the stale
    entries still age out rather than being served forever.

    :param r: A Redis client.
    :param query_vec: The query embedding the answer was produced for.
    :param tenant_id: The owning tenant.
    :param answer: The answer payload. Its ``citations`` must carry ``tenant_id``, because
        :func:`cache_lookup` refuses to serve it otherwise - a mismatch written here becomes a
        permanent miss rather than a leak.
    :param ttl_seconds: Expiry, in seconds.
    """
    epoch = get_epoch(r, tenant_id)
    r.set(
        _bucket_key(query_vec, tenant_id, epoch),
        json.dumps(answer),
        ex=ttl_seconds,
    )
    _LOG.info(
        "cache.stored",
        extra={
            "event": "cache.stored",
            "tenant_id": tenant_id,
            "epoch": epoch,
            "ttl_seconds": ttl_seconds,
        },
    )
