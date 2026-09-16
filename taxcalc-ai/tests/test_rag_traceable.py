# taxcalc-ai/tests/test_rag_traceable.py
"""Tests for the traced retrieval surface.

Two separate things are checked here, and conflating them is the mistake this file is arranged
to avoid:

*Is the function wired for tracing?* - answered locally, by introspection, below.
*Did a trace actually reach LangSmith?* - **not** answerable locally, and deliberately not
attempted here. Every realistic way tracing breaks (``LANGSMITH_TRACING`` unset, a key scoped to
the wrong workspace, a process that exits before the background uploader flushes) leaves the
decorator exactly where it was, so no local assertion can see it. That check lives in
``taxcalc_ai/scripts/assert_langsmith_run_visible.py`` and runs SaaS-side in CI.

What *is* worth testing locally is the retrieval itself: the tenant filter, the model-version
filter, and the ordering. Those are correctness and isolation properties of the SQL, and they
run against the same real pgvector container the loader tests use.
"""

from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

import taxcalc_ai.rag as rag_module
from taxcalc_ai.corpus import EMBEDDING_DIM, MODEL_NAME, CorpusRow
from taxcalc_ai.pgvector_loader import load_rows
from taxcalc_ai.rag import RETRIEVER_RUN_NAME, retrieve_chunks

from .conftest import SUITE_LANGSMITH_API_KEY


def _traceable_config(func: object) -> dict[str, object]:
    """Read the ``@traceable`` settings back off the decorated function.

    LangSmith keeps ``run_type`` and ``name`` in the wrapper's closure rather than on a public
    attribute, so this reaches into ``__closure__``. That is version-coupled, and it is written
    as one helper with this comment rather than inlined three times so the coupling has exactly
    one place to break. The durable assertion is ``__langsmith_traceable__``; this one buys the
    extra detail that the run type is ``retriever`` and not the default ``chain``, which is what
    makes the run show up as a retrieval in the LangSmith UI.
    """
    code = getattr(func, "__code__", None)
    closure = getattr(func, "__closure__", None)
    assert code is not None and closure is not None, "not a decorated closure"
    cells = dict(zip(code.co_freevars, (cell.cell_contents for cell in closure), strict=True))
    container = cells.get("container_input")
    assert isinstance(container, dict), f"unexpected traceable internals: {list(cells)}"
    return container


def _unit_vector(seed: int) -> NDArray[np.float32]:
    """A deterministic unit-length ``float32`` vector standing in for an embedding."""
    vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vec[seed % EMBEDDING_DIM] = 1.0
    return vec


def test_retrieve_chunks_is_traceable_as_a_retriever() -> None:
    """The retrieval is decorated, and decorated as a ``retriever`` rather than the default.

    ``run_type`` is not cosmetic: LangSmith renders retriever runs with their documents and
    scores broken out, and W7 D5's trace-driven debugging reads that structure. A run recorded
    as a generic ``chain`` carries the same data in a shape nothing downstream knows how to read.
    """
    assert getattr(retrieve_chunks, "__langsmith_traceable__", False) is True
    # inspect.unwrap follows __wrapped__; the decorator's return type is a Protocol that does
    # not declare that attribute, so reaching it by name is an attr-defined error under --strict.
    assert inspect.unwrap(retrieve_chunks) is not retrieve_chunks

    config = _traceable_config(retrieve_chunks)

    assert config["run_type"] == "retriever"
    assert config["name"] == RETRIEVER_RUN_NAME == "taxcalc_ai.retrieve_chunks"


