# taxcalc-ai/tests/test_cli.py
"""CLI tests - exercised by calling ``main`` directly rather than shelling out.

``main`` takes its argument vector as a parameter precisely so these tests need no subprocess:
a subprocess would double the runtime, hide the traceback on failure, and test the shell as
much as the code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taxcalc_ai.cli import main
from taxcalc_ai.models import LiabilityEstimateRequest


def test_valid_request_is_echoed_as_pydantic_json(
    tmp_path: Path, estimate_request: LiabilityEstimateRequest, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid payload exits 0 and prints the re-serialised camelCase JSON."""
    path = tmp_path / "request.json"
    path.write_text(estimate_request.model_dump_json(by_alias=True))

    assert main(["taxcalc-ai", str(path)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["correlationId"] == "corr-w7d1-0001"
    assert printed["taxpayer"]["displayName"] == "Ada Lovelace"


def test_invalid_request_exits_one_and_names_the_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad payload fails loudly, with the ValidationError text on stderr."""
    path = tmp_path / "bad.json"
    path.write_text('{"correlationId": "nope", "taxpayer": {}}')

    assert main(["taxcalc-ai", str(path)]) == 1
    assert "correlation_id" in capsys.readouterr().err


def test_missing_file_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unreadable path is a run failure, not a usage error."""
    assert main(["taxcalc-ai", str(tmp_path / "absent.json")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_wrong_argument_count_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    """Shell convention: 2 for a usage error."""
    assert main(["taxcalc-ai"]) == 2
    assert "usage:" in capsys.readouterr().err
