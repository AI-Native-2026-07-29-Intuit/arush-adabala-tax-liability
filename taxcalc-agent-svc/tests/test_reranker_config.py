# taxcalc-agent-svc/tests/test_reranker_config.py
"""``RERANKER`` reaches the sidecar, and a bad value never reaches a request.

The bug these tests were written against: this service exported its reranker choice into
``TAXCALC_AI_RERANKER``, the W7 D3 pipeline read nothing of the kind, and selecting Cohere changed
an environment variable and no behaviour at all. Nothing failed, so nothing was noticed. Half the
fix lives in the sidecar (``taxcalc-ai/tests/test_reranker_selection.py`` gates the dispatch); this
file gates the half that lives here - the NAME the two sides agree on, and the validation that
keeps a typo or a missing credential from becoming a per-request failure three repositories away.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr, ValidationError

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.deps import BUDGET_GUARD_KEY
from taxcalc_agent_svc.nodes import retrieval as retrieval_mod
from taxcalc_agent_svc.nodes.retrieval import RERANKER_ENV
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState


def _sidecar_constant(name: str) -> Any:
    """Read one module-level constant out of ``taxcalc_ai.rerank`` without importing it.

    ``find_spec`` resolves the submodule's file while importing only the (trivial) parent package,
    and ``ast`` reads the assignment. Measured, which is why it is done this way: actually importing
    ``taxcalc_ai.rerank`` pulls in ``sentence_transformers`` and costs ~7 seconds, which is longer
    than this entire suite, to compare one string. The comparison is still exact - this is not a
    substring search over source text.

    :param name: The constant's name.
    :returns: Its literal value.
    """
    spec = importlib.util.find_spec("taxcalc_ai.rerank")
    assert spec is not None and spec.origin is not None
    tree = ast.parse(pathlib.Path(spec.origin).read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in taxcalc_ai.rerank")


# ------------------------------------------------------------------ the shared name


def test_this_service_exports_the_variable_the_sidecar_reads() -> None:
    """The one assertion that would have caught the dead write.

    Two processes, one decision, two copies of the name - which is fine as long as something checks
    they are the same string. Before this, they were ``TAXCALC_AI_RERANKER`` here and nothing at
    all there.
    """
    assert RERANKER_ENV == _sidecar_constant("RERANKER_ENV") == "RERANKER"


def test_the_accepted_values_are_the_sidecars_own_tokens() -> None:
    """A value this service considers valid must be one the sidecar can resolve.

    Otherwise boot validation passes and the request-path ``ValueError`` fires anyway, which is the
    worst of both designs.
    """
    accepted = {_sidecar_constant("RERANKER_BGE"), _sidecar_constant("RERANKER_COHERE")}

    assert accepted == {"bge", "cohere"}
    for value in accepted:
        assert Settings(reranker=value, cohere_api_key=SecretStr("co-test-key")).reranker == value


# ------------------------------------------------------------------ boot validation


def test_a_misspelled_reranker_fails_at_boot_naming_the_field() -> None:
    """A ``Literal``, not a ``str``.

    The sidecar raises on an unknown name too - but in the request path, once per request, inside a
    thread, three repositories from the mistake. A ``ValidationError`` at boot fails the readiness
    probe instead, which is a rollout that halts rather than a service that 500s.
    """
    with pytest.raises(ValidationError, match="reranker"):
        Settings(reranker="cohore")  # type: ignore[arg-type]  # the typo IS the test


def test_selecting_cohere_without_a_credential_fails_at_boot() -> None:
    """The cross-field check, which is the only place this can live.

    Neither field is wrong on its own: ``cohere`` is a valid reranker and an empty key is the right
    default for the local one. Together they are a pod that starts, passes readiness, and raises on
    the first question anyone asks.
    """
    with pytest.raises(ValidationError, match="COHERE_API_KEY"):
        Settings(reranker="cohere")


def test_the_local_reranker_needs_no_credential() -> None:
    """Constructing with nothing set must work - every schema test in this suite relies on it."""
    assert Settings().reranker == "bge"
    assert Settings().cohere_api_key.get_secret_value() == ""


@pytest.mark.parametrize("variable", ["RERANKER", "TAXCALC_AGENT_RERANKER"])
def test_both_the_shared_name_and_the_prefixed_one_are_read(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unprefixed name is the contract; the prefixed one is kept so a deployment already
    setting it is not broken by the rename.
    """
    monkeypatch.setenv(variable, "cohere")
    monkeypatch.setenv("COHERE_API_KEY", "co-test-key")

    assert Settings().reranker == "cohere"


# ------------------------------------------------------------------ the export


async def test_the_retrieval_node_exports_the_selected_reranker(
    monkeypatch: pytest.MonkeyPatch, guard: BudgetGuard
) -> None:
    """The node sets the variable, in the environment, before the pipeline runs on its thread.

    Asserted from INSIDE the substituted pipeline rather than after the node returns, because the
    ordering is the whole claim: a node that exported the value after calling the pipeline would
    leave this passing at the end of the request and wrong during it.
    """
    seen: list[str | None] = []

    def fake_pipeline(_query: str, _tenant: str, _top_k: int) -> dict[str, Any]:
        """Record what the environment said at the moment the pipeline was called.

        :returns: One citation, so the node has something to shape.
        """
        import os

        seen.append(os.environ.get(RERANKER_ENV))
        return {"citations": [{"chunk_id": "chunk-doc-1-p0", "score": 0.5}]}

    async def fake_rewrite(question: str, *_args: Any, **_kwargs: Any) -> str:
        """Skip the Claude call; the rewrite is not what this test is about.

        :returns: The question unchanged.
        """
        return question

    monkeypatch.setattr("taxcalc_agent_svc.retrievers.run_pipeline", fake_pipeline)
    monkeypatch.setattr(retrieval_mod, "rewrite_query", fake_rewrite)
    monkeypatch.delenv(RERANKER_ENV, raising=False)

    settings = Settings(
        reranker="cohere",
        cohere_api_key=SecretStr("co-test-key"),
        anthropic_api_key=SecretStr("sk-ant-test"),
    )
    config: RunnableConfig = {"configurable": {BUDGET_GUARD_KEY: guard}}
    state: AgentState = {"question": "what is the home office deduction rule", "tenant_id": "t-1"}
    result = await retrieval_mod._retrieval(state, config, settings)

    assert seen == ["cohere"]
    assert result["docs"] == [{"chunk_id": "chunk-doc-1-p0", "doc_id": "doc-1", "score": 0.5}]