def test_importing_rag_without_a_langsmith_key_fails_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing credential kills the import, not the first retrieval.

    Reloading the module is what makes this testable: the check runs at import, and the module
    is already imported by the time any test runs. The reload is undone in a ``finally`` so a
    failure here cannot leave a half-initialised module behind for the rest of the session.
    """
    monkeypatch.delenv(rag_module.LANGSMITH_API_KEY_ENV, raising=False)

    try:
        with pytest.raises(RuntimeError, match=rag_module.LANGSMITH_API_KEY_ENV):
            importlib.reload(rag_module)
    finally:
        monkeypatch.setenv(rag_module.LANGSMITH_API_KEY_ENV, SUITE_LANGSMITH_API_KEY)
        importlib.reload(rag_module)


def test_the_api_key_is_never_a_parameter_or_a_default() -> None:
    """No function in the retrieval module accepts a credential as an argument.

    A key that can be passed in is a key that ends up in a call site, a partial, a test fixture
    and eventually a traceback. Reading it from the environment only is what keeps
    the repo-wide secret-prefix grep meaningful as a gate.
    """
    signature = inspect.signature(inspect.unwrap(retrieve_chunks))

    names = [p.lower() for p in signature.parameters]
    assert not any("key" in n or "secret" in n or "token" in n for n in names), names
    # Assembled from parts, for the same reason the check in
    # test_retrieval_reads_its_credential_from_the_environment_only is: this file must not
    # itself become a hit for the secret sweep whose usefulness it exists to defend.
    key_prefix = "lsv" + "2_"
    assert all(
        param.default is inspect.Parameter.empty or key_prefix not in str(param.default)
        for param in signature.parameters.values()
    )


def test_retrieval_returns_the_nearest_chunks_for_the_right_tenant(pg_dsn: str) -> None:
    """The happy path: results are tenant-scoped, correctly shaped, and ordered by distance."""
    tenant = "tenant-rag-happy"
    rows = [
        CorpusRow(
            doc_id=f"{tenant}-doc-{i:03d}",
            chunk_idx=0,
            chunk_text=f"tax topic number {i}",
            embedding=_unit_vector(i),
            model_version=MODEL_NAME,
            tenant_id=tenant,
        )
        for i in range(10)
    ]
    load_rows(pg_dsn, rows)

    results = retrieve_chunks(pg_dsn, "what is the standard deduction", k=4, tenant_id=tenant)

    assert len(results) == 4
    for hit in results:
        assert set(hit) == {"doc_id", "chunk_idx", "chunk_text", "distance"}
        assert str(hit["doc_id"]).startswith(tenant)
        assert isinstance(hit["distance"], float)
    distances = [float(str(hit["distance"])) for hit in results]
    assert distances == sorted(distances), "results must come back nearest-first"


def test_retrieval_never_crosses_a_tenant_boundary(pg_dsn: str) -> None:
    """A search scoped to one tenant cannot return another tenant's chunks.

    This is the security property, and it is enforced by the ``WHERE tenant_id = %s`` clause
    alone - an HNSW index is an approximate-nearest-neighbour structure over the vector column
    and cannot enforce it. The other tenant's rows here are deliberately *nearer* to the query
    than this tenant's, so a dropped filter fails the test rather than passing by luck.
    """
    near, far = "tenant-rag-near", "tenant-rag-far"
    # `far` owns the vectors closest to seed 0; `near` owns distant ones.
    load_rows(
        pg_dsn,
        [
            CorpusRow(
                f"{far}-doc-{i:03d}", 0, f"other tenant {i}", _unit_vector(0), MODEL_NAME, far
            )
            for i in range(3)
        ],
    )
    load_rows(
        pg_dsn,
        [
            CorpusRow(
                f"{near}-doc-{i:03d}", 0, f"own tenant {i}", _unit_vector(200 + i), MODEL_NAME, near
            )
            for i in range(3)
        ],
    )

    results = retrieve_chunks(pg_dsn, "standard deduction", k=10, tenant_id=near)

    assert results, "the tenant does have matching chunks"
    assert all(str(hit["doc_id"]).startswith(near) for hit in results), results


def test_retrieval_does_not_rank_across_model_versions(pg_dsn: str) -> None:
    """Vectors from another model are excluded, not ranked alongside.

    Two models' vectors occupy the same 384-dimensional space without meaning the same thing, so
    ranking them together returns confident nonsense. A corpus mid-way through a re-embedding
    holds both, which is exactly when this filter earns its place.
    """
    tenant = "tenant-rag-models"
    load_rows(
        pg_dsn,
        [
            CorpusRow(
                f"{tenant}-doc-000", 0, "current model chunk", _unit_vector(5), MODEL_NAME, tenant
            )
        ],
    )
    load_rows(
        pg_dsn,
        [
            CorpusRow(
                f"{tenant}-doc-001",
                0,
                "other model chunk",
                _unit_vector(5),
                "all-MiniLM-L12-v2",
                tenant,
            )
        ],
    )

    results = retrieve_chunks(pg_dsn, "standard deduction", k=10, tenant_id=tenant)

    texts = [hit["chunk_text"] for hit in results]
    assert texts == ["current model chunk"], texts


def test_retrieval_reads_its_credential_from_the_environment_only() -> None:
    """The module's credential check looks at ``os.environ`` and nowhere else."""
    assert rag_module.LANGSMITH_API_KEY_ENV in os.environ

    source = Path(str(rag_module.__file__)).read_text()

    # No literal key, and no reading the credential from anywhere but the process environment.
    # Assembled from parts on purpose: a test that contains the literal prefix would itself
    # be a hit for the repo-wide `grep -RIn` secret scan the CI gate runs, so the check and
    # the gate would contradict each other.
    langsmith_key_prefix = "ls" + "v2_pt_"
    assert langsmith_key_prefix not in source
    assert "os.environ" in source
