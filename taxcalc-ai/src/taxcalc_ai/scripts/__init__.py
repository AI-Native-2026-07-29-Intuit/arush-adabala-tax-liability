"""Operational scripts run as modules (``python -m taxcalc_ai.scripts.<name>``).

These are CI and operator entrypoints rather than library code: they exit with a status code and
are allowed to write to stdout, which is why ``T20`` is lifted for this package in
``pyproject.toml`` exactly as it is for :mod:`taxcalc_ai.cli`.
"""
