# taxcalc-ai/tests/test_ci_skip_annotations.py
"""The conftest hook that stops a skipped gate from reading as a passed one.

This is a small amount of code guarding a large failure mode, which is why it is tested rather
than eyeballed. The state it exists for - a green step whose test never ran - is invisible by
construction, so a bug in the reporting of it would also be invisible.
"""

from __future__ import annotations

import pytest

from .conftest import _escape_annotation, _skip_reason


def _skipped_report(reason: str) -> pytest.TestReport:
    """A report shaped the way pytest shapes one for a skip.

    Built directly rather than by running a throwaway test through ``pytester``: the only thing
    under test is how the triple in ``longrepr`` is read, and a real sub-run would add a plugin
    dependency and a second pytest process to assert one string.
    """
    return pytest.TestReport(
        nodeid="tests/test_x.py::test_y",
        location=("tests/test_x.py", 12, "test_y"),
        keywords={},
        outcome="skipped",
        longrepr=("tests/test_x.py", 12, f"Skipped: {reason}"),
        when="setup",
    )


def test_the_skip_reason_is_read_without_pytests_own_prefix() -> None:
    """``longrepr``'s third element carries pytest's framing; the annotation supplies its own."""
    assert _skip_reason(_skipped_report("evaluator unavailable")) == "evaluator unavailable"


def test_a_longrepr_that_is_not_a_triple_still_yields_something_printable() -> None:
    """Skips raised outside the usual path can carry a bare string, and must not crash the hook.

    A reporting hook that raised would take down the whole session at teardown, turning a
    skipped test into an infrastructure failure - the opposite of what this code is for.
    """
    report = _skipped_report("unused")
    report.longrepr = "Skipped: something else"
    assert _skip_reason(report) == "something else"


def test_newlines_are_encoded_so_the_annotation_cannot_be_truncated() -> None:
    """Actions parses workflow commands per line: an unescaped break loses the rest of the text.

    The percent sign is asserted alongside, because encoding it after the newline substitution
    would re-encode the ``%`` that substitution just introduced.
    """
    assert _escape_annotation("a\nb") == "a%0Ab"
    assert _escape_annotation("a\r\nb") == "a%0D%0Ab"
    assert _escape_annotation("100% of rows\nfailed") == "100%25 of rows%0Afailed"
