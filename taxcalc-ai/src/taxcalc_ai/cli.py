# taxcalc-ai/src/taxcalc_ai/cli.py
"""Command-line entrypoint - the one module in this package allowed to ``print()``.

``ruff``'s T20 rule bans ``print()`` everywhere else, and ``pyproject.toml`` grants this file
the only per-file exemption. The rule is not stylistic: a library that prints writes to a
stream its host did not choose, bypassing log levels, structured fields and log routing. A CLI
*is* the host, so here stdout is the interface.

Usage::

    uv run python -m taxcalc_ai.cli path/to/request.json

The file is validated as a :class:`~taxcalc_ai.models.LiabilityEstimateRequest`; on success the
re-serialised Pydantic JSON goes to stdout, which makes the CLI a boundary-contract checker you
can point at any candidate payload.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from .models import LiabilityEstimateRequest

#: Shell convention: 2 for a usage error, 1 for a run that ran but failed.
_EXIT_USAGE: int = 2
_EXIT_INVALID: int = 1


def main(argv: list[str] | None = None) -> int:
    """Validate the JSON file named by ``argv[1]`` and print it back.

    :param argv: argument vector, defaulting to :data:`sys.argv`. Injectable so tests can call
        ``main`` directly instead of shelling out.
    :return: the process exit code.
    """
    args = sys.argv if argv is None else argv
    if len(args) != 2:
        print(f"usage: {Path(args[0]).name} <request.json>", file=sys.stderr)
        return _EXIT_USAGE

    path = Path(args[1])
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return _EXIT_INVALID

    try:
        request = LiabilityEstimateRequest.model_validate_json(raw)
    except ValidationError as exc:
        # The ValidationError text is the useful part - it names the failing field and why.
        print(f"invalid LiabilityEstimateRequest in {path}:\n{exc}", file=sys.stderr)
        return _EXIT_INVALID

    print(request.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
