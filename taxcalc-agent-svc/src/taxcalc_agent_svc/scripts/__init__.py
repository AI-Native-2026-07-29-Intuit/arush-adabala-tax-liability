# taxcalc-agent-svc/src/taxcalc_agent_svc/scripts/__init__.py
"""Operator and CI entrypoints.

These are the surfaces that report a verdict on stdout and exit with a status code - the eval
gate and the checkpoint smoke. They are packaged (rather than left in a top-level ``scripts/``)
so ``uv run python -m taxcalc_agent_svc.scripts.<name>`` works from an installed wheel as well as
from a checkout, which is what lets CI and an on-call operator run the same command.
"""
