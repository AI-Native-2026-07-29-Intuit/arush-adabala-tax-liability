# taxcalc-ai/src/taxcalc_ai/eval/__init__.py
"""Evaluation harness: runs the RAG pipeline under each flag configuration and scores it.

Production code rather than a test, for one reason: a test asserts a threshold, and what this
does is *measure* - it runs the same pipeline six ways and reports six sets of numbers. Living
in ``src/`` means it is type-checked under ``--strict``, linted, and importable from a notebook
or a scheduled job, which a file under ``tests/`` is none of.
"""
